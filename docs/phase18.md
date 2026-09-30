# Phase 18: Aster core — CPU, L1/SRAM interface, and 100 MHz feasibility

Status: **in progress — milestone 18.0 (tooling) exit gate met; 18.1 prerequisites
measured; the owner chose a two-stage memory access (a seven-stage core) and
approved the revised cpu.md §4–§5; the CPU kernels run in the shell and the
CPI model projects the 18.7 gate at about 3.6×; next, 18.1 RTL.** The CPU specification this
phase implements is [`cpu.md`](cpu.md) (approved 29 September 2026); the phase
sits in the [v2 plan](phase17-plus.md#6-phase-17-sequence). Every milestone
passes its verification layer and records its timing before the next starts.

## Goal

Design and verify Aster's own seven-stage RV32IMA core (with Zicsr, Zifencei and
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
  core runs in the two-port shell (`tb_core_ports.cpp`), whose memory answers
  in two cycles by default (the core's design, [`cpu.md`](cpu.md) §5) or one
  (`+latency=1`); the 18.7 comparison uses the one-cycle memory for both cores,
  PicoRV32 at its best and the Aster core gaining nothing from it;
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
- The CPU-bound benchmark set for 18.7 — the seven kernels of
  [`cpu.md`](cpu.md) §7, built with their SoC compile flags (read from the
  Makefile) against a shell platform (`verification/core/kernels/`: the SoC
  `aster.h` with a run-ending console hook, a start-up that seeds the SoC
  register-page constants the kernels check, and a 96 KiB layout). The SoC
  register page is plain memory in the shells (`+io_page`) and in Spike; its
  performance-counter page is a deterministic clock in both (each read of the
  cycle word adds 1,000,000; Spike plugin `verification/core/spike/aster_clock.cc`),
  so CoreMark's and Dhrystone's timers behave identically. Each kernel must
  pass lockstep and print a passing AsterBench record; the shells time its
  measurement window from the retirement of its window-opening store to that
  of its closing store (`make core-kernels`, in `make check`).

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

### PicoRV32 baseline timing (18.0, superseded by v2 below)

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

### Pre-18.1 measurements (30 September 2026)

**Corrected PicoRV32 baseline (v2).** The 18.0 SKY130 runs used a constraint file
without clock uncertainty, timing derate, a maximum-transition limit, or input
drive and output load, so their slacks were optimistic. With LibreLane's
default constraint set (`asic/sky130/constraints.core.sdc`) and the synthesis
strategy chosen from four candidates (`DELAY 1`: best slow-corner slack for
1.5% more area than the default), PicoRV32 at 10 ns gives, register to
register: **+3.97 ns at `nom_tt` (166 MHz); −1.97 ns at `max_ss` (83.5 MHz)**.
On the FPGA, the core alone has +3.19 ns (147 MHz); with the 96 KiB shell
memory as block RAM inside the timed block, +1.61 ns (119 MHz), limited by
the address routing to 32 block RAMs. Evidence and the strategy comparison:
[`results/phase18/picorv32-baseline-v2`](results/phase18/picorv32-baseline-v2/README.md).
Maximum-transition violations remain at every corner (4,754 at `max_ss`);
clean electrical signoff is a Phase 20 gate.

**L1 array probes: with LibreLane's default flow, a standard-cell array read
did not close 10 ns at the slow corner.** The approved 18.6 plan puts
standard-cell arrays on the single-cycle path. Standalone arrays with the
core's port timing (address registered at one edge, read word registered at
the next); the figures are implied periods (period − register-to-register
slack, so they include setup, uncertainty, derate, and skew):

| Array | `nom_tt` | `max_ss` | Std-cell area | Flow settings |
| --- | ---: | ---: | ---: | --- |
| 2 KiB, flip-flops, synthesized mux read | 8.37 ns | 17.66 ns | 1.14 mm² | `DELAY 1`, post-global-route timing and design repair, 50% utilization |
| 512 B, flip-flops, one-hot word lines + AND-OR read | 7.83 ns | 15.39 ns | 0.30 mm² | `DELAY 1`, post-global-route timing repair, 30% utilization |
| 2 KiB, one-hot word lines + AND-OR read | — | — | — | did not finish global routing (congestion) in over an hour at 30% utilization; stopped |

Retained runs (these and the strategy comparison):
[`results/phase18/pre18.1-flow-probes`](results/phase18/pre18.1-flow-probes/README.md).

The 512 B array's worst slow-corner path takes 14.8 ns from the clock edge to
the read-data register (clock-to-Q 1.1 ns; address fanout buffers 3.6 ns; a
7-to-128 decoder 4.8 ns; word-line fanout 3.3 ns; the select 2.0 ns), and about
7.7 ns of it is electrical-repair buffers, several of them weak `buf_1`/
`clkbuf_2` cells, with transitions above the 0.75 ns limit. **This is not yet
evidence about the technology:** the resizer believed the slow corner was met
— after global routing it reported +0.03 ns for the 512 B array and +0.04 ns
for PicoRV32 — while signoff extraction then found −5.39 ns and −1.97 ns. Its
wire estimates (tech-LEF RC; `LAYERS_RC` unset) are optimistic against the
signoff extraction rules, and LibreLane 3.0.14 has no timing repair after
detailed routing. For comparison, the OpenRAM 2 KiB macro occupies 0.285 mm²
(its timing model is not credible).

What is established: with LibreLane's defaults, neither the arrays nor
PicoRV32 (83.5 MHz) close 10 ns at `max_ss`, and the flow's own estimate does
not predict signoff.

**Flow correlation, first step: PicoRV32 within 6% of 100 MHz at `max_ss`.**
Placing and routing against a tighter setup target —
`constraints.core.pnr_margin.sdc`, the same constraints with 3 ns of extra
*setup* clock uncertainty — while signing off at the real 10 ns
(`constraints.core.sdc`) gives, register to register: **−0.583 ns at
`max_ss` (94.5 MHz)**, −0.087 ns at `nom_ss` (99.1 MHz), +4.62 ns at `nom_tt`
(186 MHz); worst hold +0.056 ns; standard-cell area 167,419 µm² (+5.2% over
v2); maximum-transition violations 410 at `nom_tt`, 2,848 at `max_ss`; route
DRC 0. (A first attempt that also applied the 3 ns to hold closed `max_ss` at
+0.451 ns, but with 20.7% more area, mostly needless hold buffers, and a
different placement; it is superseded and kept only as labelled evidence.)
The margin is empirical; calibrating the resizer's wire RC (`LAYERS_RC`)
against the signoff extraction is the principled next step, in 18.1's timing
work. PicoRV32 closing is necessary, not sufficient: the Aster core has more
logic per cycle.

**The array stays well short of a cycle.** The only clean array result is the
default-flow one above (15.39 ns implied period at `max_ss` for 512 B). With
the setup-only margin at 30% utilization, global routing failed on congestion
in the re-route after timing repair (which had upsized 1,913 cells and
inserted 158 buffers; the default-flow run at the same utilization routed); the attempt confounded by the hold margin
reached 13.05 ns at 25% utilization, with its resizer not converged
(−2.21 ns against its own target) and weak `buf_1` repair buffers (4.8 ns)
still on the path. So a single-cycle read of a 512 B or larger standard-cell
flip-flop array was not reached at `max_ss` in these runs; even a 3–4 ns
improvement leaves no room in the same cycle for a tag compare and the use of
the data. Latch arrays and banked arrays with a late select remain
unmeasured. The options were (the owner chose the two-stage memory access; see
the checklist):
- **flow:** correlate the resizer with signoff (over-constraint, calibrated
  `LAYERS_RC`), placement density and regions for the array;
- **array organization:** banks with a late select, latch arrays (allowed by
  the plan, not yet measured), structured placement;
- **microarchitecture:** a two-stage memory access (a seven-stage core:
  F1 F2 D E M1 M2 W), or a small single-cycle L0/line buffer in front of a
  two-cycle L1, and earlier index decode;
- **cells:** the SKY130 high-speed library (`sky130_fd_sc_hs`) — measured
  below: no help at the slow corner;
- **target:** a lower SKY130 slow-corner frequency with 100 MHz kept for the
  FPGA and the typical corner. This changes a frozen target, so it needs the
  evidence and trade-off recorded ([`phase17-plus.md`](phase17-plus.md)); a
  50 MHz result is an intermediate milestone, not a silent substitute for the
  100 MHz goal.

**High-speed cell library (owner-chosen trial, 30 September 2026).** The same
blocks on `sky130_fd_sc_hs`, same flow as v2 (default constraints, `DELAY 1`;
input drive and output load are each library's own PDK defaults, which only
affect port paths):

| Block | Library | `max_ss` slack / implied | `nom_tt` implied | Std-cell area |
| --- | --- | ---: | ---: | ---: |
| PicoRV32 | `hd` (v2) | −1.970 ns / 83.5 MHz | 166.0 MHz | 159,211 µm² |
| PicoRV32 | `hs` | −1.944 ns / 83.7 MHz | 149.8 MHz | 223,373 µm² (+40%) |
| 512 B array | `hd` | −5.391 ns / 15.39 ns | 7.83 ns | 298,610 µm² |
| 512 B array | `hs` | −4.251 ns / 14.25 ns | 7.78 ns | 384,604 µm² (+29%) |

At the slow corner the high-speed cells give PicoRV32 nothing and the array
read 7%, still more than 4 ns over a cycle, for 30–40% more area. The option
does not close the gap. (Register-to-register hold is met in all four runs;
the arrays' remaining hold violations are on port paths, −0.871 ns for `hd`
and −0.110 ns for `hs`.)
Evidence: `pre18.1-flow-probes` (`picorv32-hs`, `sram-512b-andor-hs`); the
library was added to the pinned PDK with `ciel fetch -l sky130_fd_sc_hs`.

**CPU kernels in the shell, and the CPI model (30 September 2026).** All seven
kernels pass on PicoRV32 in the look-ahead shell in lockstep with Spike, with
their own self-checks passing. Measurement windows (zero-wait memory, the 18.7
conditions) and the trace-driven model of the approved seven-stage pipeline
over the same windows (`scripts/cpi_model.py`; it assumes no memory stalls
and one fetch per cycle — an estimate, not RTL):

| Kernel | Window instructions | PicoRV32 CPI | Seven-stage model CPI | Projected speedup | Five-stage model CPI |
| --- | ---: | ---: | ---: | ---: | ---: |
| CoreMark (1 iteration) | 284,864 | 5.249 | 1.609 | 3.26× | 1.323 |
| Dhrystone | 492,302 | 4.237 | 1.596 | 2.65× | 1.324 |
| sort/search | 420,942 | 4.277 | 1.654 | 2.59× | 1.332 |
| FFT | 248,399 | 11.144 | 1.199 | 9.29× | 1.143 |
| strided | 1,559 | 4.151 | 1.508 | 2.75× | 1.172 |
| scalar Conv2D | 704,346 | 7.850 | 1.574 | 4.99× | 1.398 |
| scalar reduction | 53,299 | 4.076 | 1.385 | 2.94× | 1.154 |

Projected against the 18.7 gate: a geometric mean of about **3.6×**, lowest
kernel **2.6×** (sort/search) — both clear of 2.0× and 1.5×. The two-stage
memory access costs 5–29% in CPI against the five-stage rules (FFT least, as
its time is in the multiplier). CoreMark's window count (284,864) is within
one instruction of the SoC record's (284,865), which cross-checks the port.

**SRAM macro SPICE characterization.** ngspice 47 with KLU (built into
`~/tools/ngspice-47`) and the PDK's transistor netlist of the 2 KiB macro:
a simulation of the whole macro did not finish 2 ns of simulated time within
an hour, with either a DC or a transient operating point, so full-macro
simulation is not practical. The characterization for 18.6 will use a trimmed
netlist (the accessed rows and columns, with the removed cells' loading kept
as capacitance), as OpenRAM's own characterizer does.

## Milestones and gates

| Milestone | Content | Exit gate |
| --- | --- | --- |
| **18.0** | Spike; CPU shell with PicoRV32 and RVFI trace; lockstep comparator; `riscv-tests` in the shell; `riscv-arch-test` harness; timing scripts; PicoRV32 baseline timing | PicoRV32 passes lockstep on `riscv-tests`; the comparator catches an injected mismatch in every compared field; timing scripts report PicoRV32 on both targets — **met** (checklist below) |
| 18.1 | RV32I pipeline | `riscv-tests` rv32ui and arch-test I in lockstep; random programs in lockstep; first timing report |
| 18.2 | M extension | um/arch-test M, multiply/divide corner cases, lockstep, timing |
| 18.3 | Zicsr, traps, interrupts, counters | arch-test Zicsr; directed traps in every stage; interrupt tests in every pipeline state |
| 18.4 | A extension, `fence`, `fence.i` | ua/arch-test A and Zifencei; atomic and self-modifying-code tests |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests on the core |
| 18.6 | L1 caches with two-stage pipelined hits; SRAM interface; runtime port | cache reference model, back-pressure, firmware regression |
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
- [x] **Before the 18.1 RTL timing report:** constraints corrected, synthesis
  strategy chosen (`DELAY 1`) and the PicoRV32 baseline re-run (v2); on the
  FPGA, core-to-memory paths timed with the memory inside the block. Corner
  enforcement: the resizer already optimizes at all nine corners
  (`RSZ_CORNERS` falls back to `STA_CORNERS`); `TIMING_VIOLATION_CORNERS`
  only chooses which corners fail a run and stays at its default, since the
  reports carry every corner
- [x] **Flow correlation (first step):** placing and routing with 3 ns of
  extra setup uncertainty against a 10 ns signoff brings PicoRV32 to −0.583 ns
  at `max_ss` (94.5 MHz, +5.2% area); the 512 B array did not route with it at
  30% utilization
- [ ] **Flow correlation (18.1 timing work):** calibrate the resizer's wire RC
  (`LAYERS_RC`) against the signoff extraction, tune the margin, and choose
  the flow the Aster core is reported with
- [ ] SKY130 core-to-SRAM port budgets for the Aster core, from the correlated
  L1 array measurements (the array's write path, which is as long as its
  read path, included)
- [x] **Decided by the owner, 30 September 2026 — two-stage memory access.**
  The core logic is near 100 MHz at `max_ss` (PicoRV32, 94.5 MHz with a first
  flow correction) but a single-cycle standard-cell array read measured
  15.4 ns there (512 B; 13.1 ns in a superseded run), and 14.25 ns with the
  high-speed cells, which the owner chose to try first. Every instruction
  fetch and data access therefore spans two pipeline stages: the Aster core
  becomes a seven-stage pipeline (F1 F2 D E M1 M2 W).
- [x] **18.1 entry:** [`cpu.md`](cpu.md) §4–§5 revised for seven stages (stage
  contents, hazards and penalties, pipelined ports, the commit point at the
  end of M1 and trap/bus-error timing); reviewed twice and approved by the
  owner on 30 September 2026
- [x] **Aster-core shell:** a two-port shell (`verification/core/tb_core_ports.cpp`)
  with the Aster core's port protocol, independent back-pressure on each port,
  every retired store checked against the bus write the memory accepted, and
  no unmatched write at the end; proven with PicoRV32 behind a port adapter —
  48/48 riscv-tests plain and under stall, 47/47 arch-test, and a corrupted
  bus write detected (`make core-ports-tests`, in `make check`)
- [x] **Two-port shell memory follows the revised port contract** (cpu.md §5):
  one- or two-cycle answers (`+latency`, default 2), up to two requests in
  flight per port (`+max_inflight`), in order under random stalls, and
  `d_rsp_error` in the cycle after acceptance; PicoRV32 passes at both
  latencies (it keeps one request in flight, so the two-in-flight path is
  first exercised by the Aster core)
- [x] **Before 18.1 RTL:** the CPU kernels in the shell (7/7 on PicoRV32 in
  lockstep) and a trace-driven CPI model of the seven-stage pipeline: 1.20–1.65
  CPI, projected gate geometric mean about 3.6×, lowest kernel about 2.6×
  (above)
- [x] **Before 18.1 RTL:** the shell's pipelined memory tested with two
  requests in flight before the core relies on it — a unit test of the port
  response model (`verification/core/test_shell_ports.cpp`, in `make
  core-ports-tests`) with a requester presenting every cycle: answers in
  order, one per cycle, never before their latency, never more than the
  in-flight limit outstanding, and full rate with two in flight (1,000
  requests in 1,002 cycles), across both latencies, three limits and 20
  random-delay seeds; a mutant that ignores the limit fails it
- [ ] **By the milestone named:** hazard coverage extended to distance 4 (the
  register-file write-through) and AMO/`lr`/`sc` classed as load-like
  producers (18.1); CSR write point and `minstret` read semantics (18.3);
  `fence.i` draining in-flight data accesses and flushing F1, F2, the buffer,
  D and E (18.4)
- [ ] **Aster-core shell, for 18.1:** exact RVFI byte masks (no `word_loads`);
  a wrapper to the core's own reset and interrupt ports (the shell drives
  PicoRV32's `resetn` and reads its `trap`); request-protocol checks (payload
  stable while waiting; which withdrawals are allowed); injected
  `d_rsp_error`/`i_rsp_error` responses; RVFI sampled as registered outputs
  (the core must register them); from 18.6, the signature read through the
  cache hierarchy
- [x] **Decided 29 September 2026 — how the 18.7 performance gate aggregates.**
  Speedup per kernel = PicoRV32 cycles ÷ Aster-core cycles over the same
  window. The gate passes only if the **geometric mean** of the seven
  per-kernel speedups (CoreMark CRC run, Dhrystone, sort/search, FFT, strided,
  scalar Conv2D, scalar reduction) is **≥ 2.0×** and **no kernel is below
  1.5×**; every per-kernel speedup is published with the aggregate. Declared
  before any measurement ([`cpu.md`](cpu.md) §7). Measurement conditions
  confirmed by the owner on 30 September 2026: unrounded ratios; both cores on
  the same one-cycle shell SRAM with separate instruction and data banks, the
  Aster core without its L1.
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

  *30 September 2026:* item 1's "single-cycle hits" is replaced by the
  owner's two-stage memory access decision (above): the L1 arrays are split
  into two pipeline stages. Item 1 was measured early, as planned; with
  LibreLane's default flow a single-cycle standard-cell array read did not
  close 10 ns at `max_ss`, and the flow's own estimate did not predict signoff
  (see "Pre-18.1 measurements"), which led to that decision. Items 2–4 stand.
- [ ] 18.1 … 18.7 as in the table above

## Non-goals

Compressed instructions, supervisor/user modes, an MMU, floating point, dynamic
branch prediction before it is measured to pay, and multi-issue execution
(see [`cpu.md`](cpu.md) §1). SoC integration of the new core is Phase 20.
