# Aster core — CPU specification (Phase 18)

Status: **approved on 29 September 2026 (P17-E) — the Phase 18 contract.**
*Revised 30 September 2026 (owner decision): a two-stage memory access, so a
seven-stage pipeline (F1 F2 D E M1 M2 W) in §4 and pipelined ports in §5;
the revision was approved by the owner the same day.*

This is the specification of the CPU that replaces PicoRV32 in v2. It is written
before any RTL so that every milestone has a fixed target, interface,
verification method, and timing/area gate. The surrounding plan is [`phase17-plus.md`](phase17-plus.md#6-phase-17-sequence).

## 1. Goals

- A single-issue, in-order, seven-stage RV32IMA core designed in this project
  (five stages until the 30 September 2026 revision; §4).
- At least **2× fewer cycles** than the v1 PicoRV32 baseline on the fixed
  CPU-bound set, at the same clock and memory configuration, judged by the
  aggregate rule in §7 (the geometric mean of the per-kernel speedups at least
  2×, and no kernel below 1.5×); an estimated 1.2–1.65 CPI with the seven-stage
  pipeline of §4 (the trace-driven model of §4, which assumes no memory
  stalls), to be measured in 18.7.
  PicoRV32's measured CPI on that set, in the retained Phase 17 baseline with
  the physical `sync1` memory, is 5.2–12.3:
  5.7 for the reduction and 10.0 for scalar Conv2D on the coherent SoC; 5.2
  sort/search, 5.8 strided, 6.4 Dhrystone, 6.7 CoreMark and 12.3 FFT on the
  minimal top (multiply-heavy code pays PicoRV32's serial multiplier); the
  minimal top's own Conv2D, 8.3, is a cross-check outside the gate (§7).
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

Seven stages (revised 30 September 2026, owner decision): **F1** and **F2**
(instruction fetch, one memory stage each), **D**ecode, **E**xecute, **M1** and
**M2** (data access, one memory stage each), **W**rite-back. Every instruction
fetch and data access spans two pipeline stages because a single-cycle read of
512 B and 2 KiB standard-cell arrays did not fit 10 ns at the SKY130 slow corner
in the pre-18.1 runs ([`phase18.md`](phase18.md), "Pre-18.1 measurements"); the
memory behind each port is itself split into two register-to-register stages.

- **F1 / F2.** The next-PC logic presents a fetch request each cycle; the
  memory accepts it at the edge where that instruction enters F1, runs its
  two stages during F1 and F2, and the instruction word is registered at the
  end of F2. Sequential fetch continues every cycle, so up to two fetches are
  in flight. A **three-entry** instruction buffer (fetch latency plus one)
  absorbs returning words while Decode is stalled. The fetch unit presents a
  new sequential request only if the buffer's occupied entries plus the
  fetches in flight plus this request fit in three — a decision made from
  registered state only, with no combinational path from the memory's data or
  from Decode's stall back to the request; with three entries this sustains
  one fetch per cycle and never overflows (two entries would allow two fetches
  every three cycles). A presented request not yet accepted counts as in
  flight, so the room rule never withdraws it. A redirect presents its target
  regardless of the room rule: it flushes the buffer, and fetches already in
  flight are marked discarded, returning into no entry.
- **D.** Decode, immediate generation, a two-read/one-write register file that
  forwards a same-cycle write to its read ports, hazard detection, and the
  target computation for `jal` and for backward branches, which are predicted
  taken.
- **E.** ALU, branch compare and target, `jalr` target, CSR read/modify,
  load/store address generation, the data request (below), the first
  multiplier and Xasterdot8 stage, and the iterative divider. A misprediction
  or `jalr` resolves at the edge where it leaves E: at that edge the compare
  result clears the valid bits entering E and D and flushes the instruction
  buffer, so no wrong-path instruction ever issues a data request, CSR write,
  redirect, or divider start; only the presentation of the target is
  registered, to the next cycle. The redirect fires once, even if the
  instruction waits in E for several cycles.
- **M1.** The data access's first memory stage; the second multiplier/DOT8
  stage; exception and interrupt commit — **the commit point is the end of
  M1**. Every trap source (illegal instruction, misalignment, `ecall`,
  `ebreak`, a fetch error carried with the instruction, a data-port error)
  is known by then.
- **M2.** The data access's second memory stage (load data, AMO old value, or
  `sc` result registered at its end) and the third multiplier stage.
  Instructions in M2 and W have committed and always retire.
- **W.** Load alignment and sign extension, register write, and retirement
  (RVFI output, registered).

Hazards and penalties:

| Case | Handling | Cost |
| --- | --- | ---: |
| ALU or link result → dependent instruction | forward from M1, M2, and W to Execute | 0 |
| Load → dependent at distance 1 / 2 | stall, then forward from W | 2 / 1 cycles |
| AMO, `lr`, `sc` result → dependent | as a load | 2 / 1 cycles |
| Store whose data comes from a load at distance 1 / 2 | as a load-use (store data is presented from E) | 2 / 1 cycles |
| `mul` → dependent | pipelined over E, M1, M2; forwarded from W | up to 2 cycles |
| Xasterdot8 → dependent | pipelined over E and M1; forwarded from M2 and W | up to 1 cycle |
| `div`/`rem` | iterative (radix-2) in E, stalls the pipeline | ≈33 cycles |
| `jal`; conditional branch predicted taken (backward) | redirect from Decode, target presented the same cycle | 2 cycles |
| Conditional branch mispredicted; `jalr` | resolved in Execute; the redirect is registered | 4 cycles |
| Data-port back-pressure or an answer later than two cycles | the stage waiting on the port stalls the pipeline behind it | as the memory returns |
| Instruction-port back-pressure or a late answer | bubbles enter Decode | as the memory returns |

The Execute redirect is registered so that the branch compare does not drive
the instruction memory's address in the same cycle; if 18.1's timing shows the
direct path fits at `max_ss`, it becomes 3 cycles. Redirect priority, highest
first: a trap or interrupt at the commit point, the registered Execute
redirect, a Decode redirect. An interrupt is taken only in a cycle in which M1
holds a valid instruction, before the instruction after it; `mepc` receives
that M1 instruction's next PC (its `pc_wdata`, the redirect target if it
redirected). The trace-driven model of these rules over the CPU kernels'
measurement windows (`scripts/cpi_model.py`, 30 September 2026; an estimate
that assumes no memory stalls, not a simulation; refined in 18.1 to the
RTL's fetch timing, which it matches cycle for cycle on RV32I programs when the
memory answers on time)
gives 1.20–1.65 CPI — 1.57
CoreMark, 1.59 Dhrystone, 1.65 sort/search, 1.20 FFT, 1.51 strided, 1.40
scalar Conv2D on the coherent SoC (the gate's), 1.39 reduction, and 1.57 for
the minimal top's Conv2D (a cross-check outside the gate) — against
PicoRV32's 4.08–11.14 in the same zero-wait shell, a projected
geometric-mean speedup over the seven gate kernels of about 3.7× (3.683) with the lowest kernel
(sort/search) at about 2.6× ([`phase18.md`](phase18.md)). 18.7 measures the
real core.
A next-line or branch-target predictor in F1 is the first candidate if 18.7
shows branch cost matters; dynamic prediction is added only if measurement
shows it pays for its area and timing.

**Data-port timing and precise traps.** A load, store, or atomic presents its
request while in Execute (address from the Execute adder); the memory accepts
it at the Execute→M1 edge, reports an error for it during M1 (the cycle after
acceptance), and returns its data by the end of M2. Execute presents a
request only in a cycle in which M1 can take a new instruction, and not in a
cycle in which the instruction in M1 traps or an interrupt is taken (the
commit logic kills it). So when any access is accepted every older
instruction has passed the commit point: no younger store, atomic, or
side-effecting I/O load reaches memory ahead of an older trap. A presented
data request that the memory has not accepted (back-pressure) stays stable,
except that it is withdrawn in a cycle in which the pipeline flushes; the
memory side must not act on a request it has not accepted. Bus errors are
therefore recognized in M1, before commit, and are precise. The kill is a
short combinational path from the M1 trap decision (including `d_rsp_error`)
to `d_req_valid`. "M1 can take a new instruction" depends on M2 advancing,
which depends on `d_rsp_valid` in that cycle, so a second path runs from
`d_rsp_valid` through the pipeline stall to `d_req_valid`; both are named in
every milestone's timing report, and the 18.6 L1 must decide hit or miss in
its first stage so that `d_rsp_valid` is known early in M2. An
instruction whose access the memory has accepted is never killed by an
interrupt: interrupts are taken between instructions at the commit point, so
the instruction in M1 completes and the interrupt is taken before the next one
(a store or I/O load is never repeated after `mret`). `d_rsp_error` must come
from registered state on the memory side — the address decode made when the
request was accepted — never from an SRAM's data output; only such errors can
be precise with the commit point at M1, so a later memory level (the Phase 20
fabric, an L2) must report its errors the same way or as an imprecise
interrupt. The core samples `d_rsp_error` in the cycle after acceptance and
holds it if M1 stalls. Misaligned addresses are detected in Execute and never
reach the port. Instruction fetches are speculative (wrong-path fetches are
discarded), so the memory side answers a fetch from an I/O region with
`i_rsp_error` without touching the device; it traps as an instruction access
fault at the commit point.

## 5. Interfaces

All ports use valid/ready handshakes. A request presented and not yet
accepted stays stable, with two exceptions: a data request is withdrawn on a
pipeline flush (§4), and an instruction request is withdrawn or replaced on a
redirect. The memory must not act on a request it has not accepted.

**Timing (pipelined).** A request accepted at a clock edge is answered at the
earliest one and normally two cycles later — the memory is built as two
register-to-register stages. Every accepted request, including a store and an
errored request, gets exactly one response, and responses return in
acceptance order. Accesses also **take effect** in acceptance order: a
later-accepted access observes every earlier-accepted write on the same port
(including an AMO's), whatever stage the memory writes in. The memory may
accept a request every cycle and holds `*_req_ready` low when it cannot take
more. On the data port the core never has more than two requests in flight
(M1 and M2 hold them). The instruction port may present a third fetch (the
fetch buffer's room rule, or a redirect), so there the memory's
`i_req_ready` enforces its own limit; two in flight suffice for one fetch per
cycle. The core is built for two-cycle answers: a one-cycle answer (as from the
CPU shell's SRAM) is held until its stage, and a longer one (a cache miss)
stalls the pipeline. `d_rsp_error` is meaningful only in the cycle after an
acceptance, where it reports that request's error whatever its data latency
(§4); `i_rsp_error` travels with the instruction word and traps at the commit
point.

**Instruction port:** `i_req_valid`, `i_req_addr[31:2]`, `i_req_ready`;
`i_rsp_valid`, `i_rsp_data[31:0]`, `i_rsp_error`.

**Data port:** `d_req_valid`, `d_req_op` (load, store, `lr`, `sc`, or one of the
AMO operations), `d_req_addr[31:0]`, `d_req_wdata[31:0]`, `d_req_be[3:0]`,
`d_req_ready`; `d_rsp_valid`, `d_rsp_rdata[31:0]` (load data, AMO old value, or
`sc` success); `d_rsp_error` (timed as above). Atomic operations are executed
by the memory side as one indivisible transaction, as the v1 atomic fabric does
today, so the coherence protocol can later own them.

**Other signals:** `clk`, `rst_n`, `meip`, `mtip`, `msip`, a hart-id parameter, a
reset-vector parameter, and an RVFI retirement port with the riscv-formal
fields: `valid`, `order`, `insn`, `trap`, `halt`, `intr`, `mode`, `ixl`,
`rs1_addr`/`rs2_addr` and their read data, `rd_addr`/`rd_wdata`,
`pc_rdata`/`pc_wdata`, and `mem_addr` with **exact** byte masks
`mem_rmask`/`mem_wmask` and `mem_rdata`/`mem_wdata` in riscv-formal's aligned
layout (`RISCV_FORMAL_ALIGNED_MEM`): mask bit *i* and data byte *i* are byte *i*
of the aligned 32-bit word containing the access, and `mem_addr` is that
word's address (the lockstep comparator also accepts the byte address of the
access's lowest byte, but riscv-formal checks the word address); from
milestone 18.3 also the `rvfi_csr_*` read/write masks and data for `mstatus`,
`mie`, `mip`, `mtvec`, `mscratch`, `mepc`, `mcause`, and `mtval`. RVFI outputs
are registered. (PicoRV32 reports full-word read masks on sub-word loads; the
Aster core must report exact masks.)

On the FPGA the memory behind each port is block RAM with its output
register enabled (a two-cycle read); on SKY130 it is the L1's standard-cell
arrays, each split into two stages. No SKY130 SRAM macro is on a
register-to-register path shorter than its slow-corner access (to be
characterized in 18.6): the macros form the backing store behind the L1 miss path, with registered inputs
and outputs and a fixed multi-cycle access (the owner-approved 18.6 SRAM
timing plan in [`phase18.md`](phase18.md)). That replaces the earlier
requirement that a macro read fit half a cycle
([`phase17-memory.md`](phase17-memory.md)).

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
   - **Port protocol (from 18.1):** the two-port shell checks the core's side
     of §4–§5 every cycle — a waiting fetch stays stable except in a cycle
     whose request is a redirect's target or in which fetching has stopped
     (the core marks it, `chk_i_redirect`), a waiting data request always
     stays stable, data byte enables are well formed, at most two data
     requests are in flight, and the RVFI outputs are registered — and each
     check is proven on a deliberately broken DUT ([`phase18.md`](phase18.md)).
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
  Dhrystone (adapted), sort/search, FFT, strided, scalar Conv2D (the coherent
  SoC's scalar-engine build, `conv2d_scalar_coh`; owner decision, 30 September
  2026), and scalar reduction — runs on the new core and on PicoRV32 in the same memory shell,
  with the same compiler flags and measurement windows. A valid CoreMark score
  (real timer, at least ten seconds) is produced on the FPGA in Phase 21.
- **Performance gate (declared 29 September 2026, before any measurement).**
  For each of the seven kernels, the speedup is PicoRV32's cycles divided by
  the Aster core's cycles over the same measurement window. The gate passes
  only if both hold:
  1. the **geometric mean** of the seven per-kernel speedups is **at least
     2.0×** (every kernel counts equally, whatever its length, and one large
     win cannot carry the rest); and
  2. **no kernel is below 1.5×** (so the aggregate cannot hide a kernel the
     core handles badly).

  Every per-kernel speedup is published next to the aggregate.

  *Measurement conditions (proposed during the 18.0 review; confirmed by the
  owner on 30 September 2026, before any measurement):* the thresholds apply
  to unrounded ratios. Both cores run in the same CPU shell against the same synchronous
  SRAM, one cycle from request to data: PicoRV32 through its look-ahead port
  (its best case) and the Aster core directly on its ports, without its L1
  caches, so the gate measures the cores alone (the L1 and SoC effects are
  measured separately, in 18.6 and Phase 20). The SRAM serves one instruction
  fetch and one data access in the same cycle — separate instruction and data
  banks, as the Option B memory decision intends — which the Aster core's two
  ports use and single-ported PicoRV32 cannot. The Aster core's two-stage
  memory access gains nothing from a one-cycle SRAM, so this comparison is
  conservative for the Aster core. (Procedure: the two-port shell runs with
  `+latency=1`; its default is the two-cycle memory the core is built for.)

  The kernel inputs and sizes are those of the
  [retained Phase 17 baseline](results/phase17/baseline-56067a15815a/README.md),
  and the measurement windows are the kernels' own; changing any of them after
  measurement starts voids the comparison.

## 8. Milestones

| Milestone | Content | Exit |
| --- | --- | --- |
| 18.0 | Spike, lockstep harness, `riscv-arch-test`, timing scripts | Harness catches an injected mismatch |
| 18.1 | RV32I seven-stage pipeline on the shell's memory | Conformance, random lockstep, first timing report |
| 18.2 | M extension | Corner-case M tests, lockstep, timing |
| 18.3 | Zicsr, traps, interrupts, counters | Trap/interrupt tests in every pipeline state |
| 18.4 | A extension, `fence`, `fence.i` | Atomic and litmus tests on the core |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests |
| 18.6 | L1 instruction/data caches with two-stage pipelined hits; SRAM interface; runtime port | Cache reference model, stalls, firmware regression |
| 18.7 | Evaluation | CPU set vs PicoRV32 (§7 gate: geometric mean ≥2×, every kernel ≥1.5×); 100 MHz feasibility report for FPGA and SKY130 |

## 9. Approval

Approved as drafted on 29 September 2026: the ISA and machine-mode-only scope
with standard traps and the interrupt controller on `MEIP` (§2–3), the pipeline
and hazard policy with static branch prediction first (§4), and Spike as the
golden model with the verification layers of §6. Changes after approval are
recorded here with the evidence that motivated them.

Clarifications during milestone 18.0 (29–30 September 2026), from the Phase 17
and 18.0 reviews; none changes the approved scope:

- §1: PicoRV32's CPI range restated from the retained baseline (the earlier
  5.4–5.7 held for the reduction workload only).
- §4: data-port timing relative to the commit point; when a request may be
  presented or withdrawn; `d_rsp_error` from registered state; the timed
  request paths named; an accepted access is never killed by an interrupt.
  (Its "one-cycle load-use penalty" is superseded by the seven-stage revision
  below.)
- §5: the RVFI field list made explicit (riscv-formal fields, exact byte masks
  in riscv-formal's aligned layout, CSR fields from 18.3).
- §6: the lockstep comparator is an offline script over a trace file rather
  than a C++ testbench; the trap, CSR, AMO, and Xasterdot8 extensions are
  scheduled; the arch-test suite is pinned at 3.10.0.

Clarifications during milestone 18.1 (30 September 2026), from the 18.1
reviews; none changes the approved scope:

- §5: `d_req_addr` may be the access's byte address or its word address (low
  bits zero); the memory uses the word address and `d_req_be`, which is an
  aligned byte, halfword, or word. The Aster core presents the byte address.
  Every memory side (the 18.6 L1, the Phase 20 fabric) follows this.
- §5: an instruction request is replaced or withdrawn only in a cycle in
  which the fetch unit presents a redirect's target or has stopped fetching —
  for a Decode redirect its own cycle, for the registered Execute redirect the
  cycle after the compare resolves (the compare does not change the request
  in the cycle it resolves).
- §4–§5: a data request that waits is never withdrawn: Execute presents only
  when M1 can take, so after a request waits M1 is empty and no trap or
  interrupt is taken there; the permission to withdraw on a flush is never
  used, and the shell allows none.
- §4: "no wrong-path instruction ever issues … a redirect" means none takes
  effect: in the cycle an Execute misprediction resolves, a wrong-path `jal`
  in Decode may still present its target, and the fetch unit discards that
  fetch a cycle later, when it presents the Execute redirect's target.
- §4: the Execute redirect's flush is registered along with its target (for
  timing: the branch compare then drives no pipeline register enable). The
  instructions that entered Decode and Execute at the edge the redirect
  resolved are squashed: masked in the next cycle — the cycle the target is
  presented, in which they would otherwise have been — and gone after it; the
  buffer is flushed at the end of that cycle. Every side effect of Decode and
  Execute (data request, redirect, and from 18.2 on the divider start, CSR
  writes, `ecall`/`ebreak`, `fence.i`) is gated by the squash-masked valid
  bits, never the raw ones (the RTL asserts it). The observable behavior and
  every cycle count are unchanged.
- §4: a `jal` or a backward branch redirects from Decode in its first Decode
  cycle, whether or not it then waits there for operands; a branch whose
  target is its own fall-through (offset +4) never redirects, since its
  direction cannot change the next PC. With these, the core's cycle count on a
  memory that answers on time depends only on the instruction stream, and the
  CPI model reproduces it exactly.
- §4: the Execute redirect stays registered, 4 cycles: at `max_ss` even the
  paths into the registered redirect miss in the first 18.1 report (into
  `e_pc` −3.91 ns, into the squash −2.97 ns), so a direct compare-to-fetch
  path cannot fit there; on the FPGA the flush had to be registered as well
  (the first run missed by 3.19 ns). No build with a direct redirect was
  timed; the question reopens only if the remaining 18.1 timing work closes
  `max_ss` with margin.
- §4: a load's value is aligned and sign- or zero-extended as it leaves M2
  (the data is registered at the end of M2 already aligned) rather than in W,
  so W forwards and writes a value straight from a register; every cycle
  count is unchanged (a load's result is still forwarded from W). The 18.6
  L1's second stage must leave room for the alignment in M2.
- §5: a memory's `*_req_ready` never depends on its `*_req_valid` in the same
  cycle. (A load or store waits in Execute for `d_req_ready` even when it is
  misaligned and presents no request, so that the stall logic does not wait
  for the forwarded address's alignment.) The CPU shells and the 18.6 L1
  follow this.
- §5: the instruction memory may allow any number of fetches in flight; the
  fetch unit itself never has more than nine (three live by the room rule,
  the rest discarded ones), and its counters hold that.

Changes after approval, by the owner:

- **29 September 2026 — SRAM timing plan (§5):** SKY130 macros are off the
  single-cycle path ([`phase18.md`](phase18.md), 18.6 plan), replacing the
  half-cycle macro-read requirement.
- **29–30 September 2026 — performance gate (§1, §7, §8):** an aggregate — the
  geometric mean of the seven per-kernel speedups at least 2.0×, with no
  kernel below 1.5×, every per-kernel speedup published — declared before any
  measurement; the measurement conditions in §7 (unrounded ratios; both cores
  on the same one-cycle, dual-banked shell SRAM, the Aster core without its L1)
  were proposed in review and confirmed on 30 September.
- **30 September 2026 — two-stage memory access (§1, §4, §5, §7, §8):** every
  instruction fetch and data access spans two pipeline stages (a seven-stage
  core), because a single-cycle read of standard-cell arrays did not fit 10 ns
  at the SKY130 slow corner in the pre-18.1 runs — 512 B and 2 KiB with the
  standard cells, 512 B with the high-speed cells ([`phase18.md`](phase18.md)).
  §4: seven stages; a three-entry fetch buffer; the commit point at the end of
  M1, with data-port errors reported the cycle after acceptance; load-use 2
  cycles; Decode redirects 2 cycles, registered Execute redirects 4, with the
  wrong-path instructions in D and entering E killed; interrupts taken with a
  valid instruction in M1. §5: pipelined ports answering in one or (normally)
  two cycles, one response per request, accesses taking effect in acceptance
  order, at most two data requests in flight and the instruction memory
  limiting fetches with `i_req_ready`. §1's CPI estimate, §7's
  conservative-comparison note, and the 18.1/18.6 rows of §8 follow. The
  revised §4–§5 were approved by the owner on 30 September 2026.
- **30 September 2026 — the gate's Conv2D (§7):** "scalar Conv2D" is the
  coherent SoC's scalar-engine build (`conv2d_scalar_coh`, the capture whose
  PicoRV32 CPI §1 quotes, from the same SoC as the scalar reduction), not the
  minimal top's Conv2D, which stays in the shell runs as a cross-check
  outside the gate. Decided before any measurement of the core.
