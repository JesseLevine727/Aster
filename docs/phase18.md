# Phase 18: Aster core — CPU, L1/SRAM interface, and 100 MHz feasibility

Status: **in progress — milestone 18.0 (tooling) exit gate met; the owner chose
a two-stage memory access (a seven-stage core) and approved the revised cpu.md
§4–§5; the CPU kernels run in the shell and the CPI model projects the 18.7
gate at about 3.7×. Milestone 18.1: the seven-stage RV32I RTL passes
riscv-tests, arch-test I and random programs in lockstep, cycle for cycle with
the CPI model on a memory that answers on time. Timing after the 18.1 timing
work: the core meets 10 ns out of context on the FPGA (105–113 MHz; with a
block RAM behind its ports, 100–112 MHz); on SKY130, in the flow chosen in
18.1, 163–168 MHz typical and 84–88 MHz at the slow corner (PicoRV32 104.6
MHz in the same flow), limited by Execute's datapath — how to close the rest
is the open 18.1 question (see "Milestone 18.1").** The CPU specification this
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
simulation is not practical. The characterization for 18.6 will use a trimmed
netlist (the accessed rows and columns, with the removed cells' loading kept
as capacitance), as OpenRAM's own characterizer does.

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
    plus the 2 directed tests, 42 programs, on the two-cycle memory, the
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
  - eleven trap-halt programs (an illegal instruction, misaligned `lw`, `lh`,
    `lhu`, `sh`, `sw` and jump target, a load and a store the memory answers
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
- **Planted bugs.** On the final RTL, 40 bugs planted in the pipeline (each
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
constraints as the PicoRV32 baseline v2 (`make timing-asic-aster`; default
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

**18.1 timing work after the first report (30 September 2026).** Five RTL
changes, each behavior-neutral (every cycle count unchanged, `make check`
passing, planted bugs in each caught except ones that cannot change
behavior), and four flow experiments. SKY130 runs are post-route at 10 ns in
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
| `p18-aster-wr-nobuf1-rc` | `209ddcb` | **chosen:** without `buf_1`, per-corner wire RC | 163.6 MHz | −1.435 ns | 87.5 MHz | 176,856 |
| `p18-aster-wr-nobuf1-rc-u38` | `209ddcb` | chosen, 38% core utilization | 168.0 MHz | −1.900 ns | 84.0 MHz | 180,670 |
| `p18-aster-sc-rc` | `9ffb3ab` + stall logic off the address alignment | chosen | 163.0 MHz | −1.644 ns | 85.9 MHz | 176,813 |
| `p18-picorv32-nobuf1` | PicoRV32 | baseline without `buf_1` | 147.8 MHz | −3.055 ns | 76.6 MHz | 165,770 |
| `p18-picorv32-nobuf1-rc` | PicoRV32 | chosen | 201.9 MHz | **+0.436 ns** | **104.6 MHz** | 175,177 |

What the runs show. In the baseline flow the typical corner tracks the RTL
changes (134 → 154–158 MHz), but the slow corner moves by up to 0.7 ns
between runs of RTL that only shortens paths: its worst paths are always a
high-fanout control net that the resizer has buffered with chains of `buf_1`
cells — adequate on its wire estimate, slow at signoff — and which net that
is changes with placement (after global routing the resizer estimated about
−2.7 ns where signoff found −4.9 ns, and stopped). Neither a setup margin nor
design repair after global routing helps. Two settings together do, for both
cores: no `buf_1`, and LibreLane's per-corner wire-RC table (alone, the table
fixes slews but costs setup; alone, dropping `buf_1` helps the Aster core and
hurts PicoRV32). **This is the flow the core is reported with from 18.1 on**
(`asic/sky130/config.core_aster.json`; PicoRV32 in the same flow:
`make timing-asic-picorv32-chosen`). In it PicoRV32 closes 10 ns at the slow
corner (104.6 MHz, from 83.5 in the baseline flow), and the Aster core
reaches 84–88 MHz (163–168 MHz typical) across three runs — a 38%-utilization
floorplan moves the slow corner by 0.5 ns, the size of the effect a single
run can resolve. The core's remaining slow-corner limit is Execute's
datapath: the forwarded operand through the 32-bit ALU, compare and adders
into M1 and into the registered redirect target, 1.4–1.9 ns short with few
buffers left on it; §4's two port paths pass (+1.62 and +2.23 ns at
`max_ss`). On the FPGA the RTL changes take the core from 102.0 to 105.6–113.1
MHz out of context (variation across runs), and with the block RAM
100.5–103.7 MHz in the §5 form and 110.0–111.9 MHz with the request
registered. Evidence: [`results/phase18/aster-18.1-timing-work`](results/phase18/aster-18.1-timing-work/README.md).

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
- [x] **Flow correlation (18.1 timing work, 30 September 2026):** the flow
  the Aster core is reported with is chosen — the baseline flow plus
  LibreLane's per-corner wire RC (`LAYERS_RC`, `VIAS_R`) and no `buf_1`
  cells, which closes PicoRV32 at the slow corner (104.6 MHz) and gives the
  Aster core 84–88 MHz there; the setup margin and design repair after
  global routing were measured and rejected. A per-layer fit of the wire RC
  to the signoff extraction was not needed to choose; it stays available if
  18.7's feasibility report needs a tighter correlation
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
- [ ] **By the milestone named:** CSR write point and `minstret` read
  semantics (18.3); `fence.i` draining in-flight data accesses and flushing
  F1, F2, the buffer, D and E, and AMO operands (address and data) as hazard
  consumers (18.4); a cover point that `bus_error_behind_load` really holds
  its error in M1 (it depends on the stall seed)
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
