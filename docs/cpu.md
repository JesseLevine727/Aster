# Aster core — CPU specification (Phase 18)

Status: **approved on 29 September 2026 (P17-E) — the Phase 18 contract.** This
is the specification of the CPU that replaces PicoRV32 in v2. It is written before any RTL so that every milestone has
a fixed target, interface, verification method, and timing/area gate. The
surrounding plan is [`phase17-plus.md`](phase17-plus.md#6-phase-17-sequence).

## 1. Goals

- A single-issue, in-order, five-stage RV32IMA core designed in this project.
- At least **2× fewer cycles** than the v1 PicoRV32 baseline on the fixed
  CPU-bound set, at the same clock and memory configuration (about 1.2–1.5 CPI
  with single-cycle memory). PicoRV32's measured CPI on that set, in the
  retained Phase 17 baseline with the physical `sync1` memory, is 5.2–12.3:
  5.7 for the reduction and 10.0 for scalar Conv2D on the coherent SoC; 5.2
  sort/search, 5.8 strided, 6.4 Dhrystone, 6.8 CoreMark, 8.3 Conv2D and 12.3
  FFT on the minimal top (multiply-heavy code pays PicoRV32's serial
  multiplier).
- **10 ns** block timing out-of-context on the PYNQ-Z1 (`xc7z020clg400-1`) and in
  SKY130 block-level STA at the declared corners, from the first milestone on.
- Verified instruction by instruction against an independent reference model
  before any performance claim.

Non-goals for the first version: compressed instructions, supervisor/user modes,
an MMU, floating point, multiple issue, out-of-order execution, and caches with
more than one outstanding miss. Each can be revisited later with measurements.

## 2. ISA

| Extension | Content | Notes |
| --- | --- | --- |
| RV32I | Base integer ISA | `fence` orders memory; `ecall`/`ebreak` trap |
| M | `mul`, `mulh`, `mulhsu`, `mulhu`, `div`, `divu`, `rem`, `remu` | pipelined multiplier, iterative divider |
| A | `lr.w`, `sc.w`, `amoswap/add/and/or/xor/min/max/minu/maxu.w` | executed by the memory side as one atomic transaction (§5) |
| Zicsr | CSR read/write/set/clear, register and immediate forms | machine-mode CSRs in §3 |
| Zifencei | `fence.i` | flushes the fetch path and instruction cache |
| Xasterdot8 | `dot8 rd, rs1, rs2`: `rd = Σᵢ sext(rs1.bᵢ) × sext(rs2.bᵢ)` | custom-0, `opcode=0x0b`, `funct3=0`, `funct7=0` — the v1 encoding, so v1 kernels run unchanged |

Unimplemented or reserved encodings raise an illegal-instruction exception.
Misaligned loads and stores raise address-misaligned exceptions (as v1's
`CATCH_MISALIGN` did); the software ABI does not rely on misaligned access.
Toolchain flags: `-march=rv32ima_zicsr_zifencei -mabi=ilp32`, with Xasterdot8
emitted through `.insn` as today.

## 3. Privilege, CSRs, traps, and interrupts

Machine mode only. Implemented CSRs:

| CSR | Behavior |
| --- | --- |
| `misa`, `mvendorid`, `marchid`, `mimpid`, `mhartid` | read-only identity; `mhartid` from a parameter |
| `mstatus` | `MIE`, `MPIE`; `MPP` reads as machine mode |
| `mtvec` | direct mode (vectored mode optional later) |
| `mepc`, `mcause`, `mtval`, `mscratch` | standard |
| `mie`, `mip` | `MEIE`/`MEIP`, `MTIE`/`MTIP`, `MSIE`/`MSIP` |
| `mcycle(h)`, `minstret(h)`, `cycle(h)`, `instret(h)` | 64-bit counters; user-level aliases read-only |

Traps are precise: an exception is recognized at the stage that detects it and
taken when the instruction reaches the commit point, after which all younger
instructions are flushed. `mret` restores `MIE` from `MPIE` and resumes at
`mepc`. Interrupts are taken between instructions at the commit point.

Interrupt wiring on the Aster SoC: the existing interrupt controller at
`0x2000_4000` drives `MEIP` for its hart (sources: timer, DMA, NPU, software);
the handler reads the controller's `PENDING` register. `MTIP` and `MSIP` inputs
exist on the core but are tied off in the first SoC integration, because the
Aster timer is a custom MMIO block routed through the controller. This replaces
PicoRV32's custom IRQ instructions and fixed vector, so `start.S`,
`start_multicore.S`, and the handlers are ported to standard `mtvec`/`mret`.

## 4. Microarchitecture

Five stages: **F**etch, **D**ecode, **E**xecute, **M**emory, **W**rite-back.

- **Fetch.** The PC register addresses the instruction port; with a
  single-cycle synchronous memory the instruction arrives when the instruction
  enters Decode. Sequential fetch continues every cycle unless stalled or
  redirected.
- **Decode.** Decode, immediate generation, a two-read/one-write register file
  that forwards a same-cycle write to its read ports, hazard detection, and
  jump-target computation for `jal`.
- **Execute.** ALU, branch compare and target, CSR read/modify, load/store
  address generation, the first stage of the multiplier, and Xasterdot8's
  first stage.
- **Memory.** Data-port response, atomic operations, the second
  multiplier/DOT8 stage, and exception/interrupt commit (the commit point is
  the end of Memory). The data request itself is presented from Execute and
  accepted at the Execute→Memory edge; see *Data-port timing and precise
  traps* below.
- **Write-back.** Register write and retirement (RVFI output).

Hazards and penalties:

| Case | Handling | Cost |
| --- | --- | ---: |
| ALU result → next instruction | forward from E/M and M/W to Execute | 0 |
| Load → dependent next instruction | stall one cycle, then forward | 1 cycle |
| `mul` → dependent instruction | pipelined over E and M, forwarded from W | up to 2 cycles |
| `div`/`rem` | iterative (radix-2), stalls the pipeline | ≈33 cycles |
| `jal` | redirect from Decode | 1 cycle |
| Conditional branch | static prediction: backward taken, forward not taken, predicted in Decode; resolved in Execute | 1 cycle if predicted taken; 2 cycles if mispredicted |
| `jalr` | resolved in Execute | 2 cycles |
| Memory back-pressure | the stage waiting on a port stalls the pipeline behind it | as the memory returns |

Dynamic branch prediction is added only if measurement on the CPU set shows it
pays for its area and timing.

**Data-port timing and precise traps.** A load, store, or atomic presents its
request while in Execute (address from the Execute adder) and the memory
accepts it at the Execute→Memory edge; with a synchronous SRAM the response —
load data, AMO old value, `sc` result, or `d_rsp_error` — arrives while the
instruction is in Memory. That is what makes the load-use penalty one cycle.
The request is not presented in a cycle in which the instruction in Memory
traps or an interrupt is taken (the commit logic kills it), so when any access
is accepted every older instruction has passed the commit point: no younger
store, atomic, or side-effecting I/O load reaches memory ahead of an older
trap. Bus errors are recognized in Memory, before commit, and are precise. The
kill is a short combinational path from the Memory-stage trap decision to
`d_req_valid`; its timing is part of every milestone report. `d_rsp_error`
must come from registered state on the memory side — for example an address
decode made when the request was accepted and returned as a flag with the
response — never from an SRAM macro's data output, whose falling-edge launch
leaves only half a cycle. Misaligned addresses are detected in Execute and
never reach the port.

## 5. Interfaces

All ports use valid/ready handshakes, hold their request stable until accepted,
and carry at most one outstanding request in the first version (a parameter
reserves room for more).

**Instruction port:** `i_req_valid`, `i_req_addr[31:2]`, `i_req_ready`;
`i_rsp_valid`, `i_rsp_data[31:0]`, `i_rsp_error`.

**Data port:** `d_req_valid`, `d_req_op` (load, store, `lr`, `sc`, or one of the
AMO operations), `d_req_addr[31:0]`, `d_req_wdata[31:0]`, `d_req_be[3:0]`,
`d_req_ready`; `d_rsp_valid`, `d_rsp_rdata[31:0]` (load data, AMO old value, or
`sc` success), `d_rsp_error`. Atomic operations are executed by the memory side
as one indivisible transaction, as the v1 atomic fabric does today, so the
coherence protocol can later own them.

**Other signals:** `clk`, `rst_n`, `meip`, `mtip`, `msip`, a hart-id parameter, a
reset-vector parameter, and an RVFI retirement port with the riscv-formal
fields: `valid`, `order`, `insn`, `trap`, `halt`, `intr`, `mode`, `ixl`,
`rs1_addr`/`rs2_addr` and their read data, `rd_addr`/`rd_wdata`,
`pc_rdata`/`pc_wdata`, and `mem_addr` (byte address) with **exact** byte masks
`mem_rmask`/`mem_wmask` and `mem_rdata`/`mem_wdata`; from milestone 18.3 also the
`rvfi_csr_*` read/write masks and data for `mstatus`, `mie`, `mip`, `mtvec`,
`mscratch`, `mepc`, `mcause`, and `mtval`. (PicoRV32 reports full-word read
masks on sub-word loads; the Aster core must report exact masks.)

The ports are shaped for synchronous SRAM (address presented in one cycle, data
returned the next), which matches FPGA block RAM and a registered SKY130 macro
interface. On SKY130, a macro read must fit the half-cycle budget described in
[`phase17-memory.md`](phase17-memory.md).

## 6. Verification

Each milestone passes all applicable layers before the next milestone starts.

1. **Reference model.** Spike (`riscv-isa-sim`), built into `~/tools`, is the
   golden model. The Verilator shell writes the RVFI retirement stream to a
   trace file, and an offline comparator (`scripts/lockstep.py`) checks it
   against Spike's commit log instruction by instruction (PC, instruction,
   destination write, memory access, trap) up to and including the `tohost`
   store. The harness must detect a deliberately injected mismatch in every
   field before it is trusted. Extensions of the comparison, each due by the
   milestone that needs it:
   - **Traps (18.3):** `--log-commits` writes no record for a trapping
     instruction; Spike's `-l` exception lines (`exception …, epc …`, `tval …`)
     become trap records, and trap injection joins the self-test.
   - **CSRs and Spike configuration (18.3):** CSR writes are compared through the
     RVFI CSR fields; Spike runs with `--priv=m`, `--pmpregions=0`, and
     `--isa=rv32ima_zicsr_zifencei_zicntr`. Values that legitimately differ are
     allowlisted by name, never by position: `mcycle`/`cycle`/`time` reads,
     `marchid`, and Spike's debug-trigger `tcontrol` update on `mret`; the
     environment zeroes `minstret`, since Spike's boot ROM retires five
     instructions first. Lockstep programs do not let an allowlisted value
     reach later results; counter behavior is checked by self-checking tests.
   - **Atomics (18.4):** both memory records of an AMO are parsed (done in
     18.0); a failed `sc` has no memory record.
   - **Xasterdot8 (18.5):** Spike does not know custom-0; an Aster extension
     library (`--extlib`) implements DOT8 in Spike, so DOT8 programs run in
     lockstep rather than being excluded.
   - **I/O and interrupts:** the core shell has no devices; interrupts are
     checked by self-checking directed tests (layer 3), and device-dependent
     values in the SoC by the firmware oracles (layer 5).
2. **Conformance.** The vendored `riscv-tests` (rv32ui, rv32um, rv32ua) and
   `riscv-arch-test` 3.10.0 (I, M, A, Zifencei, and the privilege tests for
   Zicsr and traps), compared by signature with Spike as well as in lockstep.
3. **Directed microarchitecture tests.** Forwarding from every stage, load-use,
   multiply-use, branch/jump flush in both prediction directions, `fence.i`
   self-modifying code, CSR read-modify-write ordering, every exception class
   raised in every stage it can occur, `mret`, and interrupts arriving in every
   pipeline state — all with randomized port back-pressure.
4. **Constrained-random programs.** A seeded generator produces instruction
   streams (arithmetic, loads/stores within a data region, bounded loops and
   forward branches, M and A operations, CSR accesses) that run under lockstep
   with injected memory stalls. Coverage is reported as instruction class ×
   hazard type.
5. **System regression.** The existing firmware and workload suites, with their
   independent oracles, on the new core.
6. **Formal (stretch).** riscv-formal through the RVFI port (needs Yosys and
   SymbiYosys).

## 7. Timing, area, and performance gates

- **Timing:** from milestone 18.1 on, an out-of-context Vivado implementation at
  10 ns on `xc7z020clg400-1`, and a SKY130 block-level synthesis plus STA at
  10 ns at the declared corners, both as committed scripts. A milestone that
  misses timing records the limiting path and the proposed fix before it
  advances.
- **Area:** reported per milestone (LUT/FF/DSP on the FPGA; cell area on
  SKY130).
- **Performance:** the CPU set — the fixed-iteration CoreMark CRC run,
  Dhrystone (adapted), sort/search, FFT, strided, scalar Conv2D, and scalar
  reduction — runs on the new core and on PicoRV32 in the same memory shell,
  with the same compiler flags and measurement windows. The gate is at least
  2× fewer cycles. A valid CoreMark score (real timer, at least ten seconds) is
  produced on the FPGA in Phase 21.

## 8. Milestones

| Milestone | Content | Exit |
| --- | --- | --- |
| 18.0 | Spike, lockstep harness, `riscv-arch-test`, timing scripts | Harness catches an injected mismatch |
| 18.1 | RV32I pipeline on single-cycle memory | Conformance, random lockstep, first timing report |
| 18.2 | M extension | Corner-case M tests, lockstep, timing |
| 18.3 | Zicsr, traps, interrupts, counters | Trap/interrupt tests in every pipeline state |
| 18.4 | A extension, `fence`, `fence.i` | Atomic and litmus tests on the core |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests |
| 18.6 | L1 instruction/data caches with single-cycle hits; SRAM interface; runtime port | Cache reference model, stalls, firmware regression |
| 18.7 | Evaluation | CPU set vs PicoRV32 (≥2×); 100 MHz feasibility report for FPGA and SKY130 |

## 9. Approval

Approved as drafted on 29 September 2026: the ISA and machine-mode-only scope
with standard traps and the interrupt controller on `MEIP` (§2–3), the pipeline
and hazard policy with static branch prediction first (§4), and Spike as the
golden model with the verification layers of §6. Changes after approval are
recorded here with the evidence that motivated them.

Clarifications during milestone 18.0 (29 September 2026), from the Phase 17
and 18.0 reviews; none changes the approved scope:

- §1: PicoRV32's CPI range restated from the retained baseline (the earlier
  5.4–5.7 held for the reduction workload only).
- §4: data-port timing relative to the commit point, so that precise traps and
  the one-cycle load-use penalty hold together; `d_rsp_error` from registered
  state.
- §5: the RVFI field list made explicit (riscv-formal fields, exact byte masks,
  CSR fields from 18.3).
- §6: the lockstep comparator is an offline script over a trace file rather
  than a C++ testbench; the trap, CSR, AMO, and Xasterdot8 extensions are
  scheduled; the arch-test suite is pinned at 3.10.0.
