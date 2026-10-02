# Phase 18: Aster core — CPU, L1/SRAM interface, and 100 MHz feasibility on the FPGA

Status: **in progress — milestone 18.0 (tooling) exit gate met; the owner chose
a two-stage memory access (a seven-stage core) and approved the revised cpu.md
§4–§5; the CPU kernels run in the shell and the CPI model projects the 18.7
gate at about 3.7×. Milestone 18.1 (complete; owner, 1 October 2026): the
seven-stage RV32I RTL passes riscv-tests, arch-test I and random programs in
lockstep, cycle for cycle with the CPI model on a memory that answers on
time. Timing after the 18.1 timing work (`941bff4`): the core meets 10 ns out
of context on the FPGA (114.9 MHz; with a block RAM behind its ports, 101.9
and 111.8 MHz); on SKY130, in the flow chosen in 18.1 as corrected on
1 October (delay cells excluded: the resizer had been using them as buffers),
register to register at the slow corner it meets 10 ns in all four
floorplans swept, +0.373 to +0.495 ns (103.9–105.2 MHz; 195–198 MHz
typical), with PicoRV32 at 104.3–105.2 MHz in the same flow and floorplans;
the request address still misses its 2 ns output budget at the slow corner,
by 1.6–1.7 ns (see "Milestone 18.1"). Milestone 18.2 (the M extension,
through `42a1f1f`): RV32IM passes rv32ui/um, arch-test I and M and random programs in
lockstep, cycle for cycle with the CPI model, and the eight CPU kernels run
on the RTL in lockstep, each window exactly the model's cycles — a measured
3.68× geometric mean over PicoRV32 (lowest 2.59×); the FPGA meets 10 ns
(108.0 MHz at `42a1f1f`). On 1 October 2026 the owner dropped the SKY130 ASIC
implementation: the FPGA is the only implementation target (the last SKY130
runs: `42a1f1f` at 10 ns missed the slow corner by up to 0.52 ns in four of
five floorplans, and at 12.5 ns closed in all five; see "Milestone 18.2" and
[`phase17-plus.md`](phase17-plus.md)).** The CPU specification this
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
- meets **10 ns** timing out-of-context on the PYNQ-Z1, or records the limiting
  path and its cost (SKY130 block-level STA was part of this goal until the
  owner dropped SKY130 on 1 October 2026).

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
  [`cpu.md`](cpu.md) §7, built from their SoC sources, headers and compile
  flags (read from the Makefile's defaults in a minimal environment, so a
  parent `make`'s or the shell's variables cannot change them)
  with only the shell's start-up and layout (`verification/core/kernels/`: a
  start-up that seeds the SoC register-page constants the kernels check, and
  a 96 KiB layout with at least 8 KiB of stack). The SoC register page is plain
  memory in the shells (`+io_page`) and in Spike; its performance-counter page
  is a deterministic clock in both (each word read of the cycle word adds
  1,000,000; Spike plugin `verification/core/spike/aster_clock.cc`), so
  CoreMark's and Dhrystone's timers behave identically. A kernel spins after
  printing its record, so the shell ends the run at the console store of the
  record's closing newline (`+kernel_end`) and the lockstep cuts Spike's
  stream at the same store. Each kernel must pass lockstep, print a passing
  AsterBench record, and match the retained Phase 17 baseline record of the
  same build in window instruction count and checksum — a check that the
  shell build runs the SoC's code. The shells time the measurement window
  from the retirement of the window-opening store to that of the closing
  store (`make core-kernels`, in `make check`: on PicoRV32 in the look-ahead
  shell, the §7 baseline, and in the two-port shell at `+latency=1`).

### Timing and area

Committed scripts produce, for any core top:

- a Vivado out-of-context synthesis and implementation at 10 ns on
  `xc7z020clg400-1` (timing summary and utilization);
- a SKY130 block synthesis and static timing analysis at 10 ns at the declared
  corners (cell area and per-corner slack) — used through 18.2; SKY130 was
  dropped on 1 October 2026, and these scripts remain as history.

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
clean electrical signoff was a later-phase gate (withdrawn with SKY130,
1 October 2026).

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

**CPU kernels in the shell, and the CPI model (30 September 2026).** All the
kernels pass on PicoRV32 in the look-ahead shell in lockstep with Spike, with
their own self-checks passing and their window instruction counts and
checksums equal to the retained Phase 17 records of the same builds (Conv2D
is listed twice; see below). Measurement windows (zero-wait memory, the 18.7
conditions) and the trace-driven model of the approved seven-stage pipeline
over the same windows (`scripts/cpi_model.py`, an estimate, not RTL: it
assumes every memory access is answered on time and a fetch every cycle
outside redirects, applies the §4 hazard and redirect rules with static
backward-taken prediction, and charges a divide 33 cycles; it does not model
fetch-buffer or data-port contention):

| Kernel | Window instructions | PicoRV32 CPI | Seven-stage model CPI | Projected speedup | Five-stage model CPI |
| --- | ---: | ---: | ---: | ---: | ---: |
| CoreMark (1 iteration) | 284,865 | 5.249 | 1.567 | 3.35× | 1.303 |
| Dhrystone | 492,302 | 4.237 | 1.585 | 2.67× | 1.320 |
| sort/search | 420,942 | 4.277 | 1.654 | 2.59× | 1.332 |
| FFT | 248,399 | 11.144 | 1.199 | 9.30× | 1.143 |
| strided | 1,559 | 4.151 | 1.508 | 2.75× | 1.172 |
| Conv2D, minimal top (`minimal_conv2d`; cross-check, not a gate kernel) | 704,346 | 7.850 | 1.574 | 4.99× | 1.398 |
| scalar Conv2D, coherent SoC (`conv2d_scalar_coh`; the gate's Conv2D) | 971,011 | 7.358 | 1.397 | 5.27× | 1.279 |
| scalar reduction | 53,305 | 4.076 | 1.385 | 2.94× | 1.154 |

The gate's Conv2D is the coherent SoC's scalar build (`conv2d_scalar_coh`;
owner decision, 30 September 2026, [`cpu.md`](cpu.md) §7); the minimal top's
Conv2D stays in the runs as a cross-check outside the gate. Projected against
the 18.7 gate over its seven kernels: a geometric mean of about **3.68×**,
lowest kernel **2.6×** (sort/search) — both clear of 2.0× and 1.5×. The
two-stage memory access costs 5–29% in CPI against the five-stage rules (FFT
least, as its time is in the multiplier). In the two-port shell at
`+latency=1` PicoRV32 runs 9–35% slower than in the look-ahead shell (its adapter cannot present the next address
early), so the look-ahead shell remains the §7 baseline.

**SRAM macro SPICE characterization.** ngspice 47 with KLU (built into
`~/tools/ngspice-47`) and the PDK's transistor netlist of the 2 KiB macro:
a simulation of the whole macro did not finish 2 ns of simulated time within
an hour, with either a DC or a transient operating point, so full-macro
simulation is not practical. The characterization for 18.6 was to use a
trimmed netlist (the accessed rows and columns, with the removed cells'
loading kept as capacitance), as OpenRAM's own characterizer does (withdrawn
with SKY130, 1 October 2026).

### Milestone 18.1: the seven-stage RV32I pipeline (30 September 2026)

The RTL is in `rtl/aster_core/`: `aster_core_fetch.sv` (F1, F2 and the
three-entry buffer), `aster_core_pkg.sv` (decoder, ALU, branch compare, load
alignment) and `aster_core.sv` (Decode, Execute, M1, M2, W, forwarding, the
load-use interlock, the data port, RVFI). Until traps arrive in 18.3, an
instruction with a trap cause stops the core at the commit point (end of M1)
as a trap record, with everything after it killed. Implementation choices
settled in 18.1 are recorded in [`cpu.md`](cpu.md) §9: the Execute redirect's
flush is registered (the wrong-path instructions are squashed — masked for a
cycle), the Decode redirect fires in the first Decode cycle, a branch to its
own fall-through never redirects, and the fetch unit holds up to nine fetches
in flight (three live).

**Verification** (all in `make check`):

- **Fetch unit, on its own** (`make core-aster-fetch`): a randomized unit
  test against the shell's memory model (`verification/core/test_aster_fetch.cpp`,
  96 runs of 20,000 cycles over both latencies, memory limits of 2, 3, 4 and
  16 fetches in flight, back-pressure, random Decode stalls, Decode and
  Execute redirects, targets outside memory, and a halt) checks that Decode
  receives exactly the program-order stream, the request protocol, the
  in-flight limit and the RTL's assertions (buffer overflow, answers with
  nothing in flight, more than nine in flight), and requires each corner to
  be reached. Deterministic runs check one fetch per cycle, the 2- and
  4-cycle redirect penalties (identical with a one-cycle memory), and the
  deepest state — nine fetches in flight, six discarded — after which the
  stream must still be exact.
- **The core in the two-port shell** (`make core-aster-tests`), in lockstep
  with Spike with exact byte masks and the shell's store and protocol checks:
  - riscv-tests rv32ui (40; `fence_i` and `ma_data` wait for 18.4 and 18.3)
    plus the 3 directed tests, 43 programs, on the two-cycle memory, the
    one-cycle memory, back-pressure, back-pressure with the one-cycle memory,
    and back-pressure with room for three and four requests in flight;
  - riscv-arch-test I (39/39), on the two-cycle and the one-cycle memory;
  - constrained-random programs with every read-after-write hazard bin
    covered at distances 1–4 (68/68; distance 4 is the register file's
    same-cycle write-through), on the two-cycle memory, the one-cycle memory
    and back-pressured;
  - directed tests: wrong-path fetches that run off the end of memory and are
    answered with `i_rsp_error` (the test requires such fetches — the core
    makes 7 — and they must be discarded), and control transfers that wait in
    Decode for a load (a redirect must be neither lost nor repeated);
  - thirteen trap-halt programs (an illegal instruction, misaligned `lw`, `lh`,
    `lhu`, `sh`, `sw`, jump, `jal` and branch targets, a load and a store the memory answers
    with `d_rsp_error`, the same error while the access waits in M1, and a
    right-path fetch outside memory) on the two-cycle memory, the one-cycle
    memory and two back-pressure seeds: each must trap at its `trap_pc`, match
    Spike in every record before it, and let no younger store reach memory
    (the shell continues past a data-port error with `+bus_error_traps` and
    fails an unmatched write after a trap as after a pass).
- **Cycle-exact against the CPI model on a memory that answers on time.**
  Every rv32ui, directed, arch-test and random program's cycle count must
  equal the seven-stage model's plus 7, the start and the drain
  (`--cpi-check`), on the two-cycle and the one-cycle memory; it does. (The
  check compares whole programs and does not cover back-pressure.) The
  one-cycle and two-cycle memories give identical counts, as §7's
  conservative comparison requires.
- **Planted bugs.** On the RTL of `9971ea1`, 40 bugs planted in the pipeline (each
  forwarding path and select, the operand capture while Execute waits, a
  load's value forwarded early, the answer held from M1, M2 waiting for its
  answer, the prediction flag, the Decode redirect and its once-only rule,
  the squash of Decode and Execute, the request conditions, sign extension,
  byte enables, the misalignment offset, branch compare, stall propagation,
  the register file's write-through, the trap kill and gating, the data-port
  error and its hold, a right-path fetch fault, the fall-through rule, RVFI
  data, store-data forwarding, and six in the fetch unit): 37 are caught, the
  performance-only ones by the cycle check (the held error only after
  `bus_error_behind_load` was added for it). The three survivors cannot
  change behavior: a load in M1 marked ready (the Decode interlock never lets
  a dependent instruction into Execute then), and two forwarding-select
  shortcuts that a stalled stage's priority or an empty stage's zeroed fields
  make harmless. Of 15 bugs planted in the fetch unit alone, its unit test
  catches 13 (the counter width only after the deepest-state run was added);
  the two survivors cannot change behavior (a write already inside the
  non-flush branch, and a priority case that cannot arise because Decode is
  masked then).

**Timing (step 4, first report;** evidence retained in
[`results/phase18/aster-18.1`](results/phase18/aster-18.1/README.md)**).** FPGA, Vivado out of context on
`xc7z020clg400-1` at 10 ns (`make timing-fpga-aster`; register-to-register):

| Block | WNS | Implied fmax | LUTs | FFs | BRAM36 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Core alone | +0.196 ns | 102.0 MHz | 1,688 | 767 | 0 |
| Core + two-cycle 128 KiB block RAM, the §5 form (the block RAM samples the request; its output register is the second cycle) | −0.440 ns | 95.8 MHz | 1,820 | 869 | 32 |
| Core + two-cycle block RAM with the request registered first (the block RAM read is the second cycle) | +0.364 ns | 103.8 MHz | 1,791 | 999 | 32 |

The paths §4 names — from the register behind `d_rsp_valid` through the
stall logic to the next data request, and from the one behind `d_rsp_error`
through the kill, ending at whatever samples `d_req_valid` (the block RAMs'
write enables in the §5 form, the request register otherwise) — have +3.711
and +3.168 ns of slack in the §5 form and +7.059 and +6.192 ns with the
request registered. The worst paths from those two registers to anywhere end
at the forwarding selects' precompute: +2.022 and +1.186 ns in the §5 form,
+2.236 and +1.942 ns with the request registered. The first run of the RTL missed by 3.2 ns
(75.8 MHz alone, 74.3 MHz with the block RAM); its limiting path ran from
Execute's forwarding compare through the operand mux, the branch compare,
the trap logic and the mispredict decision into the enables of Decode,
Execute and the fetch buffer. Four changes, none of them to behavior or to
any cycle count, closed it: the Execute redirect's flush is registered (the
squash), the mispredict no longer waits for the trap logic, the forwarding
selects are computed a cycle ahead and registered, and loads and stores have
their own address adder with alignment from the two low bits. The remaining
miss in the §5 form (−0.440 ns on the final RTL) is on the core-to-memory
path — the forwarded base register, the address adder, the memory's region
decode and the write enables of 32 block RAMs spread over the device; the
fix, measured above, is for the memory side to register the request before
the block RAM (same two-cycle latency, and the §4 structure of two
register-to-register memory stages). That is a memory-side decision for the
18.6 L1 and the Phase 20 fabric, recorded here for the owner.

SKY130, the core alone through post-route STA at 10 ns in the same flow and
constraints as the PicoRV32 baseline v2 (then `make timing-asic-aster`, which
has since moved to the flow chosen in the 18.1 timing work below; default
flow, no setup margin; ports with 2 ns input and output delays):

| Corner | Register-to-register setup | Implied fmax |
| --- | ---: | ---: |
| `nom_tt_025C_1v80` | +2.538 ns | 134.0 MHz |
| `max_ss_100C_1v60` | −4.874 ns | 67.2 MHz |
| `nom_ss_100C_1v60` | −4.327 ns | 69.8 MHz |
| `max_ff_n40C_1v95` | +5.028 ns | 201.1 MHz |

163,412 µm² of standard cells (18,891 cells, 1,763 flip-flops — 992 of them
the register file), no routing DRC errors; register-to-register hold met at
every corner (20 input-port hold violations at `max_ss`, worst −0.074 ns).
At the slow corner this is below PicoRV32's 83.5 MHz in the same flow, and the
miss is broad: 576 register-to-register endpoints fail at `max_ss`. The worst
path (−4.874 ns) runs from a bit of Decode's `rs1` field (`d_insn[16]`)
through the register file's same-cycle write-through compare (W's `rd`
against `rs1`) to a 32-bit select and Execute's operand register
(`e_rs1v[16]`); about 9 ns of it is chains of the smallest buffers (slews up
to 1.8 ns at the slow corner) against about 4 ns of logic. The next path
classes are within 0.7 ns: `halted` to the fetch unit's counters (−4.20 ns,
five chained buffers), Decode's instruction to the fetch buffer and `d_pc`
(−4.15 to −4.06 ns), `e_dec` to the forwarding selects (−3.94 ns). §4's two
named port paths (input to `d_req_valid`, within 2 ns input and output
delays): `d_rsp_valid` +1.035 ns and `d_rsp_error` −0.238 ns at `max_ss`
(+3.379 and +2.773 ns at `nom_tt`). Placing and routing against 3 ns of extra
setup margin, which gained PicoRV32 11 MHz, made the Aster core worse
(−5.462 ns at `max_ss`, 64.7 MHz; run `p18-aster-pnr-margin`). Proposed
fixes, the rest of 18.1's timing work: the flow correlation the checklist
carries (per-corner wire RC, `LAYERS_RC`, so the resizer builds fanout trees
for the real wires — the main lever, as the buffering dominates), and
precomputing the write-through match a cycle early (as the forwarding selects
already are) or replacing the write-through with a fourth forwarding source.

**18.1 timing work after the first report (30 September 2026).** In the
table: seven RTL changes in six commits — the first five behavior-neutral (every cycle count
unchanged, `make check` passing, planted bugs in each caught except ones that
cannot change behavior); the sixth (`9ffb3ab`, a load or store waiting for
ready even when misaligned) showed no measurable gain, and the owner rejected
the port rule it needed, so it was reverted; the load alignment leaving M2
(`3cf31ce`) changed approved §4 text and was confirmed by the owner (cpu.md
§9) — four flow experiments, and (below the first conclusions) two more RTL
changes and a utilization sweep, then one reverted experiment with a sweep of
its own, the flow's correction (delay cells excluded) with sweeps of both
cores, and the adopted select copies (`941bff4`) with a sweep; "chosen" rows
without a utilization ran at
40%, the configured value until the sweep. SKY130 runs are post-route at 10 ns in
the baseline flow unless noted; the slow corner is `max_ss_100C_1v60`:

| Run | RTL (commit) | Flow | `nom_tt` fmax | `max_ss` setup | `max_ss` fmax | Area µm² |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `p18-aster` (first report) | `9971ea1` | baseline | 134.0 MHz | −4.874 ns | 67.2 MHz | 163,412 |
| `p18-aster-pnr-margin` | `9971ea1` | +3 ns setup margin in place and route | 130.3 MHz | −5.462 ns | 64.7 MHz | 164,972 |
| `p18-aster-grt-repair` | `9971ea1` | design repair after global routing | 132.7 MHz | −5.000 ns | 66.7 MHz | 164,117 |
| `p18-aster-wt` | `34cf6d5` write-through select registered | baseline | 147.5 MHz | −2.981 ns | 77.0 MHz | 165,426 |
| `p18-aster-wt-rc` | `34cf6d5` | per-corner wire RC (`LAYERS_RC`, LibreLane's SKY130 table) | 132.5 MHz | −4.115 ns | 70.8 MHz | 172,781 |
| `p18-aster-pd` | `9d29175` + predecoded Decode redirect, circular fetch buffer | baseline | 140.4 MHz | −3.864 ns | 72.1 MHz | 161,879 |
| `p18-aster-ex` | `3cf31ce` + compare-free redirect target, loads aligned leaving M2 | baseline | 154.6 MHz | −2.811 ns | 78.1 MHz | 156,299 |
| `p18-aster-wr` | `209ddcb` + buffer writes off the stall chain | baseline | 154.0 MHz | −3.528 ns | 73.9 MHz | 156,325 |
| `p18-aster-wr-nobuf1` | `209ddcb` | baseline without `buf_1` | 158.3 MHz | −2.312 ns | 81.2 MHz | 164,171 |
| `p18-aster-wr-nobuf1-rc` | `209ddcb` | **chosen:** without `buf_1`, per-corner wire RC (40%) | 163.6 MHz | −1.435 ns | 87.5 MHz | 176,856 |
| `p18-aster-wr-nobuf1-rc-u38` | `209ddcb` | chosen, 38% core utilization | 168.0 MHz | −1.900 ns | 84.0 MHz | 180,670 |
| `p18-aster-sc-rc` | `9ffb3ab` + stall logic off the address alignment (since reverted) | chosen (40%) | 163.0 MHz | −1.644 ns | 85.9 MHz | 176,813 |
| `p18-picorv32-nobuf1` | PicoRV32 | baseline without `buf_1` | 147.8 MHz | −3.055 ns | 76.6 MHz | 165,770 |
| `p18-picorv32-nobuf1-rc` | PicoRV32 | chosen (40%) | 201.9 MHz | **+0.436 ns** | **104.6 MHz** | 175,177 |
| `p18-aster-oh` | `bf247e8` one-hot forwarding selects | chosen (40%) | 175.4 MHz | −1.142 ns | 89.7 MHz | 171,464 |
| `p18-aster-oh-u38` | `bf247e8` | chosen, 38% | 183.6 MHz | −0.152 ns | 98.5 MHz | 174,843 |
| `p18-aster-jt-u34` | `233aa60` + jalr target from the address adder | chosen, 34% | 182.8 MHz | −0.546 ns | 94.8 MHz | 178,113 |
| `p18-aster-jt-u36` | `233aa60` | chosen, 36% | 189.9 MHz | −0.190 ns | 98.1 MHz | 179,217 |
| `p18-aster-jt-u38` | `233aa60` | chosen, 38% | 187.4 MHz | **+0.168 ns** | **101.7 MHz** | 177,562 |
| `p18-aster-jt` | `233aa60` | chosen, 40% | 165.8 MHz | −1.571 ns | 86.4 MHz | 174,014 |
| `p18-aster-rw-u38` | `85a2607` + a `keep`-marked low-bit copy of rs1's select (not in the history; merged in synthesis) | chosen, 38% | 156.6 MHz | −2.683 ns | 78.8 MHz | 175,511 |
| `p18-aster-rr-u34` | `85a2607` registered operand readiness (reverted) | chosen, 34% | 172.2 MHz | −1.110 ns | 90.0 MHz | 181,445 |
| `p18-aster-rr-u36` | `85a2607` | chosen, 36% | 175.2 MHz | −1.178 ns | 89.5 MHz | 179,025 |
| `p18-aster-rr-u38` | `85a2607` | chosen, 38% | 178.7 MHz | −0.305 ns | 97.0 MHz | 178,145 |
| `p18-aster-rr-u40` | `85a2607` | chosen, 40% | 181.8 MHz | −0.422 ns | 96.0 MHz | 174,621 |
| `p18-aster-inv-u38` | `7dbcb50` + inverted copies of the selects for the stall logic (not in the history) | chosen, 38% | 173.5 MHz | −1.551 ns | 86.6 MHz | 175,605 |
| `p18-aster-nodly-u34` | `7dbcb50` (`233aa60`'s synthesized RTL) | **corrected:** chosen with delay cells excluded, 34% | 185.6 MHz | −0.465 ns | 95.6 MHz | 160,710 |
| `p18-aster-nodly-u36` | `7dbcb50` | corrected, 36% | 158.0 MHz | −1.939 ns | 83.8 MHz | 160,884 |
| `p18-aster-nodly-u38` | `7dbcb50` | corrected, 38% | 192.8 MHz | **+0.461 ns** | **104.8 MHz** | 158,392 |
| `p18-aster-nodly-u40` | `7dbcb50` | corrected, 40% | 200.1 MHz | **+0.363 ns** | **103.8 MHz** | 158,013 |
| `p18-picorv32-nodly` | PicoRV32 | corrected (40%) | 200.2 MHz | **+0.427 ns** | **104.5 MHz** | 155,821 |
| `p18-picorv32-nodly-u38` | PicoRV32 | corrected, 38% | 209.1 MHz | **+0.420 ns** | **104.4 MHz** | 157,322 |
| `p18-picorv32-nodly-u36` | PicoRV32 | corrected, 36% | 204.1 MHz | **+0.408 ns** | **104.3 MHz** | 157,137 |
| `p18-picorv32-nodly-u34` | PicoRV32 | corrected, 34% | 202.6 MHz | **+0.498 ns** | **105.2 MHz** | 158,336 |
| `p18-aster-norebuf-u36` | `7dbcb50` | corrected, setup repair without rebuffering, 36% | 188.4 MHz | −1.072 ns | 90.3 MHz | 157,500 |
| `p18-aster-norebuf-u38` | `7dbcb50` | the same, 38% | 198.7 MHz | −0.344 ns | 96.7 MHz | 156,210 |
| `p18-aster-invc-u34` | `941bff4` inverted select copies for the stall logic | corrected, 34% | 196.1 MHz | **+0.495 ns** | **105.2 MHz** | 160,749 |
| `p18-aster-invc-u36` | `941bff4` | corrected, 36% | 197.9 MHz | **+0.438 ns** | **104.6 MHz** | 159,704 |
| `p18-aster-invc-u38` | `941bff4` | corrected, 38% | 194.9 MHz | **+0.382 ns** | **104.0 MHz** | 159,409 |
| `p18-aster-invc-u40` | `941bff4` | corrected, 40% | 197.1 MHz | **+0.373 ns** | **103.9 MHz** | 157,671 |

What the runs show. In the baseline flow the typical corner tracks the RTL
changes (134 → 140–155 MHz), but the slow corner moves by up to 0.88 ns
between runs of RTL that only shortens paths: its worst paths are always a
high-fanout control net that the resizer has buffered with chains of `buf_1`
cells — adequate on its wire estimate, slow at signoff — and which net that
is changes with placement (after global routing the resizer estimated about
−2.7 ns where signoff found −4.9 ns, and stopped). Neither a setup margin nor
design repair after global routing helps. Two settings together do, for both
cores: no `buf_1`, and LibreLane's per-corner wire-RC table (alone, the table
fixes slews but costs setup on the Aster core — not measured alone on
PicoRV32; alone, dropping `buf_1` helps the Aster core and hurts PicoRV32).
It costs area — +13% on the Aster core at the same RTL (156,325 → 176,856
µm²), +10% on PicoRV32 (159,211 → 175,177 µm²) — and leaves maximum-transition
violations at the slow corner (837 and 1,199); signoff still uses the
extracted parasitics, so its numbers are not made optimistic by the change.
(Corrected on 1 October, below: without `buf_1` the resizer had buffered with
delay cells, which caused most of that area cost — PicoRV32 is 155,821 µm² in
the corrected flow — and, on PicoRV32, more than half of the violations (1,199
to 539 at `max_ss`; on the Aster core 5–18% of them).) **This is the flow the core is reported with from 18.1 on**
(`asic/sky130/config.core_aster.json`; PicoRV32 in the same flow, at 40%
utilization and not swept:
`make timing-asic-picorv32-chosen`). In it PicoRV32 closes 10 ns at the slow
corner (104.6 MHz, from 83.5 in the baseline flow), and the Aster core
reaches 84.0 and 87.5 MHz register to register (163.6 and 168.0 MHz
typical) in two runs of `209ddcb` — a 38%-utilization
floorplan moves the slow corner by 0.47 ns, the size of the effect a single
run can resolve (the reverted `9ffb3ab` gave 85.9 MHz). The core's remaining
register-to-register limit at the slow corner is Execute's datapath: the
forwarded operand through the 32-bit ALU, compare and adders into M1 and
into the registered redirect target, 1.4–1.9 ns short with few buffers left
on it. The design's worst path overall is a port path: the request address
(`d_req_addr`) against its 2 ns output budget, −2.90 and −3.39 ns at `max_ss`
(+2.13 and +1.94 ns at `nom_tt`) — the core-to-L1 request path, which the
open "core-to-SRAM port budgets" item must settle with the 18.6 L1. §4's two
named port paths move with placement: `d_rsp_valid` +0.145 and −0.423 ns,
`d_rsp_error` +1.726 and +1.508 ns at `max_ss`. On the FPGA `209ddcb`
runs at 113.1 MHz out of context, 103.7 MHz with the block RAM in the §5 form
and 111.9 MHz with the request registered (retained; the reverted `9ffb3ab`
gave 105.6, 100.5 and 110.0 MHz).

**Execute's datapath, continued (the owner chose to keep closing the slow
corner within 18.1).** Two more behavior-neutral changes: the forwarding
selects are one-hot including "none", so the operand is an AND-OR of its four
sources with no decode ahead of the select's fanout (`bf247e8`); and a
`jalr`'s target comes from the address adder rather than the ALU's result mux
(`233aa60`). The slow corner now closes register to register in one
floorplan and is within 0.55 ns in two more of a four-point utilization
sweep of `233aa60`: +0.168 ns at 38% (101.7 MHz, no failing register-to-
register endpoint; 187.4 MHz typical), −0.190 ns at 36% (98.1 MHz), −0.546 ns
at 34% (94.8 MHz) and −1.571 ns at 40% (86.4 MHz) — floorplan sensitivity
larger than any single change's effect, so the sweep, not the best point, is
the result: the pass at 38% is one placement with less margin than the
~0.5 ns run-to-run spread, the same 38% floorplan was worse than 40% for
`209ddcb`, and 657 maximum-transition violations remain at `max_ss` (limit
0.75 ns, worst 1.34 ns). 38% is now the configured utilization
(`config.core_aster.json`) because it was the sweep's passing point, not
because it is known to be better; later RTL (18.2's multiplier) is expected
to move it, so each milestone's timing report reruns the sweep. §4's named
port paths pass at 34–38% (+0.71 to +1.09 ns for `d_rsp_valid`, +1.14 to
+1.56 ns for `d_rsp_error` at `max_ss`; −0.45 ns for `d_rsp_valid` at 40%).
Other port paths to memory still miss at the slow corner in every floorplan:
the request address by about 2.0–2.2 ns (all-path −1.98 to −2.17 ns),
`d_req_valid` (from a register) and `i_req_addr` by up to 0.3 ns, and the
`i_rsp_valid` input paths by 0.49 ns at 34% — the core-to-L1 port budgets
for 18.6. Hold is met on every path. On the FPGA `233aa60` runs at 111.4 MHz out of context,
101.6 MHz with the block RAM in the §5 form and 114.8 MHz with the request
registered. Evidence: [`results/phase18/aster-18.1-timing-work`](results/phase18/aster-18.1-timing-work/README.md).

**Tried and reverted: registered operand readiness.** At the slow corner,
`233aa60`'s failing paths include, in every failing floorplan, paths from a
forwarding select through Execute's stall logic (into Decode's hold, the
selects themselves, the fetch buffer's count and the operand registers) and
Execute's datapath into M1; at 34% and 40% also the register-file read into
the operand registers (40%'s worst, −1.571 ns) and the register-file write,
and at 40% paths inside fetch. `85a2607` computed whether an operand waits for a load
in M1 or M2 a cycle ahead and registered it, so readiness no longer passes
through the select's fanout; it was behavior-neutral (`make check`, cycle
counts, planted bugs). Its sweep is no better: −1.110, −1.178, −0.305 and
−0.422 ns at 34/36/38/40% (mean −0.754 ns against `233aa60`'s −0.535; no
floorplan passes), and the FPGA is slower (108.8, 101.4 and 106.9 MHz against
111.4, 101.6 and 114.8). At 40% and 36% its worst paths still run from a
select through the misalignment check — which reads the forwarded operand's
two low bits — into Decode's hold; at 38% the same route ends in the selects
and in the new readiness registers, which themselves fail at 34–38% (worst
−0.741 ns at 36%) because computing them put logic on the next-select cone;
at 34% the worst is Execute's datapath (rs2's select through the ALU into
M1). §4's `d_rsp_valid` path misses by 0.013 ns at 36%. Reverted in
`b31761b`; the synthesized RTL is again `233aa60`'s. A `keep`-marked copy of
rs1's select driving only those low bits was also run (not in the history;
its diff is retained) but did not test the idea: synthesis merged the two
registers, keeping only the copy's name (1,776 flops, as without it), and that
run's worst path (−2.683 ns at 38%, both §4 named paths failing) has a chain
of 36 rebuffer cells on the low-bit output. A copy synthesis cannot merge
(for example, one holding the inverted select) had not been tried then; it
was the one option found that leaves §4 unchanged (the others change §4: the
rejected ready/valid rule, or a pipeline change such as a skid register ahead
of Execute), and it is adopted below (`941bff4`).

**The chosen flow's delay cells (1 October 2026).** Part of the
floorplan-to-floorplan spread had a cause in the flow itself. With `buf_1`
excluded, the resizer's weakest remaining buffers are the delay cells
(`dlygate4sd*`, `dlymetal6s*`; a `dlygate4sd3_1` takes 1.1 ns or more at
`max_ss`, median about 1.45 ns), and design repair used them as fanout
buffers: 850 to 1,011 in each chosen-flow run counted, of either core (2,400 to 2,800 delay cells counting
hold repair's, which the baseline flow also uses; there design repair uses
`buf_1` and no delay cells), on 187 of the 232 failing paths of `233aa60`'s
40% run (up to 6.4 ns of them on one path) and 485 of 547 in the first run
without `buf_1` (`scripts/timing/buffer_census.py`; a report lists one path
per failing endpoint). The Aster runs that passed or nearly did were
placements where they missed the worst paths (PicoRV32 passed with two on its
worst path). The inverted select copies — a copy
synthesis cannot merge, run on `7dbcb50` after passing `make check` (not
retained) — were caught by it: −1.551 ns at 38%, 241 of the 247 failing paths
through delay cells; that run says nothing about the copies (re-measured in
the corrected flow below; its diff is retained). Excluding the delay cells as well (both
configurations; hold repair then uses `clkbuf_1`, and hold is met on every
path at every corner, worst +0.094 ns) leaves synthesis unchanged — the
netlist is identical to the `233aa60` runs' — and moves `233aa60`'s logic to
+0.461, +0.363 and −0.465 ns at 38, 40 and 34% (from +0.168, −1.571 and
−0.546), with 0, 0 and 3 failing register-to-register paths, 158–161 thousand
µm² (about −10%), and 186–200 MHz typical. At 36% it is worse, −1.939 ns
(from −0.190): all five failing paths pass through one serial chain of 24
`buf_6` rebuffer cells that post-CTS setup repair built on the forwarding-select
path. Such chains predate the correction — up to 21 cells in baseline-flow
runs and 36 in the keep-copy run — and the other corrected runs' longest is 3
to 5; so the spread remains (−1.939 to +0.461 ns across the four floorplans),
its extreme at 36% now from a chain, while 34%'s three failing paths start
at rs2's forwarding select and pass through no chain. Turning rebuffering off in setup repair is no remedy
(−1.072 ns at 36%, −0.344 at 38%). PicoRV32 in the corrected flow is
unchanged, +0.427 ns at 40% (104.5 MHz, from 104.6), and steady across the same
four floorplans: +0.498, +0.408, +0.420 and +0.427 ns at 34/36/38/40%
(104.3–105.2 MHz), a 0.09 ns spread against the Aster core's 2.4 ns (0.93 ns
without the 36% run). In the same flow, the spread comes from the Aster core's
forwarding-select paths, not the flow alone: its failing paths all start at
the forwarding selects (at 36% through setup repair's chain). In the four corrected Aster (`nodly`) runs, §4's named port
paths pass (`d_rsp_valid` +0.50 to +1.28 ns, `d_rsp_error` +1.46 to +1.69 ns at
`max_ss`), and of the other port paths only the request address still misses
its 2 ns output budget, by 1.55–1.72 ns (from about 2.1). The antenna check
this timing flow runs finds one antenna-ratio violation in the Aster core's
38% run (1.24, on a `met1` net) and one in PicoRV32's 36% run (1.05, `met1`),
the only ones in these runs. The corrected flow is the
configured one for both cores from now on; the readiness result above was
measured in the old flow (the select copies were re-measured in the corrected
flow, below). Evidence:
[`results/phase18/aster-18.1-delay-cells`](results/phase18/aster-18.1-delay-cells/README.md).

**Inverted select copies for the stall logic (`941bff4`, adopted).** Every
failing path of the corrected sweep started at a forwarding select. `941bff4`
gives Execute's stall logic its own registered copies of the selects — rs1's
four bits and rs2's two load bits, each holding the complement so that
synthesis cannot merge them into the selects (a `keep`-marked plain copy was
merged) — and takes readiness and rs1's two low bits (the alignment and byte
enables) from them, so whether Execute advances no longer waits behind the
32-bit operand mux's fanout. It is behavior-neutral: `make check` with every
cycle count unchanged, simulation assertions that the copies and low bits
agree with the selects, and four of five planted bugs caught with the
assertions off (the fifth, rs2's readiness ignoring a load in M1, cannot
change behavior: the load-use interlock keeps a consumer in Decode while its
load is in Execute or M1). In the corrected flow the slow corner now meets
10 ns register to register in all four floorplans: +0.495, +0.438, +0.382 and
+0.373 ns at 34/36/38/40% (105.2, 104.6, 104.0 and 103.9 MHz; 195–198 MHz
typical; 157,671–160,749 µm²), with no failing register-to-register path at
any corner, a 0.12 ns spread (PicoRV32's is 0.09 ns), no delay cells and
rebuffer chains of 2 to 7 cells. Hold is met on every path at every corner
(worst +0.055 ns, at 34%). Of §4's named port paths `d_rsp_valid` gains
margin (+1.90 to +2.20 ns at `max_ss`, from +0.50 to +1.28) and `d_rsp_error`
is about unchanged (+1.63 to +1.82 ns); the request address is the only
failing port path, by 1.64–1.73 ns against its 2 ns budget — for 18.6. The
34% run has one antenna-ratio violation (1.22, `met1`), and 510–648
maximum-transition violations remain at `max_ss`, as in the other corrected
runs. These are one run per floorplan; the margins (+0.37 ns and up) exceed
the corrected flow's floorplan spread (0.12 ns here, 0.09 ns for PicoRV32),
though not the ~0.5 ns run-to-run spread measured in the old flow. On the
FPGA the core runs at 114.9 MHz out of context, 101.9 MHz with the block RAM
in the §5 form — a thin +0.184 ns, its worst path from rs1's select through
the address adder to the block RAMs' write enables — and 111.8 MHz with the
request registered (from 111.4, 101.6 and 114.8: the request-registered top
lost 3 MHz). The configured utilization stays 38%. Evidence:
[`results/phase18/aster-18.1-select-copies`](results/phase18/aster-18.1-select-copies/README.md).

### Milestone 18.2: the M extension (1 October 2026)

The Aster core implements RV32IM (`4f42794`, revised for timing in `d74408d`
and `bdfa836`):

- **Multiply** (`mul`, `mulh`, `mulhsu`, `mulhu`), pipelined over Execute, M1
  and M2 as §4 says. The operands travel with the instruction; M1 forms the
  radix-4 Booth partial products of the 33-bit sign- or zero-extended operands
  (17 rows, each sign-extended to 64 bits, and a row of the negations' +1s)
  and compresses them with 3:2 carry-save adders to four vectors, registered
  at its end, so no carry propagates in M1; M2 compresses the four to two and
  adds them into the 64-bit product, whose low or high word is registered into
  W (`bdfa836`; the first version formed four 17×17 products in M1). The result is
  ready from W, as a load's is, so the load-use interlock and Execute's
  readiness treat "load or multiply" as one late result: 2 and 1 cycles at
  distances 1 and 2 (§4). The partial products load whenever M1 holds a
  multiply rather than on M1's advance (which waits on the data port's
  answer): a multiply never waits in M2, which the RTL asserts.
- **Divide and remainder**, an iterative radix-2 restoring divider in Execute
  that holds Execute for 36 cycles (§4's table, revised from "≈33"): the
  operands are latched as read in the first cycle their values are ready (no
  arithmetic after the forwarding mux), the next cycle turns them into
  magnitudes and notes the signs and a zero divisor, 32 steps follow, and a
  cycle registers the result with its sign, so the result leaves Execute
  from a register, like an ALU result. A divisor of zero gives a quotient of
  all ones and the dividend as remainder, and −2³¹ / −1 gives −2³¹ and 0, as
  RISC-V specifies. Only the divider's count resets when Execute takes a new
  instruction or a trap kills it; its data registers load on a start and step
  while the count runs, which keeps them off those nets. The CPI model's
  `DIVIDE_CYCLES` follows (36).

Verification (`make core-aster-tests`, `make core-aster-kernels`):

- riscv-tests rv32ui and rv32um and the directed tests, 52/52, in lockstep
  and by signature with Spike in all six memory modes, cycle for cycle with
  the CPI model on the memories that answer on time; arch-test I and M, 47/47,
  also cycle for cycle;
- constrained-random programs with all eight M operations (about 5,800 per
  run of 20), 112/112 read-after-write bins (multiply and divide results into
  every consumer at distances 1–4), on time and back-pressured;
- `directed/wrongpath_muldiv`: a multiply or divide squashed on a
  mispredicted path (after a taken forward branch or a `jalr`, the one squashed
  in Execute) does nothing — confirmed to put a squashed division in Execute,
  where the RTL asserts it never starts;
- `trap_halt/muldiv_killed`: a division and a multiply behind a load that the
  memory answers with an error; on the back-pressure seed added for it (4) the
  division is running when the trap kills it (checked: killed at count 1);
- assertions: a squashed division never starts, the divider runs only for
  Execute's division, a division leaves Execute only when done, a multiply
  never waits in M2, and a finished division's result holds while it waits
  (the shell's memory never stalls long enough for that today; 18.6's cache
  misses will);
- planted bugs with the assertions off, over the versions: 25 of 33 caught
  (the carry-save multiplier's six all by rv32um; `4f42794`'s message counts
  15 of 21 for the first two rounds, which were 14); the eight survivors are
  equivalent or unreachable (a squashed start, which Execute's reset clears at
  the same edge; the divider not resetting on a kill, cleared a cycle later
  when Execute empties; the multiply's M1 and M2 readiness terms, two
  mutants, which the interlock makes unreachable; partial products loaded every cycle; the
  divider's data loaded for a squashed division; the divider's data stepping
  when done, which needs a 30-cycle stall; stepping enabled in the magnitude
  cycle, which the magnitude branch shadows). The carry-save multiplier's
  arithmetic was also checked on its own against exact products, over 3
  million vectors including corner values.

**The CPU kernels on the Aster core.** With M in place, the eight CPU kernels
run on the RTL in the two-port shell: in lockstep with Spike, with their
self-checks passing and their window instruction counts and checksums equal
to the Phase 17 records — and each measurement window takes exactly the CPI
model's cycles (`make core-aster-kernels`, part of `make check`, requires the
equality on the one-cycle memory of §7, and was shown to fail on a perturbed
model; the two-cycle memory gives the same cycles). The
model's projection is therefore now a measurement of the RTL's cycles. Against
PicoRV32 in the look-ahead shell (the §7 baseline), measured window cycles:

| Kernel | PicoRV32 cycles | Aster core cycles | Aster core CPI | Speedup |
| --- | ---: | ---: | ---: | ---: |
| CoreMark (1 iteration) | 1,495,136 | 446,348 | 1.567 | 3.35× |
| Dhrystone | 2,085,683 | 783,326 | 1.591 | 2.66× |
| sort/search | 1,800,445 | 696,118 | 1.654 | 2.59× |
| FFT | 2,768,064 | 297,863 | 1.199 | 9.29× |
| strided | 6,472 | 2,351 | 1.508 | 2.75× |
| scalar Conv2D, coherent SoC (the gate's) | 7,144,621 | 1,356,221 | 1.397 | 5.27× |
| scalar reduction | 217,275 | 73,811 | 1.385 | 2.94× |
| Conv2D, minimal top (not a gate kernel) | 5,529,285 | 1,108,394 | 1.574 | 4.99× |

Over the gate's seven kernels: geometric mean **3.68×**, lowest **2.59×**
(sort/search), against the gate's 2.0× and 1.5× — the cycle side of
the 18.7 gate, before 18.3–18.6 add to the core (the §7 conditions: both cores
on the one-cycle shell SRAM, the Aster core without its L1).

**Timing.** On the FPGA every version measured meets 10 ns out of context: `4f42794`
at 107.6 MHz (101.4 with the block RAM in the §5 form, 106.3 with the request
registered; the multiplier in 4 DSP blocks), `bdfa836` at 107.3 (101.9, 105.0;
with the carry-save multiplier the core uses 3,236 LUTs, against 2,092, and no DSP).
On SKY130 the M extension grew the core's standard-cell area by about
57–104% (about 160 thousand µm² in 18.1; 326 thousand for `4f42794`,
259–267 thousand for `bdfa836`, 252–258 thousand for `42a1f1f`), and the slow corner missed — in the multiplier, the
divider's negations, and, as the area grew, Execute's paths that had closed
in 18.1. Runs at `max_ss`, register to register, in the corrected chosen flow
(evidence: [`results/phase18/aster-18.2`](results/phase18/aster-18.2/README.md)):

| Run | RTL | Utilization | `nom_tt` fmax | `max_ss` setup | `max_ss` fmax | Area µm² |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `p18-aster-m-u38` | `4f42794` | 38% | 157.0 MHz | −1.971 ns | 83.5 MHz | 326,461 |
| `p18-aster-m2-u34` | `d74408d` (Booth in synthesis) | 34% | 150.7 MHz | −3.003 ns | 76.9 MHz | 300,997 |
| `p18-aster-m2-u36` | `d74408d` | 36% | 153.0 MHz | −3.103 ns | 76.3 MHz | 298,819 |
| `p18-aster-m2-u38` | `d74408d` | 38% | 148.9 MHz | −2.707 ns | 78.7 MHz | 297,517 |
| `p18-aster-m2-u40` | `d74408d` | 40% | 151.7 MHz | −2.141 ns | 82.4 MHz | 295,615 |
| `p18-aster-m2nb-u38` | `d74408d` without Booth | 38% | 147.1 MHz | −2.622 ns | 79.2 MHz | 332,141 |
| `p18-aster-m2nb-u40` | `d74408d` without Booth | 40% | 155.7 MHz | −2.101 ns | 82.6 MHz | 328,649 |
| `p18-aster-m3-u34` | carry-save, two vectors out of M1 (not in the history) | 34% | 175.3 MHz | −0.488 ns | 95.4 MHz | 314,793 |
| `p18-aster-m3-u36` | the same | 36% | 169.1 MHz | −0.869 ns | 92.0 MHz | 301,110 |
| `p18-aster-m3-u38` | the same | 38% | 170.1 MHz | −1.226 ns | 89.1 MHz | 301,399 |
| `p18-aster-m3-u40` | the same | 40% | 170.3 MHz | −0.984 ns | 91.0 MHz | 298,686 |
| `p18-aster-m3ss-u38` | the same, synthesized at `max_ss` (`SYNTH_CORNER`) | 38% | 170.3 MHz | −0.963 ns | 91.2 MHz | 294,475 |
| `p18-aster-m4-u34` | `bdfa836`: carry-save, four vectors out of M1 | 34% | 188.3 MHz | −0.340 ns | 96.7 MHz | 266,956 |
| `p18-aster-m4-u36` | `bdfa836` | 36% | 180.7 MHz | −0.733 ns | 93.2 MHz | 262,357 |
| `p18-aster-m4-u38` | `bdfa836` | 38% | 191.8 MHz | −0.480 ns | 95.4 MHz | 260,998 |
| `p18-aster-m4-u40` | `bdfa836` | 40% | 190.5 MHz | **+0.078 ns** | **100.8 MHz** | 260,531 |
| `p18-aster-m4-u42` | `bdfa836` | 42% | 188.7 MHz | −0.505 ns | 95.2 MHz | 259,443 |
| `p18-aster-m4-u44` | `bdfa836` | 44% | 187.0 MHz | −0.225 ns | 97.8 MHz | 259,814 |
| `p18-aster-m4-u46` | `bdfa836` | 46% | 181.4 MHz | −0.399 ns | 96.2 MHz | 260,388 |
| `p18-aster-m5-u34` | `42a1f1f`: one-hot result select, M1 data loaded when free | 34% | 190.9 MHz | −0.094 ns | 99.1 MHz | 258,163 |
| `p18-aster-m5-u36` | `42a1f1f` | 36% | 188.6 MHz | **+0.038 ns** | **100.4 MHz** | 257,972 |
| `p18-aster-m5-u38` | `42a1f1f` | 38% | 177.8 MHz | −0.520 ns | 95.1 MHz | 255,784 |
| `p18-aster-m5-u40` | `42a1f1f` | 40% | 187.1 MHz | −0.020 ns | 99.8 MHz | 252,938 |
| `p18-aster-m5-u44` | `42a1f1f` | 44% | 184.1 MHz | −0.109 ns | 98.9 MHz | 251,620 |
| `p18-aster-m6-u34` | `42a1f1f` + high-half select copies, carry-select multiply add (reverted; not in the history) | 34% | 183.1 MHz | −0.378 ns | 96.4 MHz | 261,719 |
| `p18-aster-m6-u36` | the same | 36% | 176.3 MHz | −1.027 ns | 90.7 MHz | 256,674 |
| `p18-aster-m6-u38` | the same | 38% | 173.5 MHz | −1.249 ns | 88.9 MHz | 257,824 |
| `p18-aster-m6-u40` | the same | 40% | 183.5 MHz | −0.812 ns | 92.5 MHz | 256,923 |
| `p18-aster-m6-u44` | the same | 44% | 184.9 MHz | −0.389 ns | 96.3 MHz | 254,809 |

The first run's failures were the multiplier's M1 stage (to −1.97 ns) and the
divider's negations of the forwarded operand and of its result (to −1.94 ns);
`d74408d` moved the negations off those paths into the divider's own cycles,
where as register-to-register paths they still failed in every `d74408d` run
(worst −3.103 ns) and in most two-vector runs (to −0.765 ns), but in no
`bdfa836` run; it also turned on Booth multipliers in synthesis for both
cores (PicoRV32's timing build has no multiply operator, and its netlist with
the setting is byte-identical to its swept runs'), which did not help
materially: the multiplier's own worst paths were −2.304, −2.279, −2.437 and
−2.141 ns at 34–40% with Booth, −2.622 and −2.101 ns at 38 and 40% without.
Synthesis maps for the typical corner (no `SYNTH_CORNER` is
set); one run mapped at the slow corner gave −0.963 ns against −1.226 (161
failing paths against 224), a gain smaller than the spread across floorplans,
so it was not adopted without repeats. The written-out carry-save multiplier
(`bdfa836`) takes the multiplier out of the failing paths at 34, 38 and 40%
(at 36% 14 of the 115 failing paths still run into or out of its M1
registers, to −0.291 ns) and brings the sweep to −0.340, −0.733, −0.480 and
+0.078 ns (5, 115, 3 and 0 failing register-to-register paths), 181–192 MHz
typical; the Booth setting was
removed again, as no multiply operator is left in either core. What fails now
differs by floorplan: Execute's datapath into M1 (34%), the stall chain's
enable trees from W's forwarded value (36%), the branch target into fetch
(38%). Hold is met on every path at every corner (worst +0.062 ns in the
`bdfa836` runs, +0.059 over all sixteen). §4's
named paths pass in every `bdfa836` run (`d_rsp_valid` +0.08 to +0.79 ns,
`d_rsp_error` +0.78 to +1.46 ns); of the other port paths the request address
misses its 2 ns output budget by 2.03–2.53 ns (from 1.6–1.7 in 18.1),
`i_req_addr` by up to 0.38 ns at 34, 36 and 38% (it passed in 18.1's final
runs, +0.15 to +0.70 ns), and at 36% `d_req_valid` and `d_req_wdata` by up
to 0.30 ns.

Utilization is not the lever: `bdfa836` at 42, 44 and 46% gave −0.505, −0.225
and −0.399 ns, every failing path the forwarding select through Execute's ALU
into M1's result. `42a1f1f` makes Execute's result a one-hot select decoded in
Decode (no op decode after the forwarded operands; operand A is rs1 alone —
`auipc` takes the branch-target adder, `lui` the immediate, add and subtract
their own adders) and loads M1's data fields whenever M1 is free (`m1.valid`
qualifies them), so Execute's late advance and the kill are off their inputs;
it is behavior-neutral (`make check`; six planted bugs, all caught). Its sweep
is −0.094, +0.038, −0.520, −0.020 and −0.109 ns at 34/36/38/40/44% (1, 0, 2,
2 and 6 failing paths; 178–191 MHz typical; 252–258 thousand µm²): Execute
into M1's result at 34, 38 and 40%, M2's product into W at 44%. Hold is met
everywhere (worst +0.047 ns) and §4's named paths pass (`d_rsp_valid` +0.93
to +1.26 ns), but the request address misses its output budget by 2.79–3.01
ns (from 2.03–2.53 for `bdfa836`), and `i_req_addr` and `d_req_valid` by up to
0.44 ns. On the FPGA `42a1f1f` runs at 108.0, 102.8 and 106.1 MHz. A further
step — inverted copies of the forwarding selects for the operands' high
halves and a carry-select multiply add — was worse at every floorplan
(−0.378 to −1.249 ns, divider paths failing again) and was reverted (its diff
is retained); synthesis had merged the new high-half copies with the stall
logic's inverted copies, which hold the same value, so the step tested the
carry-select add but not the split select fanout; at this size, changes of a few hundred picoseconds are within
the variation from one run to the next.

**SKY130 dropped (1 October 2026, owner decision).** The owner first lowered
the SKY130 target to 80 MHz: at 12.5 ns `42a1f1f` closes register to register
at `max_ss` in all five floorplans swept — +0.411, +0.560, +0.488, +0.394 and
+0.383 ns at 34/36/38/40/44% (82.5–83.8 MHz; 161–165 MHz typical; no failing
register-to-register path, hold met everywhere; the same synthesized netlist
as the 10 ns runs — the flow repairs only up to the period asked for, so these
frequencies are not the core's limit, which reached 95–100 MHz at 10 ns), with
the request address still 0.78–0.97 ns over its
2.5 ns output budget, and PicoRV32 at +1.022 ns (87.1 MHz). Then the owner
dropped the SKY130 implementation from the project: the FPGA is the only
implementation target, and the frozen SKY130 targets are withdrawn, with the
evidence and trade-off recorded in [`phase17-plus.md`](phase17-plus.md) and
[`cpu.md`](cpu.md) §9. These are the last SKY130 runs; the scripts and
retained evidence stay as history.

## Milestones and gates

| Milestone | Content | Exit gate |
| --- | --- | --- |
| **18.0** | Spike; CPU shell with PicoRV32 and RVFI trace; lockstep comparator; `riscv-tests` in the shell; `riscv-arch-test` harness; timing scripts; PicoRV32 baseline timing | PicoRV32 passes lockstep on `riscv-tests`; the comparator catches an injected mismatch in every compared field; timing scripts report PicoRV32 on both targets — **met** (checklist below) |
| 18.1 | RV32I pipeline | `riscv-tests` rv32ui and arch-test I in lockstep; random programs in lockstep; first timing report — **met** (owner, 1 October 2026; "Milestone 18.1" below) |
| 18.2 | M extension | um/arch-test M, multiply/divide corner cases, lockstep, timing |
| 18.3 | Zicsr, traps, interrupts, counters | arch-test Zicsr; directed traps in every stage; interrupt tests in every pipeline state |
| 18.4 | A extension, `fence`, `fence.i` | ua/arch-test A and Zifencei; atomic and self-modifying-code tests |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests on the core |
| 18.6 | L1 caches with two-stage pipelined hits; SRAM interface; runtime port | cache reference model, back-pressure, firmware regression |
| 18.7 | Evaluation and feasibility | Against PicoRV32 on the CPU set in the same shell: geometric mean of the per-kernel speedups ≥2.0× and every kernel ≥1.5×, each kernel's speedup published; 100 MHz feasibility report for the FPGA (SKY130 dropped, 1 October 2026) |

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
- [x] **Flow chosen (18.1 timing work, 30 September 2026):** the flow the
  Aster core is reported with — the baseline flow plus LibreLane's per-corner
  wire RC (`LAYERS_RC`, `VIAS_R`, its stock SKY130 table) and no `buf_1`
  cells — closes PicoRV32 at the slow corner (104.6 MHz) and gives the Aster
  core 84–88 MHz there register to register; the setup margin and design
  repair after global routing were measured and rejected
- [x] **Flow corrected (18.1, 1 October 2026):** without `buf_1` the resizer
  had buffered with delay cells; both cores' chosen configurations now
  exclude `dlygate4sd*` and `dlymetal6s*` as well (a test keeps the two
  configurations' settings equal). PicoRV32 104.3–105.2 MHz at `max_ss`
  across 34–40%; `233aa60`'s logic meets 10 ns register to register in two of
  four floorplans (38%, 40%), `941bff4`'s in all four (next item)
- [x] **Slow corner closed register to register (18.1, `941bff4`):** with
  inverted copies of the forwarding selects driving Execute's stall logic, 10 ns
  is met at `max_ss` in all four floorplans swept (+0.373 to +0.495 ns,
  103.9–105.2 MHz); port paths remain for 18.6
- [x] **Decided by the owner, 1 October 2026 — the direct Execute redirect
  (cpu.md §9):** §9 reopened the question if the 18.1 timing work closed
  `max_ss` with margin; it closes register to register by only +0.37 to
  +0.50 ns, a thin margin for putting the branch compare in front of the fetch
  address (in the first report the paths into the registered redirect alone
  missed by about 3–4 ns, cpu.md §9), and no direct redirect was built, so the
  registered redirect (4 cycles) stays
- [x] **Serial rebuffer chains — closed: SKY130 dropped (1 October 2026).** Setup repair occasionally built a
  long serial chain of buffers on one net (24 cells in `233aa60`'s 36% run of
  the corrected flow; up to 21 in baseline-flow runs and 36 in the old chosen
  flow); with `941bff4` the longest is 7 and none is on a failing path, but
  each milestone's sweep should check (`buffer_census.py`)
- [x] **PicoRV32 swept in the corrected flow:** 34/36/38/40%, +0.498, +0.408,
  +0.420 and +0.427 ns at `max_ss` (104.3–105.2 MHz), a 0.09 ns spread
- [x] **Flow correlation — closed: SKY130 dropped (1 October 2026).** Calibrating the resizer's wire RC per
  layer against the signoff extraction (the stock table was used instead);
  needed if 18.7's feasibility report requires a tighter correlation
- [x] **Closed: SKY130 dropped (1 October 2026); the FPGA's block-RAM port timing stays in 18.6.** SKY130 core-to-SRAM port budgets for the Aster core, from the correlated
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
- [x] **Before 18.1 RTL:** the CPU kernels in the shell (both Conv2D builds
  included, 8/8 on PicoRV32 in lockstep in both shells, each equal to its
  Phase 17 record in window instruction count and checksum) and a
  trace-driven CPI model of the seven-stage pipeline: 1.20–1.65 CPI,
  projected gate geometric mean about 3.7× (3.683), lowest kernel about 2.6× (above)
- [x] **Before 18.1 RTL:** the shell's pipelined memory tested with two
  requests in flight before the core relies on it — a unit test of the port
  response model (`verification/core/test_shell_ports.cpp`, in `make
  core-ports-tests`) with a requester presenting every cycle: answers in
  order, one per cycle, never before their latency, never more than the
  in-flight limit outstanding, and full rate with two in flight (1,000
  requests in 1,002 cycles), across both latencies, three limits and 20
  random-delay seeds; a mutant that ignores the limit fails it
- [x] **18.1:** hazard coverage extended to distance 4 (the register-file
  write-through; `rvgen` places uses 1–4 instructions after their producers,
  68/68 bins on the Aster core, 112/112 with M on PicoRV32) and AMO/`lr`/`sc`
  classed as load-like producers
- [x] **RVFI `pc_wdata` checked (18.1):** the trace carries `pc_rdata` only, so
  a wrong next-PC report that the fetch path ignores (bit 0 of a `jalr`
  target) passed; the two-port shell now checks every retired record's
  `pc_wdata` against the next record's `pc_rdata` (riscv-formal's `pc_fwd`,
  `PC_WDATA_MISMATCH`), proven by a PicoRV32 adapter self-test (`+selftest=6`)
  and by the planted Aster-core bug that passed before it — a `jalr` target
  keeping bit 0 — now caught. The record after a trap is not checked while a
  trap stops the core; from 18.3 a trap record's `pc_wdata` must be the
  handler's address (see the 18.3 item). (The PicoRV32 look-ahead shell, the
  §7 baseline, does not carry the check.)
- [ ] **By the milestone named:** CSR write point and `minstret` read
  semantics, and the `pc_wdata` check extended to trap records (a trap
  record's `pc_wdata` is the handler's address, matched by the next record)
  (18.3); `fence.i` draining in-flight data accesses and flushing
  F1, F2, the buffer, D and E, and AMO operands (address and data) as hazard
  consumers (18.4); a cover point that `bus_error_behind_load` really holds
  its error in M1 (it depends on the stall seed); the two rv32ui programs
  18.1 skipped, `ma_data` (misaligned accesses trap: 18.3) and `fence_i`
  (18.4)
- [x] **Aster-core shell, 18.1 step 1 — protocol checks (30 September 2026):**
  the two-port shell now enforces the core's side of §4–§5 in every run: a
  fetch presented and not accepted must be presented unchanged in the next
  cycle unless the DUT raises `chk_i_redirect` in that next cycle — the cycle
  whose request is a redirect's target, or in which fetching has stopped —
  `I_REQ_UNSTABLE`; a data request presented and not accepted must be
  presented unchanged in the next cycle, always (a core presenting only when
  M1 can take never needs §4's withdrawal on a flush: after a request waits,
  M1 is empty) — `D_REQ_UNSTABLE`; data byte enables must be an aligned byte,
  halfword or word consistent with the address — `D_REQ_MALFORMED`; never
  more than two data requests in flight — `D_INFLIGHT`, active in a new
  back-pressured mode with room for three (`+max_inflight=3`); and the RVFI
  outputs must be registered (a value sampled after a rising edge may not
  change before the next) — `RVFI_COMBINATIONAL`. The stability rule and the
  byte-enable shapes are unit-tested (`test_shell_ports.cpp`); every check is
  proven end to end on a PicoRV32 adapter that breaks that one rule
  (`+selftest=1…5`, in `make core-ports-tests`, each failing without its
  check); unbroken PicoRV32 passes every mode. `chk_i_redirect` is a
  verification output: the Aster core drives it from its fetch unit.
- [x] **Aster-core shell, with the core (18.1):** exact RVFI byte masks (no
  `word_loads`); the wrapper (`shell_aster_ports.sv`) to the core's own reset
  and interrupt ports (tied off until 18.3); a directed test in which
  wrong-path fetches run off the end of memory and are answered with
  `i_rsp_error` and garbage, which the core discards
- [x] **Error injection (18.1, as trap-halt):** a data-port error on a load,
  on a store and while the access waits in M1, and a right-path fetch outside
  memory, each stopping the core at its `trap_pc` (`+bus_error_traps`, the
  trap-halt programs above)
- [ ] **Aster-core shell, later:** the same errors taken as traps to a
  handler (18.3); from 18.6, the signature read through the cache hierarchy
- [x] **Decode-redirect timing (settled in 18.1):** a `jal` or a backward
  branch redirects on its first cycle in Decode, whether or not it then waits
  for operands (`lw; bne` back to a loop head costs the load-use wait only).
  The CPI model follows the RTL; with it CoreMark's projection rises from
  3.26× to 3.35× and Dhrystone's from 2.65× to 2.67×, the gate's geometric
  mean from 3.665× to 3.683×
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
- [x] **Decided by the owner, 29 September 2026 — SRAM timing plan for 18.6**
  (withdrawn with SKY130, 1 October 2026).
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
  (see "Pre-18.1 measurements"), which led to that decision. Items 2–4 stood
  until the whole plan was withdrawn with SKY130 (1 October 2026).
- [x] 18.1 as in the table above — complete (owner, 1 October 2026)
- [x] **18.2 slow corner — closed: SKY130 dropped (1 October 2026).** At
  10 ns `42a1f1f` met `max_ss` at 36% (+0.038 ns) and missed by 0.094, 0.520,
  0.020 and 0.109 ns at 34, 38, 40 and 44%; at 12.5 ns (the 80 MHz target the
  owner set first) it closed in all five floorplans
- [x] **Decided by the owner, 1 October 2026 — SKY130 dropped.** The FPGA is
  the only implementation target; the SKY130 clock, physical-signoff and
  energy targets are withdrawn (evidence and trade-off in
  [`phase17-plus.md`](phase17-plus.md)); the 18.6 SRAM timing plan, which is
  SKY130's, is withdrawn, and 18.7's feasibility report covers the FPGA
- [ ] **A long-stall memory mode, by 18.6:** the shell's back-pressure adds at
  most two cycles, so a finished division never waits in Execute (its result
  is asserted to hold); 18.6's cache misses will need a mode with long stalls,
  which will exercise it
- [ ] 18.2 … 18.7 as in the table above

## Non-goals

Compressed instructions, supervisor/user modes, an MMU, floating point, dynamic
branch prediction before it is measured to pay, and multi-issue execution
(see [`cpu.md`](cpu.md) §1). SoC integration of the new core is Phase 20.
