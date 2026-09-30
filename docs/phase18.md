# Phase 18: Aster core — CPU, L1/SRAM interface, and 100 MHz feasibility

Status: **in progress — milestone 18.0 (tooling) exit gate met; 18.1 next.** The CPU specification this
phase implements is [`cpu.md`](cpu.md) (approved 29 September 2026); the phase
sits in the [v2 plan](phase17-plus.md#6-phase-17-sequence). Every milestone
passes its verification layer and records its timing before the next starts.

## Goal

Design and verify Aster's own five-stage RV32IMA core (with Zicsr, Zifencei and
native Xasterdot8), together with the L1/SRAM interface that feeds it, so that it:

- retires the same architectural stream as an independent reference model
  (Spike) on conformance, directed, and constrained-random programs;
- takes at least **2× fewer cycles** than PicoRV32 on the CPU-bound set in the
  same memory shell: the geometric mean of the seven per-kernel speedups is at
  least 2.0×, and no kernel is below 1.5× ([`cpu.md`](cpu.md) §7);
- meets **10 ns** timing out-of-context on the PYNQ-Z1 and in SKY130 block-level
  STA, or records the limiting path and its cost.

## Verification architecture

### The CPU shell

`verification/core/` holds a Verilator shell that surrounds one CPU with a
synchronous SRAM and nothing else:

- one unified memory at `0x8000_0000` (a CPU-only test image; the SoC keeps its
  own map): 96 KiB by default, the v2 SRAM size, and 2 MiB for the
  `riscv-arch-test` programs (`+mem_bytes`);
- **memory timing:** the SRAM latches an address (and performs a write) at a
  clock edge and returns read data in the next cycle. PicoRV32 drives it from
  its look-ahead port (`mem_la_*`, one cycle ahead of `mem_valid`), so data is
  ready in the cycle `mem_valid` is high: PicoRV32's best case with a
  synchronous SRAM (the v1 SoC's `sync1` model adds a wait state). The Aster
  core's ports are shaped for the same SRAM ([`cpu.md`](cpu.md) §5), so the
  18.7 comparison runs both cores against identical memory timing, with
  PicoRV32 at its best;
- random back-pressure (`+stall_seed`: 0–3 wait states per access), and garbage
  on the read-data bus outside a response, so a core that samples it at the
  wrong time fails;
- a `tohost` word that ends the run when the store to it **retires** (seen in
  the RVFI stream, so the trace always ends with that store, also behind a
  future write-back cache);
- an RVFI trace writer: one line per retired instruction (order, PC,
  instruction, trap, destination register and value, memory address, masks and
  data), and a signature dump for `riscv-arch-test`.

The reset vector is a core parameter: `0x8000_0000` in the shell, the SoC's boot
address later. `0x8000_0000` also keeps the shell clear of Spike's debug module
and boot ROM at the bottom of the address space.

The first DUT in the shell is **PicoRV32**, through its RVFI port. That proves
the harness end to end before the Aster core exists, and gives the 18.7
comparison an identical-shell PicoRV32 baseline.

### Lockstep against Spike

Spike (`riscv-isa-sim`, built into `~/tools/spike` from a pinned commit) runs
the same ELF with `--log-commits`, bounded by the same instruction budget as
the shell's cycle limit. `scripts/lockstep.py` takes Spike's commits from the
ELF entry (after its boot ROM) up to and including its store to `tohost`, and
requires the DUT's RVFI trace to equal that stream exactly — record for record
and in length — comparing PC, instruction word, destination register write,
memory access (byte address, size, store data), and trap. While parsing the
trace it also rejects non-consecutive RVFI order numbers (a dropped or
duplicated record), byte masks that are not one naturally aligned access, byte
addresses that disagree with their mask, a value written to x0, and a record
that both reads and writes memory unless it is an AMO with equal masks. A test
passes only if the shell exits 0 reporting PASS with a retired count equal to
its trace's, Spike exits 0, lockstep passes, and the program's data region
(`begin_signature` to `end_signature`) is identical in the shell's memory and
Spike's at the end. Traces, logs and signatures are deleted before each run,
and a runner that finds no programs for a configured suite fails. The first
mismatch fails the run with both records and their context. Interrupt behavior is
asynchronous and is checked by self-checking directed tests instead of
lockstep. The extensions the comparison needs for traps and CSRs (18.3),
atomics (18.4), and Xasterdot8 (18.5) are specified in [`cpu.md`](cpu.md) §6.

The harness is trusted only after it **rejects deliberately corrupted runs**:
`make core-lockstep-selftest` edits the raw trace text of a real run (PC,
instruction, trap, destination register and value, memory address, store byte
lane and mask shape, store data; dropped, duplicated, missing, extra, and
truncated records; a missing `tohost` store) and the raw Spike log (register
value, store data, memory address, PC), re-parses both, and requires every
corruption to be caught — 19 of 19.

### Programs

- `riscv-tests` (vendored): rv32ui, rv32um, rv32ua, plus the machine-mode tests
  that apply.
- `riscv-arch-test` 3.10.0 (vendored): I, M, A, Zifencei, and the privilege
  tests; each program runs in lockstep and its signature region must equal
  Spike's word for word. (Release 4.1.0, ACT4, builds self-checking programs
  from the Sail model; it is reconsidered at 18.3, when the core's
  configuration is final — see `vendor/riscv-arch-test/UPSTREAM.md`.)
- A seeded constrained-random program generator (`scripts/rvgen.py`, built in
  18.0 so that 18.1 starts with it): random register and data-region state;
  every RV32I computational, load, store, branch and jump instruction and every
  M instruction (not `fence`, `ecall` or `ebreak`); loads and stores of every
  width, including a load that overwrites its own base; forward branches;
  bounded loops closed by three kinds of backward branch (`bne`, `blt`, `bltu`); `jal`/`jalr` calls
  with varied `jalr` offsets and a set low bit, including `jalr x1, imm(x1)`;
  the `INT_MIN ÷ −1` case; and sources drawn mostly from the last three
  results, so forwarding and load-use cases are dense. Programs check nothing
  themselves; lockstep and the data-region signature check every retired
  instruction. Coverage is counted from the reference stream as
  read-after-write pairs — producer (ALU, load, link, multiply, divide) ×
  distance 1–3 × consumer operand (ALU, multiply/divide, branch, store data,
  load and store address, `jalr` target) — and a run fails if any required
  bin is missed (`make core-random-lockstep`: 20 programs plain and 20 under
  back-pressure). Over 100 seeds the thinnest bin averages 1.7 hits per
  program and no window of five programs misses a bin. This coverage is
  architectural (distance in the retired stream); from 18.1 the core also
  reports which forwarding and stall events it actually took.
- Directed microarchitecture tests (from milestone 18.1).
- The CPU-bound benchmark set for 18.7.

### Timing and area

Committed scripts produce, for any core top:

- a Vivado out-of-context synthesis and implementation at 10 ns on
  `xc7z020clg400-1` (timing summary and utilization);
- a SKY130 block synthesis and static timing analysis at 10 ns at the declared
  corners (cell area and per-corner slack).

The same scripts run on PicoRV32 first, to record its baseline
(`make timing-fpga-picorv32`, `make timing-asic-picorv32`; the timed top is
`verification/core/timing_picorv32.sv`, PicoRV32 with the v1 core's parameters
except IRQ and PCPI, which are off, and only its memory port). Only
register-to-register paths set the implied period: an out-of-context block's
port budgets are arbitrary.

### PicoRV32 baseline timing (18.0)

| Target (10 ns) | Worst reg-to-reg setup slack | Implied period / Fmax | Hold | Area |
| --- | --- | --- | --- | --- |
| Vivado 2025.1, `xc7z020clg400-1`, OOC, routed | +3.194 ns | 6.81 ns / 146.9 MHz | +0.093 ns | 1,478 LUTs, 1,054 FFs, 0 BRAM, 0 DSP |
| SKY130 post-route, `nom_tt_025C_1v80` | +2.804 ns | 7.20 ns / 139.0 MHz | +0.418 ns | 132,348 µm² std cells (14,673 cells including 4,059 taps; 2,076 flops), 46% utilization |
| SKY130 post-route, `nom_ss_100C_1v60` | −4.186 ns | 14.19 ns / 70.5 MHz | +0.853 ns | 744 max-slew and 81 max-cap violations |
| SKY130 post-route, `max_ss_100C_1v60` (slow signoff) | −4.325 ns | 14.32 ns / 69.8 MHz | +0.855 ns | 1,194 max-slew and 125 max-cap violations |
| SKY130 post-route, `max_ff_n40C_1v95` | +5.523 ns | 4.48 ns / 223.4 MHz | +0.265 ns | no violations |

Evidence, retained with checksums and a host test:
[`results/phase18/picorv32-baseline`](results/phase18/picorv32-baseline/README.md)
(copied from the git-ignored `build/timing/fpga/picorv32/` and LibreLane run
`asic/sky130/runs/p18-picorv32`); route DRC 0.

**What it means for the Aster core.** The FPGA and the SKY130 typical corner
have margin; the SKY130 slow corner misses 10 ns by 43% (70 MHz). That figure
is partly the flow's: LibreLane ran its area-oriented default
(`SYNTH_STRATEGY "AREA 0"`), enforced timing only at the typical corners, and
mapped the slow-corner critical path — the divider's operand negation — as a
long OR-gate ripple chain (see the retained README). The Aster core has more
logic per cycle (single-cycle ALU and branch resolution, forwarding, the
data-port kill of [`cpu.md`](cpu.md) §4), so the **SKY130 slow-corner 100 MHz
target is the principal Phase 18 risk**. Before the 18.1 timing report: choose
the synthesis strategy and corner enforcement, re-run this baseline with them,
and time the core-to-SRAM ports (below). The 18.1 report then decides early
whether the slow corner can close at 10 ns, and if not records the limiting
path, the cycles-versus-period trade, and the proposal, as the frozen target
requires (a 50 MHz result is an intermediate milestone, not a substitute).

## Milestones and gates

| Milestone | Content | Exit gate |
| --- | --- | --- |
| **18.0** | Spike; CPU shell with PicoRV32 and RVFI trace; lockstep comparator; `riscv-tests` in the shell; `riscv-arch-test` harness; timing scripts; PicoRV32 baseline timing | PicoRV32 passes lockstep on `riscv-tests`; the comparator catches an injected mismatch in every compared field; timing scripts report PicoRV32 on both targets — **met** (checklist below) |
| 18.1 | RV32I pipeline | `riscv-tests` rv32ui and arch-test I in lockstep; random programs in lockstep; first timing report |
| 18.2 | M extension | um/arch-test M, multiply/divide corner cases, lockstep, timing |
| 18.3 | Zicsr, traps, interrupts, counters | arch-test Zicsr; directed traps in every stage; interrupt tests in every pipeline state |
| 18.4 | A extension, `fence`, `fence.i` | ua/arch-test A and Zifencei; atomic and self-modifying-code tests |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests on the core |
| 18.6 | L1 caches with single-cycle hits; SRAM interface; runtime port | cache reference model, back-pressure, firmware regression |
| 18.7 | Evaluation and feasibility | Against PicoRV32 on the CPU set in the same shell: geometric mean of the per-kernel speedups ≥2.0× and every kernel ≥1.5×, each kernel's speedup published; 100 MHz feasibility report for FPGA and SKY130 |

## Checklist

- [x] 18.0 Spike built and pinned (`0bff1212`); recorded in `docs/toolchain.md`;
  `make tools` checks Spike and `dtc`
- [x] 18.0 CPU shell (`verification/core/`) + RVFI trace writer with PicoRV32 as the
  first DUT (v1 core parameters without IRQ and PCPI; a 96 KiB synchronous SRAM
  at `0x8000_0000`, zero-wait through PicoRV32's look-ahead port)
- [x] 18.0 `scripts/lockstep.py` comparator with host tests; `make
  core-lockstep-selftest` rejects 19/19 corruptions of a real run's trace and
  Spike log (above)
- [x] 18.0 `riscv-tests` rv32ui and rv32um (vendored at the pinned revision, byte
  for byte equal to upstream) pass in lockstep on PicoRV32: 48/48, 15,288
  retired instructions in 63,848 cycles (`make core-riscv-tests`), and 48/48
  again under random back-pressure with seeds 1–3 (`make
  core-riscv-tests-stall`; 86,190–89,743 cycles, identical retired streams).
  Skipped with reasons: `fence_i` (no Zifencei) and `ma_data` (misaligned
  accesses trap by design). PicoRV32 reports full-word RVFI read masks on
  sub-word loads, so its loads are compared by word address; the Aster core
  must report exact byte masks. The A-extension tests (rv32ua) run from
  milestone 18.4, as PicoRV32 has no native A. A failing test that never
  reaches its first test case can no longer report a pass (the environment
  spins, as upstream does, and the run times out).
- [x] 18.0 `riscv-arch-test` 3.10.0 vendored (`vendor/riscv-arch-test`, checksummed);
  I and M run on PicoRV32 in lockstep **and** by signature: 47/47 programs,
  114,163 retired instructions compared (`make core-arch-tests`)
- [x] 18.0 Vivado out-of-context and SKY130 block timing scripts; PicoRV32 baseline
  recorded (table above)
- [x] 18.1 prerequisite: constrained-random generator and hazard coverage; on
  PicoRV32, 20/20 programs in lockstep plain and 20/20 under back-pressure,
  84/84 required hazard bins each (about 154,000 instructions)
- [x] 18.0 review findings closed: harness pass-on-prefix, wrong-lane stores,
  TESTNUM=0 false pass, parallel-make race, memory-timing claim, stale-trace
  false pass (traces, logs and signatures are deleted before each run, the
  shell fails if it cannot write them, and its retired count must equal its
  trace's), timing evidence retained; every program's data region is also
  compared with Spike's by signature; lockstep extensions for 18.3–18.5
  specified in [`cpu.md`](cpu.md) §6; data-port timing versus the commit point,
  and `d_rsp_error` timing, specified in §4
- [ ] **Before the 18.1 RTL timing report:** synthesis strategy and corner
  enforcement chosen and the PicoRV32 baseline re-run with them; core-to-SRAM
  port paths timed (the SRAM model inside the timed top, or declared port
  budgets with in-to-reg and reg-to-out reports)
- [ ] **Aster-core shell (18.1):** separate instruction and data ports with
  independent back-pressure; each retired store checked against the bus write
  the shell observed; exact RVFI byte masks (no `word_loads`); from 18.6, the
  signature read through the cache hierarchy rather than the backing array
- [x] **Decided 29 September 2026 — how the 18.7 performance gate aggregates.**
  Speedup per kernel = PicoRV32 cycles ÷ Aster-core cycles over the same
  window. The gate passes only if the **geometric mean** of the seven
  per-kernel speedups (CoreMark CRC run, Dhrystone, sort/search, FFT, strided,
  scalar Conv2D, scalar reduction) is **≥ 2.0×** and **no kernel is below
  1.5×**; every per-kernel speedup is published with the aggregate. Declared
  before any measurement ([`cpu.md`](cpu.md) §7).
- [x] **Decided by the owner, 29 September 2026 — SRAM timing plan for 18.6.**
  The SKY130 macros' only liberty models are analytical and typical-corner
  ([`phase17-memory.md`](phase17-memory.md)), so no macro sits on the 10 ns
  single-cycle path:
  1. the L1 instruction and data arrays (a few KiB each, single-cycle hits) are
     standard-cell latch or flip-flop arrays, timed by the foundry cell
     libraries at every corner including `max_ss`; a standalone 2–4 KiB array
     block is timed early (18.1–18.2), as the PicoRV32 baseline was, to size
     the L1 from measurement;
  2. the 96 KiB OpenRAM backing store sits behind the L1 miss path with
     registered inputs and outputs and a fixed multi-cycle access, so its
     falling-edge launch and slow-corner delay fall in a 15–20 ns window
     instead of half a cycle; the miss cost is measured in 18.6/18.7;
  3. the one macro type used is characterized with OpenRAM's SPICE
     characterizer against the PDK models at `ss`/1.60 V/100 °C (and at `tt`, to
     check the shipped model), run in the background during 18.1–18.5;
  4. derating the typical-corner model is used only as a labelled
     cross-check, never as the basis.
- [ ] 18.1 … 18.7 as in the table above

## Non-goals

Compressed instructions, supervisor/user modes, an MMU, floating point, dynamic
branch prediction before it is measured to pay, and multi-issue execution
(see [`cpu.md`](cpu.md) §1). SoC integration of the new core is Phase 20.
