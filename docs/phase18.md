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
complete; owner, 1 October 2026; `77f7372`): RV32IM passes rv32ui/um, arch-test I and M and random programs in
lockstep, cycle for cycle with the CPI model, and the eight CPU kernels run
on the RTL in lockstep, each window exactly the model's cycles — a measured
3.68× geometric mean over PicoRV32 (lowest 2.59×); the FPGA meets 10 ns
(110.6 MHz; 102.1 MHz with block RAM in the §5 form, 110.6 with the request
registered). On 1 October 2026 the owner dropped the SKY130 ASIC
implementation: the FPGA is the only implementation target (the last SKY130
runs: `42a1f1f` at 10 ns missed the slow corner by up to 0.52 ns in four of
five floorplans, and at 12.5 ns closed in all five; see "Milestone 18.2" and
[`phase17-plus.md`](phase17-plus.md)). Milestone 18.3 (Zicsr, traps,
interrupts, counters; complete, owner, 2 October 2026; `9cbd3c1`):
rv32ui/um/mi, arch-test I, M and privilege, directed traps
from every stage and random programs with exceptions pass in lockstep, cycle
for cycle with the CPI model; interrupts are checked by self-checking tests
and by about 23,000 random interrupts whose streams, with the handlers cut
out, equal Spike's; the kernels are unchanged; `time`/`timeh` read the
core's own time counter (the owner's choice at sign-off); the FPGA meets
10 ns (107.7 MHz; 101.8 and 104.6 MHz with block RAM; see "Milestone
18.3"). Milestone 18.4 (the A extension, `fence`, `fence.i`; complete,
owner, 2 October 2026; `d53c46e`): rv32ua,
`fence_i`, arch-test A and Zifencei, directed atomics and self-modifying-code
tests and random programs with atomics and fences pass in lockstep, cycle for
cycle with the CPI model, with the shell answering each `sc` as Spike did;
about 60,000 random interrupts, now over the directed tests too; the kernels
are unchanged; the FPGA meets 10 ns (108.3 MHz; 104.7 and 108.0 MHz with
block RAM; see "Milestone 18.4"). Milestone 18.5 (Xasterdot8; complete,
owner, 2 October 2026; `b087ab2`): v1's
DOT8 reference tests on the core — the unit test's exhaustive arithmetic,
the probe's register-field matrix and illegal encodings, the runtime's
dot/FIR/GEMM jobs — with a Spike extension putting the DOT8 programs in
lockstep (the exhaustive arithmetic runs self-checking in the shell alone,
and ran once in lockstep); the kernels are unchanged and v1's DOT8 Conv2D runs, matching its
baseline; the FPGA meets 10 ns (103.3 MHz; 101.6 and 102.4 MHz with block
RAM; see "Milestone 18.5"). ACT4 (riscv-arch-test 4.1.0, checked against the
Sail model) adopted after 18.5: 102/102 programs (97 testing this machine-mode
core) in six memory modes, once
`time`/`timeh` read the platform's `mtime` (owner decision; FPGA 105.1 MHz,
105.3 and 105.0 MHz with block RAM; see "ACT4 adopted").** The CPU specification this
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
asynchronous: it is checked by self-checking directed tests run in the shell
alone, and (from 18.3) by random interrupts over lockstep programs whose
stream, with each interrupt handler cut out, must equal Spike's uninterrupted
run. The extensions the comparison needs for traps and CSRs (18.3, in place:
"Milestone 18.3"), atomics (18.4), and Xasterdot8 (18.5) are specified in
[`cpu.md`](cpu.md) §6.

The harness is trusted only after it **rejects deliberately corrupted runs**:
`make core-lockstep-selftest` edits the raw trace text of a real run (PC,
instruction, trap, destination register and value, memory address, store byte
lane and mask shape, store data; dropped, duplicated, missing, extra, and
truncated records; a missing `tohost` store) and the raw Spike log (register
value, store data, memory address, PC), re-parses both, and requires every
corruption to be caught — 19 of 19. From 18.3 the same run on a trapping,
CSR-writing program (`traps/decode_traps`, in `make core-aster-tests`) adds a
CSR write's value, a missing and an extra CSR write, a trap's mcause, mepc and
mtval, a dropped and an extra trap record, and Spike's CSR value, trap cause,
epc and tval — 31 of 31.

### Programs

- `riscv-tests` (vendored): rv32ui, rv32um, rv32ua, plus the machine-mode tests
  that apply (rv32mi, from 18.3).
- `riscv-arch-test` 3.10.0 (vendored): I, M, A, Zifencei, and the privilege
  tests; each program runs in lockstep and its signature region must equal
  Spike's word for word. (Release 4.1.0, ACT4, builds self-checking programs
  from the Sail model; it was to be reconsidered at 18.3, when the core's
  configuration is final — see `vendor/riscv-arch-test/UPSTREAM.md`. At 18.3
  it was not adopted: the ISA was not final until 18.5 (A in 18.4, Xasterdot8
  in 18.5), and ACT4 needs a framework, Ruby/UDB and Sail not installed here;
  the owner chose to reconsider it at 18.5.)
- `riscv-arch-test` 4.1.0 (ACT4), adopted after 18.5 (2 October 2026; not
  vendored, docs/toolchain.md): 102 self-checking programs built for the
  core's configuration (`verification/core/act4`; five of them test
  supervisor features and run only their harness here), their expected
  results from the Sail model, run in the shell (`make core-aster-act4`;
  "ACT4 adopted").
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

The Aster core implements RV32IM (`4f42794`, revised for timing in `d74408d`,
`bdfa836` and `42a1f1f`; the multiplier back on the FPGA's DSP blocks in
`77f7372`):

- **Multiply** (`mul`, `mulh`, `mulhsu`, `mulhu`), pipelined over Execute, M1
  and M2 as §4 says. The operands travel with the instruction; M1 forms four
  17×17 signed partial products of the 33-bit sign- or zero-extended operands
  (the low halves zero-extended), which the FPGA maps to 4 DSP blocks,
  registered at its end; M2 adds them into the 64-bit product, whose low or
  high word is registered into W. (For SKY130, `bdfa836` wrote the multiplier
  out as radix-4 Booth partial products compressed by carry-save adders, M1 to
  M2; with SKY130 dropped, `77f7372` returned to the DSP form.) The result is
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
  (in 18.3 the case moved into `traps/m1_traps`, which takes the trap; with
  its layout the division runs before the kill with stall seed 2, seed 9 on
  the one-cycle memory and seed 7 with room for three or four requests, and
  seeds 2 and 5 hold the error in M1 — measured with cover counters in an
  instrumented copy of the RTL);
- assertions: a squashed division never starts, the divider runs only for
  Execute's division, a division leaves Execute only when done, a multiply
  never waits in M2, and a finished division's result holds while it waits
  (the shell's memory never stalls long enough for that today; 18.6's cache
  misses will);
- planted bugs with the assertions off, over the versions: 36 of 44 caught
  (25 of 33 through `bdfa836`, the carry-save multiplier's six all by rv32um —
  `4f42794`'s message counts 15 of 21 for the first two rounds, which were 14 —
  and `42a1f1f`'s six and `77f7372`'s five, all caught); the eight survivors are
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

**The multiplier on the DSP blocks again (`77f7372`; 18.2 complete).** With
SKY130 dropped, the carry-save multiplier written for it cost the FPGA about
1,150 LUTs and used no DSP block; the owner chose to switch back. `77f7372`
forms four 17×17 signed partial products in M1 (mapped to 4 DSP blocks),
registered when M1 holds a multiply, and adds them in M2 — the multiplier of
`4f42794`, unchanged. It is behavior-neutral (`make check`,
every Aster suite, the kernels cycle-exact; five planted bugs in the
multiplier, all caught by rv32um). On the FPGA it runs at 110.6 MHz out of
context, 102.1 MHz with the block RAM in the §5 form and 110.6 MHz with the
request registered (2,105 LUTs and 4 DSPs, against `42a1f1f`'s 108.0, 102.8
and 106.1 MHz with 3,248 LUTs). The owner declared 18.2 complete on 1 October
2026.

### Milestone 18.3: Zicsr, traps, interrupts and counters (2 October 2026)

The Aster core takes its traps (`705b5f9`): RV32IM with Zicsr, `ecall`,
`ebreak`, `mret` and `wfi` (a no-op), the machine-mode CSRs of
[`cpu.md`](cpu.md) §3, machine-mode traps and interrupts, and the counters.
The choices made in building it are listed in §9 ("Clarifications during
milestone 18.3"), which the owner accepted at sign-off (below):

- **Traps** commit at the end of M1, as §4 says: mepc, mcause, mtval and
  mstatus (`MPIE` = `MIE`, `MIE` = 0) are written, the younger instructions are
  killed, and the fetch unit is sent to `mtvec` through the registered
  Execute redirect's path, which a trap overrides — a branch that redirects
  from Execute and then traps at the end of its M1 cycle replaces its own
  target (the fetch unit's maximum in flight becomes twelve). mtval is the
  access's address, the jump or branch target, the instruction (an illegal
  16-bit encoding's 16 bits, as Spike reports them), the PC (`ebreak`, a
  fetch fault) or 0. The trapping instruction retires as a trap record whose
  `rvfi_pc_wdata` is the handler's address; the handler's first instruction
  carries `rvfi_intr`. A trap costs 5 cycles from its last Execute cycle to
  the handler's first Decode cycle.
- **Interrupts** (`MEIP`, `MTIP`, `MSIP`, registered from the inputs, enabled
  by `mie` and `mstatus.MIE`; priority `MEI`, `MSI`, `MTI`) are taken at the
  commit point in a cycle in which M1 holds a valid instruction: it completes,
  mepc receives its next PC, and the younger instructions are killed — so no
  accepted access is ever repeated. None is taken while M1 holds a CSR
  instruction or `mret`; the instruction after it can be interrupted.
- **CSRs.** A CSR instruction or `mret` is serializing: Execute holds it until
  M1 is empty (one cycle behind an instruction in M1), so it reads every older
  instruction's effects and `minstret` counts exactly the older instructions.
  Its write (and `mret`'s mstatus update, computed in Execute) takes place,
  and it retires, at the end of its first cycle in M1, where nothing can kill
  it — so its write enables see no late signal, a write to `minstret`
  suppresses the writer's own increment and one to `mcountinhibit` applies to
  the instructions after it, as in Spike, whatever the memory's timing; the
  RTL asserts that a CSR write in M1 never meets a trap or an interrupt and
  that a serializing instruction never traps. `mret` redirects from Execute
  to `mepc` like a `jalr`. Beyond §3's table the core has `mcountinhibit`,
  `mstatush` and `mconfigptr`, and the hardware performance monitor
  (`mhpmcounter3`–`31(h)`, `mhpmevent3`–`31`) as zero; `time`/`timeh` were
  left out here, and added at sign-off as the core's own time counter (below).
- **RVFI** reports every CSR an instruction writes, and the reads of those
  CSRs (riscv-formal's `rvfi_csr_*` fields for the eight CSRs §5 names, and
  for `mstatush`, `misa`, `mcountinhibit` and the 64-bit counters; the
  identity CSRs and the hardware performance monitor have none), with the
  value written as it reads back; a trap record reports mepc, mcause, mtval and mstatus as the trap
  wrote them (carried with the record), and `mret` its mstatus and mstatush.

Verification (`make core-aster-tests`, `make core-aster-fetch`, both in
`make check`):

- **The lockstep extensions of §6.** Spike runs as the core is configured —
  `--priv=m --pmpregions=0 --triggers=0 --wfi-as-nop`,
  `--isa=rv32im_zicsr_zicntr` — and with `-l`, whose `exception …, epc …` and
  `tval` lines become trap records compared with the core's (PC, instruction,
  mcause, mepc, mtval); every CSR write is compared through the RVFI CSR
  fields. Allowlisted by name: the values read from `mcycle(h)`, `cycle(h)`,
  `time(h)`, `marchid` and `mip`, and the value written to `mip` (Spike's
  CLINT holds `MTIP` high from reset). The environment zeroes `minstret`.
  Spike counts its instruction budget in steps of 5,000 that a trap ends
  early, so the runner allows a step per trap. The shell's `rvfi_pc_wdata`
  check now covers trap records (the next record must start at the handler)
  and exempts only an interrupt's handler entry; the comparator requires
  `rvfi_intr` on exactly the records after a trap record; and the shell now
  matches every load the memory accepts to one retired load, so a load
  performed twice — an I/O load repeated after an interrupt, which §4
  forbids and a stream comparison cannot see — fails (proven by
  `+duplicate_read`, in `make core-aster-tests` and `make core-ports-tests`).
  The comparator rejects every
  injected corruption of a trapping, CSR-writing run — 31 of 31 on
  `traps/decode_traps`, the 19 of 18.0 plus a CSR value, a missing and an
  extra CSR write, a trap's mcause, mepc and mtval, a dropped and an extra
  trap record, and Spike's CSR value, cause, epc and tval.
- **Conformance, in lockstep and by signature, cycle for cycle with the CPI
  model** (which now models the serialization, the trap redirect, and an
  illegal instruction that reads no register): riscv-tests rv32ui (41, with
  `ma_data`: the environment's trap handler emulates a misaligned access, on
  the core and in Spike alike), rv32um (8) and rv32mi (14 of 16, vendored
  from the pinned revision; `breakpoint` needs debug triggers and `pmpaddr` a
  PMP, which §3 leaves out); riscv-arch-test I, M and privilege (62/62; the
  runner selects each program's `RVTEST_CASE` for this core as riscof would:
  no C, Zicsr, no misaligned-access hardware). An arch-test run found that an
  illegal 16-bit encoding's mtval was the whole word (fixed).
- **Directed traps in every stage** (`verification/core/traps`, lockstep, each
  also checking its own trap log): Decode (`decode_traps`: illegal encodings
  of every kind — an all-zero and an all-ones word, a 16-bit encoding, a
  reserved shift, an unimplemented CSR, writes to read-only CSRs, `sret`,
  `sfence.vma` — and `ecall`/`ebreak`, next to a load-use stall, a multiply,
  a Decode redirect and stores on both sides); Execute (`execute_traps`:
  misaligned loads and stores with forwarded and loaded addresses, misaligned
  `jalr`, `jal` and branch targets in both prediction directions); M1
  (`m1_traps`: data-port errors on loads and stores, one held in M1 behind a
  slow load, with a younger store, CSR write, division and multiply killed and
  never taking effect); Fetch (`fetch_traps`: right-path fetches outside
  memory after a `jalr`, a `jal`, a taken branch and running off the end of
  memory); and CSR ordering (`csr_ordering`: read-modify-write chains, sources
  forwarded from ALU, load, multiply and division, CSR results forwarded at
  distances 1–4 to every consumer, `minstret` across writes, traps and
  `mcountinhibit`, `mret` and `mtvec` changes right before use). They replace
  the 18.1/18.2 trap-halt programs, whose cases they all keep.
- **Interrupts in every pipeline state.** Self-checking programs in the shell
  alone, with an interrupt device (`+irq_device`) whose store sets the lines
  after a chosen delay: each line, priority, `wfi`, enabling by `csrs mie`,
  `csrsi mstatus` and `mret` (taken after the next instruction, mepc exact),
  an exception and an interrupt at the same instruction (`irq_lines`,
  `irq_enable`), and `irq_sweep` — one interrupt at each of 128 arrival
  times over a block of CSR writes, loads, stores, a multiply, a division,
  branches, `jal` and `jalr`, every result checked. Random interrupts over
  lockstep programs (`--interrupts`): MEIP and MSIP raised at random while
  rv32ui, rv32um and random programs run (the random ones with the CSR
  instructions an interrupt leaves alone); each run's stream with every
  handler cut out must equal Spike's uninterrupted run — every instruction
  executed exactly once and in order, whatever the interrupts hit — and every
  pair of (interrupted, next) instruction classes among ALU, load, store,
  branch, jump, multiply and divide must occur: 23,291 interrupts in `make
  core-aster-tests` (1,192 over rv32ui/rv32um, which reach 18 of the 49
  pairs; 6,345, 9,507 and 6,247 over random programs on the two-cycle
  memory, back-pressured and the one-cycle memory, each reaching all 49).
- **Constrained-random programs** (`rvgen`) now also emit CSR instructions in
  every form and exceptions (`ecall`, `ebreak`, illegal words, misaligned
  accesses, data-port errors, misaligned jump and branch targets), with
  hazard coverage extended to CSR results and CSR sources (152/152 bins); 240
  programs matched the CPI model cycle for cycle on both memories (one, seed
  204, first exposed the model's operand wait for an illegal instruction).
- **Fetch unit, on its own:** trap redirects in any cycle, including while an
  Execute redirect's target is presented; the deepest state is now twelve
  fetches in flight, nine discarded.
- **The CPU kernels** run unchanged and cycle-exact (`make
  core-aster-kernels`): every window's CPI is 18.2's.
- **Planted bugs** (assertions off), in trap entry, mcause and mtval
  selection, the trap and interrupt redirects, interrupt gating, priority and
  enabling, the CSR decode, serialization and write, `mret`, the counters,
  `mip`'s layout, the trap records' RVFI values and the fetch unit's
  trap-while-presented path: 48, 44 caught. The four not caught are equivalent or unobservable:
  Execute advancing in the cycle an interrupt is taken (the kill overrides all
  it loads); gating a CSR write by the instruction's trap (no CSR-writing
  instruction traps); the CSR write repeated in every M1 cycle (with the
  writer counted at its first M1 edge, writing the same value again changes
  no architectural state; `mcycle`'s exact count is the implementation's);
  and a trap record's RVFI mstatus (Spike's log says nothing of it; the
  mstatus a trap leaves is checked through the handlers' reads). The first
  round missed two real ones — mtval left non-zero
  by an interrupt, and an interrupt taken while a CSR write was in M1 (the
  trap entry would have won over the write) — and the tests were extended:
  the interrupt handler now records mtval, the sweep's block writes CSRs, and
  the random interrupt runs include CSR writes.
- **Review.** The milestone's watchdog review found one RTL bug, which the
  tests above had missed: CSR writes then took place in every cycle the
  instruction was in M1 and its own retirement was counted when it left, so a
  `mcountinhibit` write right behind a load that M1 waited on changed whether
  the writer counted itself (and `mcycle` stood still while it waited) — a
  `minstret` difference with Spike under back-pressure. Fixed by the write and
  the count at the end of the first M1 cycle; `csr_ordering` now writes
  `mcountinhibit`, `minstret`, `minstreth` and `mcycle` behind loads and
  stores (a second review confirmed with a cover point that such writes wait
  in M1, and matched Spike on a wider program of its own over eight stall
  seeds) and checks `mcycle` counting and carrying. It also found the
  comparator's exception pattern unable to read Spike's interrupt lines
  (unused so far: Spike takes no interrupt in these runs; fixed), the missing
  load accounting (added), the `mip` layout unchecked (now read with each
  line alone, which catches a swapped layout), and the trap records' RVFI
  values read from the live registers, which would be wrong once a handler
  writes them before the record leaves W. They are now carried with the
  record; no test can tell the difference yet, since the shell's memory
  always answers before a handler's first CSR write — the long-stall mode of
  18.6 will (checklist).
- The PicoRV32 suites pass unchanged; the shells' self-tests pass, with the
  new `+duplicate_read` (a load performed twice) on both DUTs.

**Timing** (FPGA, out of context, 10 ns; `705b5f9`): the core alone
meets it at 105.4 MHz (+0.514 ns; 2,695 LUTs, 1,287 flip-flops, 4 DSPs,
against 18.2's 110.6 MHz and 2,105 LUTs), with the block RAM in the §5 form
at 100.6 MHz (+0.055 ns — thin, as in 18.2, where it was +0.207) and with
the request registered at 104.8 MHz (+0.462 ns). The core alone and the §5
form keep 18.2's worst paths (a forwarded operand through the adders into
M1; the store's forwarded base through the address adder and the block RAM's
range decode into its write enable). The request-registered form's worst
path is new: from the instruction in Decode through the CSR address's
legality decode (12 bits, new in 18.3) into `illegal` — an illegal
instruction reads no register — and on through the load-use hazard and
Decode's advance into the fetch buffer's count (9 LUT levels); its next is
into M1's CSR write flag (+0.667 ns). Should timing tighten, Decode could
compute operand use without the legality decode (the CPI model's operand
rule would follow). (A run of the same design before the review's fixes, which did not
touch these paths, gave +0.673, +0.448 and +0.460 ns: Vivado's placement
varies by a few hundred picoseconds from one netlist to the next.) The paths
§4 names keep their margin: `d_rsp_valid` to the next request +2.866 ns, the
`d_rsp_error` kill +2.451 ns (§5 form). Evidence:
[`results/phase18/aster-18.3`](results/phase18/aster-18.3/README.md).

**Signed off; the time counter (`9cbd3c1`).** The owner signed off 18.3
on 2 October 2026, accepting the added CSRs and the conformance standing in
for "arch-test Zicsr" (cpu.md §9), and choosing to reconsider ACT4 at 18.5.
For `time`/`timeh`, of three options — leave them out (the SoC timer is
memory-mapped), alias `mcycle` (which software can write or stop), or give
them their own counter — the owner chose the last: `time`/`timeh` read a
64-bit counter that ticks once per clock from reset and is never written or
stopped (`mcountinhibit` does not touch it); writing them is illegal. A
self-checking program in the shell alone (`interrupts/time_counter`) checks
that it advances by at least the cycles a loop takes, runs on while
`mcountinhibit` stops the other counters, ignores `mcycle` writes and has
`timeh` 0; in lockstep `csr_ordering` reads them (their values are
allowlisted: Spike's time moves at its own rate) and `decode_traps` writes
`time` (illegal in both). Three planted bugs — a stuck counter, `timeh`
reading the low half, the counter obeying `mcountinhibit` — are caught
(51 planted bugs in all, 47 caught). `make check` passes (284 PASS
lines). On the FPGA it meets 10 ns: the core alone at 107.7 MHz (+0.716 ns;
2,736 LUTs, 1,353 flip-flops), the §5 form at 101.8 MHz (+0.177 ns) and the
request-registered form at 104.6 MHz (+0.439 ns; its worst path is a forwarded
operand's, from `fsel2`, as in 18.2 — not Decode's CSR decode, as at `705b5f9`) (evidence:
[`results/phase18/aster-18.3-time`](results/phase18/aster-18.3-time/README.md)).

### Milestone 18.4: the A extension, `fence` and `fence.i` (2 October 2026)

The Aster core is RV32IMA with Zicsr and Zifencei (`d53c46e`). The choices
made in building it are listed in [`cpu.md`](cpu.md) §9 ("Clarifications
during milestone 18.4"), which the owner accepted at sign-off (below):

- **Atomics.** `lr.w`, `sc.w` and the nine AMOs decode as a load and a store
  (`lr` as a load) and go to the data port as one request with §5's
  `d_req_op` (2 `lr`, 3 `sc`, 4–12 the AMOs); the memory side performs them
  and holds the reservation. Their result is a late result, forwarded from W
  like a load's (load-use 2/1 cycles), and both the address and the AMO's
  data are operands forwarded like a store's. A misaligned `lr` raises a
  load-misaligned exception and a misaligned `sc` or AMO a
  store/AMO-misaligned one in Execute, before the port; an error answer is a
  load (`lr`) or store/AMO access fault at the commit point, as a load's or
  store's. `lr.w` with a non-zero `rs2` field and the `.d` forms are illegal.
  `aq` and `rl` need no action: one data port, accesses in program order.
  `misa` reads `A`.
- **`fence.i`** waits in Execute until M1 and M2 are empty — every older data
  access has been answered — and then redirects to the next instruction
  through the registered Execute redirect, discarding everything fetched
  after it (up to 2 cycles, then 4). `fence` is free.
- **RVFI.** An AMO reports its read (the old value) and its write (the value
  the memory computed, recomputed from the old value and `rs2`); `lr` a read;
  a successful `sc` a write and a failed `sc` no memory access.

Verification (`make core-aster-tests`, in `make check`):

- **The memory side, in the shell.** The shell performs the atomics as §5's
  memory side does and holds the reservation as Spike does (the hart's own
  stores and interrupts leave it; an exception and every `sc` end it). Spike
  also ends a reservation at its own step boundaries, so the runner reads
  Spike's `sc` outcomes and hands them to the shell, which answers each `sc`
  as Spike's did and fails the run (`SC_MISMATCH`) where Spike's `sc`
  succeeded and its own reservation did not hold, or the counts differ
  (proven in `make core-aster-tests` with every `sc` claimed successful). The
  shell's instruction fetches now see a data write only after the edge at
  which the core takes its answer — §5 orders accesses within a port only —
  so a `fence.i` that did not wait for the older stores fetches the old
  instruction.
- **Conformance, in lockstep and by signature, cycle for cycle with the CPI
  model** (which now models `fence.i`): riscv-tests rv32ua (10; `lrsc` runs
  1,029 `sc`s, four of them failing in Spike) and rv32ui `fence_i` (now
  run), with rv32ui, rv32um, rv32mi, the directed tests and the directed
  traps, and the four self-checking interrupt programs in the shell alone —
  89 tests in six memory modes; riscv-arch-test A (9) and Zifencei (1) with I, M and
  privilege, 72/72 on both memories.
- **Directed atomics** (`directed/atomics`, lockstep and self-checking):
  addresses forwarded from an ALU result and a load (load-use), AMO data from
  a multiply, a division and a load; AMO, `lr` and `sc` results forwarded at
  distance 1 to an ALU, a branch, a store's data and the next AMO's address;
  every AMO at the sign and wrap corners; `rd` = x0, `rs1` and `rs2`; `sc`
  succeeding, failing with no reservation and on another word (ending the
  reservation), a second `lr` moving the reservation, work and the hart's own
  store between `lr` and `sc`; and single-hart ordering — a store, an AMO and
  a load to one word observing each other in program order. Traps:
  misaligned `lr`, `sc` and AMO (`execute_traps`), each as a port error
  (`m1_traps`, `rd` unchanged), and `lr.w` with `rs2` ≠ 0 and `amoadd.d`
  illegal, a `fence.i` with a non-zero immediate executed
  (`decode_traps`). The environment's trap handler fails a misaligned atomic
  instead of emulating it.
- **Self-modifying code** (`directed/smc`, lockstep and self-checking): an
  instruction rewritten right behind its store and run after `fence.i`, a
  loop rewriting its own body 40 times, and a rewrite behind a load, in every
  memory mode and under random interrupts. Of three planted `fence.i` bugs,
  no redirect fails the program in all six memory modes; no wait at all fails
  it on the two-cycle memory and in all four back-pressured modes, and the
  cycle count (the CPI check) on the one-cycle memory, where the store is
  always answered before the refetch; waiting for M1 alone fails it in all
  four back-pressured modes, and the cycle count on both unstalled memories.
- **Constrained-random programs** now emit atomics (AMOs on fresh and
  forwarded addresses, `lr`/`sc` pairs with work between, `sc`s to the other
  word and alone, pointers read from memory by an AMO or `lr` and used as
  addresses), `fence.i` and `fence` in its forms; the hazard coverage counts
  atomic results as a producer class of their own and the AMO address and
  data as consumers: 220/220 bins in each of the three modes (172/172 under
  random interrupts), and 240 more programs matched the CPI model cycle for
  cycle on both memories. (A register the generator reserved for an `sc` to
  another word was never released, shrinking its pool; found by the second
  review, fixed, and now a host test.)
- **Random interrupts**, with atomics and fences as two more instruction
  classes (81 pairs): the shell now raises one about every 20 cycles (was 40,
  which left one or two pairs uncovered); 60,441 interrupts in `make
  core-aster-tests` (4,801 over rv32ui, rv32um, rv32ua and now the
  directed tests — `atomics` and `smc` among them — reaching 32 of the
  pairs; 14,835, 25,318 and 15,487 over random programs on the two-cycle memory,
  back-pressured and the one-cycle memory, each reaching all 81), every run's
  stream with its handlers cut out equal to Spike's.
- **The CPU kernels** run unchanged and cycle-exact (`make
  core-aster-kernels`). The PicoRV32 suites pass, with `directed/atomics`
  and `directed/smc` skipped with their reasons (no native A, no Zifencei);
  its random programs differ (multiply and divide results are now also used
  as data on purpose) and keep full coverage.
- **Planted bugs** (assertions off): twelve new ones in the atomics' decode
  and RVFI, `fence.i`'s decode, redirect, target and drain, and `misa`, all
  caught; with 18.3's, 63 planted, 59 caught (the four 18.3 accepted as
  equivalent or unobservable). The comparator rejects 19 of 19 injected
  corruptions of an AMO run (`rv32ua/amoadd_w`).
- **Review.** The milestone's watchdog review found no functional bug; it
  ran its own `fence.i` and atomics programs (`fence.i` behind a load, a
  multiply, a division, a CSR write and an AMO, back to back and at a loop's
  head; code rewritten by `amoadd`, `sc`, `sh` and `sb`) in five memory modes
  and under interrupts, and nine planted bugs of its own, all but an
  equivalent one caught. It found the tests weaker than they should be in
  four places, all fixed: the shell let a fetch at the very edge of a
  store's answer see the new word, so on the two-cycle memory a `fence.i`
  that did not wait passed every program (it now fails `smc`); random
  programs had no `fence` or `fence.i`, and the directed tests never ran
  under interrupts (both added, with fences an interrupt class); atomic
  results counted as loads in the hazard coverage, so no bin required one to
  be forwarded (now a class of their own, used as data and as addresses);
  and the self-test of `SC_MISMATCH` relied on the simulator taking the
  first of two `+sc_outcomes` (the runner now leaves a given one alone). It
  also noted that the shell's request-stability check still compared an
  `lr`'s don't-care write data (relaxed, as the comment said), and that the
  shell ends a reservation when the trap record retires, which a handler's
  `lr` accepted earlier could in principle precede (none does; it would be a
  false `SC_MISMATCH`, not a false pass), and that recording a written
  word's old value read the I/O page's clock (now recorded only where the
  core can fetch).

**Timing** (FPGA, out of context, 10 ns; `d53c46e`): the core alone
meets it at 108.3 MHz (+0.763 ns; 2,808 LUTs, 1,359 flip-flops, 4 DSPs,
against 18.3's 107.7 MHz and 2,736 LUTs at sign-off), with the block RAM in
the §5 form at 104.7 MHz (+0.448 ns) and with the request registered at
108.0 MHz (+0.745 ns). No path particular to the atomics or `fence.i` is
the worst (their decode and request logic is shared with the loads and
stores); the worst are of 18.3's kinds: in the core alone, from `mie`
through the interrupt decision and the kill into Execute's instruction
register (7 LUT levels); in the §5 form,
18.2's forwarded operand through the address adder and the block RAM's range
decode into its write enable; with the request registered, from the
instruction in Decode through its hazard check into Decode's advance (the
enable of the predecoded redirect target; 8 levels), much as at 18.3's
`705b5f9`. Of the paths §4
names, `d_rsp_valid` to the next request has +3.455 ns (18.3: +2.881 ns) and
the `d_rsp_error` kill +1.622 ns (18.3: +2.227 ns; six LUT levels against
five, from the error through the kill and Execute's advance into the block
RAM's write enable). Evidence:
[`results/phase18/aster-18.4`](results/phase18/aster-18.4/README.md).

**Signed off.** The owner signed off 18.4 on 2 October 2026, accepting
cpu.md §9's 18.4 clarifications: litmus tests single-hart until the Phase 20
coherent SoC puts two cores on shared memory, and the reservation as the
memory side's — the shell following Spike (the hart's own stores and
interrupts keep it; an exception and every `sc` end it), the v1 fabric's
rule (any store to the word ends it) to be kept or documented by the
18.6/Phase 20 memory side.

### Milestone 18.5: Xasterdot8 (2 October 2026)

The Aster core runs Xasterdot8 (`b087ab2`): `dot8 rd, rs1, rs2` — custom-0,
funct3 0, funct7 0, v1's encoding — writes the sum of the four products of the
operands' signed bytes, sign-extended. The choices made in building it are
listed in [`cpu.md`](cpu.md) §9 ("Clarifications during milestone 18.5"),
which the owner accepted at sign-off (below):

- **In the pipeline.** The products and their sum are computed in M1 from the
  operands Execute passes on, and the value enters M2 with the instruction,
  so it is forwarded from M2 and W: a reader waits one cycle in Decode at
  distance 1, as cpu.md §4's hazard table says (and, should M1 wait on the
  memory, a reader in Execute waits for it). The four 8×8 products are DSP
  blocks and the sum is in the fabric (below). Every other custom-0 encoding,
  and custom-1, -2 and -3, are illegal; `misa`'s X bit is set.
- **In Spike** (`verification/core/spike/aster_dot8.cc`, built by `make
  core-aster-tests`): the instruction, from integer arithmetic, enabled by
  `_xasterdot8` in Spike's ISA string (which sets `misa.X`). Spike treats
  any custom extension as a RoCC coprocessor, making `mie` bit 12 (its
  interrupt) and `mstatus.XS` (its state, and SD with it) writable; the core
  has neither, so the extension keeps those bits clear (`traps/csr_ordering`
  checks them; against the extension without that, it fails). Every
  Aster-core run now loads it.

Verification (`make core-aster-tests`, `make core-aster-kernels`, both in
`make check`) — v1's DOT8 reference tests, as they apply to a core:

- **v1's unit test** (`verification/unit/tb_aster_pcpi_dot8.cpp`):
  `selfcheck/dot8_arith`, in a new suite of self-checking programs run in
  the shell alone (its 2.6 million instructions are too many to log on every
  run), checks dot8 against a reference computed with `mul` (itself checked
  in lockstep): every pair of signed bytes in every lane with the other lanes
  0 (4 × 65,536), every pair of 12 corner words, and 6,000 pseudo-random
  pairs, in every memory mode. Run once in lockstep as well (a 270 MB Spike
  log), all 2,645,248 of its instructions matched Spike, cycle for cycle
  with the CPI model.
- **v1's DOT8 runtime jobs** (`software/tests/dot8_runtime.c`), in lockstep:
  `c/dot8_jobs`, the first of a new suite of C programs in the shell (a
  start-up, `main`, and the v1 sources it needs), runs the dot, FIR and GEMM
  kernels of `software/benchmarks/dot8_kernels.c`, scalar and Xasterdot8,
  over every alignment of their two inputs (4 × 4) and sizes from empty
  through the scalar tails and the packed loop — 416 jobs, each with its own
  data as in v1 (zero, −128, −128 against 127, alternating, or
  pseudo-random, the last on half the alignments at every size, so every
  alignment of each input meets random data), each result against an
  independent scalar reference, with guard words around the outputs and the
  inputs checked unchanged — then v1's sixteen `lr.w`, dot8, `sc.w`
  sequences and a dot8 into x0 (1,114,993 instructions, cycle for cycle
  with the CPI model). v1 runs 736 jobs, on two harts beside DMA; the
  sizes here are trimmed (dot to 64, FIR and GEMM to 13) to keep the Spike
  log near 100 MB; the multi-hart and DMA parts belong to the SoC
  (Phase 20).
- **v1's probe matrix** (`verification/unit/tb_aster_dot8_probe.cpp`), in
  lockstep: every (rd, rs1, rs2) combination — 32,768 dot8s, four programs
  (`directed/dot8_fields_0`–`3`; the matrix exceeds the shell's 96 KiB),
  each rd's results feeding the next at distance 1 — and every illegal
  custom encoding (`traps/dot8_traps`: the 1,023 custom-0 words with
  (funct7, funct3) ≠ (0, 0), PicoRV32's `retirq`/`maskirq`/`waitirq` among
  them, and 30 custom-1/-2/-3 words; each trap record matches Spike's and the
  handler checks mcause and mtval).
- **The pipeline's own cases** (`directed/dot8`, lockstep and
  self-checking): operands from an ALU result, a load, a multiply, a
  division, an AMO, `lr` and a link value at distance 1; the result to an
  ALU, a branch, a store's data, a multiply and the next dot8 at distances
  1–4; chains with rd = rs1 and rd = rs2; x0 as rd and operands; `lr.w`,
  dot8, `sc.w` (the reservation survives); dot8 behind a load the memory may
  answer late; the corners (−128 × −128 in every lane, 65,536, needs 18 bits).
- **A v1 DOT8 workload:** the coherent Conv2D engine built for Xasterdot8
  (`conv2d_dot8`, Phase 17 capture `conv2d_dot8`) runs in the shell outside
  the gate, in lockstep (1.18 million instructions), its window and checksum
  equal to the baseline record's and its cycles to the CPI model's (CPI
  1.193). It is no faster than the scalar engine on this core (1,365,828
  window cycles against 1,356,221, with 18% more instructions), nor was it
  on v1's SoC (10.34 against 9.73 million cycles, the baseline's sync1
  records) — the engine's DOT8 path
  packs bytes at a cost close to the multiplies it saves (cpu.md §9).
- **Constrained-random programs** now emit dot8 (random and corner bytes),
  with its result a hazard producer and its operands a consumer (276/276
  bins in each of the three modes), and a corner word as its first operand
  at times; fences are now 3% of the stream (were 2%), which the dot8 and
  fence interrupt pairs needed; under random interrupts dot8 is a tenth
  instruction class: 62,629 interrupts in `make core-aster-tests`
  (8,623 over rv32ui, rv32um, rv32ua and the directed tests, the dot8
  programs among them; 14,822, 24,282 and 14,902 over random programs, each reaching all 100
  (interrupted, next) pairs), every stream with its handlers cut out equal to
  Spike's.
- **Conformance** is unchanged — 97 tests in six memory modes (the eight
  new ones above among them; five self-checking in the shell alone),
  arch-test 72/72 on both memories — with `misa` now X; the CPU kernels run
  unchanged and cycle-exact; the PicoRV32 suites pass, skipping the dot8
  directed tests (the shell's PicoRV32 has no Xasterdot8).
- **Planted bugs** (assertions off): thirteen new ones — the dot8 interlock
  removed in Decode, added in M1, missing from M1's `late` or kept in M2's;
  the value not written to M2, not sign-extended or cut to 17 bits; a lane
  unsigned, swapped or dropped; funct3 or funct7 ignored; `misa` without X —
  all caught (one first written as a sign-extension bug turned out
  equivalent: a size cast of a signed value sign-extends, so it was replaced
  by a zero-extension). With 18.4's, 76 planted, 72 caught (18.3's four).
- **Review.** The milestone's watchdog review found no RTL bug: it traced
  every forwarding and stall path for a dot8 (M1 waiting, kills, interrupts,
  x0, back-to-back writers), and two planted bugs of its own — the dot8 not
  late in M1, its value dropped on a kill — were caught (under back-pressure
  and under interrupts). It found Spike's `mstatus.XS` writable (fixed with
  `mie`'s bit 12, above); v1's C-level DOT8 jobs not ported (now
  `c/dot8_jobs`); the exhaustive program's stated reason for running in the
  shell alone weaker than stated (it has now run once in lockstep); and
  random programs rarely giving dot8 a corner word (the corner word is now
  its first operand). A second review, before the push, found `c/dot8_jobs`
  giving every alignment of a size the same data (often all zero or all
  −128, so alignment did not matter), where v1 gives each job its own (now
  so, as above), and two overstated sentences in this record (corrected).

**Timing** (FPGA, out of context, 10 ns; `b087ab2`): with the four products
in LUTs, dot8's M1 path (product, sum, M2's result) missed 10 ns in every
form (93.1, 91.7 and 90.0 MHz); with them in DSP blocks Vivado summed them
through the DSPs' cascade, still slower (99.1, 98.4 and 99.1 MHz); with the
products in DSP blocks and the sum in the fabric it meets 10 ns: the core
alone at 103.3 MHz (+0.318 ns; 2,830 LUTs, 1,361 flip-flops, 8 DSPs, against
18.4's 108.3 MHz, 2,808 LUTs and 4 DSPs), with the block RAM in the §5 form
at 101.6 MHz (+0.154 ns) and with the request registered at 102.4 MHz
(+0.233 ns). The dot8 path (a DSP product through the sum into M2's result)
is now the worst in the core alone and with the request registered; in the
§5 form the worst is still 18.2's forwarded operand into the block RAM's
write enable. The paths §4 names: `d_rsp_valid` to the next request
+3.231 ns, the `d_rsp_error` kill +2.599 ns (§5 form). Evidence:
[`results/phase18/aster-18.5`](results/phase18/aster-18.5/README.md).

**Signed off.** The owner signed off 18.5 on 2 October 2026, accepting
cpu.md §9's 18.5 clarifications: dot8 computed in M1 with the hazard
table's timing, `misa.X` set, custom-0 funct7 2–4 illegal (v1's runtime
returns from interrupts with `mret` once the SoC moves to the Aster core,
Phase 20), and the DOT8 Conv2D's lack of speed-up left to the software
(Phase 19/20). ACT4 is decided separately (checklist).

### ACT4 adopted; `time` reads the platform's `mtime` (2 October 2026)

At 18.5's sign-off the owner adopted riscv-arch-test 4.1.0 (ACT4), time-boxed:
its programs check themselves against expected results computed by the Sail
model, a reference independent of Spike, which every other test here is
compared with. `make core-aster-act4` (in `make check`) builds and runs them.

- **Tools** (docs/toolchain.md, "ACT4"), outside the repository like Spike's
  source: the 4.1.0 checkout (commit `6e8a451`), the Sail model 0.13.1's
  release binary, the framework's Python packages in their own environment,
  and the UDB gems through Bundler. The build leaves the checkout unchanged
  and caches under `build/act4`.
- **The core's configuration** (`verification/core/act4/aster-rv32ima`):
  its UDB description (RV32IMA, Zicsr, Zifencei, Zicntr, Sm 1.12, machine
  mode only; misaligned accesses trap; mtval as the core writes it; mtvec
  direct only; the hardware performance monitor read-only zero; `udb validate`
  passes); Sail's configuration to match (the cv32e40x example's, adapted; the
  shell's memory at 0x8000_0000, nothing mapped at 0x4000_0000 so accesses
  there fault, as in the shell); the macros (halting through tohost, failure
  diagnostics on the shell's console, interrupts through the shell's device);
  and the layout. A host test keeps them consistent with each other and with
  the shell (`verification/host/test_act4_config.py`).
- **A machine timer in the shell.** ACT4's environment assumes a
  memory-mapped timer: without one, Sail (whose CLINT holds MTIP high from
  reset) took timer interrupts the core never would. The shell now has one,
  as a SoC would: `mtime` counts once per cycle, and with `+timer` its
  registers are mapped in the CLINT layout Sail uses and MTIP is high while
  `mtime >= mtimecmp`.
- **Results:** 102 programs (I 39, M 8, Zmmul 4, Zaamo 9, Zalrsc 2, Zicsr 6,
  Zicntr 2, Zifencei 1, Sm 24, exceptions 6, interrupts 1) — 102/102 in all
  six memory modes. Five of them (`Sm_shadow-00`, `Sm_scsr_from_m-00`,
  `ExceptionsSm_medeleg_m/s/u-00`) test supervisor features and run only their
  harness on this machine-mode core, so 97 test it. The set is committed
  (`programs.txt`) and the run requires exactly it, so a configuration or
  framework change that adds or drops a program fails. Two of the first run's failures were the configuration's:
  Sail treated `mhpmevent3`–`31` as unimplemented until its Zihpm switch was
  on (the core implements them as read-only zero, which the privileged
  specification allows and Spike agrees with); and a halting macro that kept
  storing to tohost left a store accepted after the shell stopped, under
  back-pressure (it now stores once).
- **The finding:** `Sm_mcsr_cntr-00` writes 42 to `mtime` and requires `time`
  to read about 42 — the privileged specification's rule that `time` is a
  read-only shadow of the platform's `mtime`. 18.3's choice, the core's own
  counter, cannot follow a write. The owner chose to make `time`/`timeh` read
  the platform's `mtime` through a new 64-bit input, registered in the core
  (cpu.md §3, §5, "Changes after approval"); the shell's timer drives it, and
  the SoC's timer will (Phase 20). The register replaces the old counter's,
  so the flip-flop count is unchanged.

Verification of the change: `make core-aster-tests` (`interrupts/time_counter`
still holds — `time` advances with the cycles, runs on while `mcountinhibit`
stops the other counters, ignores `mcycle` writes; `traps/csr_ordering` reads
it in lockstep), and the planted bugs: 77 (18.3's three `time` bugs moved to
the new register, and one added, `time` reading `mcycle`), 73 caught (18.3's
four). Run against ACT4 alone, 45 of the 77 are caught — none that the other
tests miss: ACT4 cannot see the thirteen DOT8 and `misa` bugs (Sail has no
Xasterdot8, and no program checks `misa` exactly), interrupt timing (it has one
interrupt program), the pipeline-internal and RVFI-only bugs, or those that
only cycle counts show. Its value is the second, independent reference: the
standard ISA as Sail reads it agrees with the core in all 102 programs, and
the one place they disagreed was a real departure from the specification.
`make check` passes (292 PASS lines). The pre-push watchdog review found no
error in what ran; it found that the gate could not notice programs going
missing (it now requires the committed set), the five programs that only run
their harness here (named above), two comments still describing the old
counter and a sentence in cpu.md on what drives `mtip` (corrected), and that
the framework would build its own environment inside the checkout if `mise`
or `uv` were installed (the Makefile now keeps it on ours).

**Timing** (FPGA, out of context, 10 ns; `c24b32b`): the core alone at
105.1 MHz (+0.489 ns; 2,848 LUTs, 1,361 flip-flops, 8 DSPs), the §5 form at
105.3 MHz (+0.502 ns), the request-registered form at 105.0 MHz (+0.474 ns),
against 18.5's 103.3, 101.6 and 102.4 MHz — the `mtime` input adds no path
(it is registered at the port), and Vivado's placement varies by a few hundred
picoseconds from one netlist to the next. The worst paths are of 18.5's kinds:
a forwarded operand into M1's CSR-write flag (core alone), dot8's product
through its sum into M2 (§5 form), W's result through forwarding into M1's
next PC (request registered). The paths §4 names: `d_rsp_valid` to the next
request +3.856 ns, the `d_rsp_error` kill +3.197 ns (§5 form). Evidence:
[`results/phase18/aster-act4`](results/phase18/aster-act4/README.md).

### Milestone 18.6: L1 caches and the runtime port (3 October 2026)

The Aster core has its L1 caches and v1's runtime (`rtl/aster_core/aster_l1i.sv`,
`aster_l1d.sv`, `aster_l1_ram.sv`; `software/runtime/start_aster.S`,
`start_multicore_aster.S`, `aster_trap.S`), as the owner set them on
3 October 2026 (cpu.md §9, "Changes after approval"): an instruction and a
data cache of 4 KiB each, direct-mapped, with 16-byte lines, the data cache
write-through with no write-allocate, both blocking. The choices made in
building them are listed in [`cpu.md`](cpu.md) §9 ("Clarifications during
milestone 18.6"), for the owner's acceptance:

- **The caches** sit between the core's ports and memory-side ports of the
  same protocol (cpu.md §5), in two stages: the tag compare (tags in LUT RAM)
  beside the data array's two-cycle block-RAM read, then the answer — a hit
  two cycles after acceptance, as §5's normal answer, at one per cycle. A
  waiting request keeps its word and rereads the array only if an older
  request wrote it after its read. The data cache decodes its own errors
  (outside the cacheable memory and its I/O windows) in the cycle after
  acceptance from the registered address; stores are written through (into
  the array too on a hit) and answered once the memory side has; `lr`, `sc`
  and the AMOs pass through to the memory side, which holds the reservation,
  and `sc` and the AMOs invalidate their line. The instruction cache answers
  a fetch outside the cacheable memory with an error itself. Their array
  reads and stage-1 registers are enabled by their own readiness (from their
  registers), not by the core's late request valid — 18.1's timing lesson.
- **The core** gains `fencei_inval` (`fence.i` invalidates the instruction
  cache; a refill in progress then installs nothing), and its CSR
  instructions and `mret` now wait for M1 and M2 to be empty (18.3: M1): a
  `time` read right behind a store to `mtime` read the old value when the
  data cache sent the store on after it had left M1 (ACT4's `Sm_mcsr_cntr`).
  No kernel's window changes (none has a CSR instruction); the CPI model
  follows the rule and stays exact.

Verification (`make core-aster-l1-unit`, `core-aster-l1-tests`,
`core-aster-firmware`, all in `make check`) — the gate's three parts:

- **The cache reference model.** With `+cache_model` the shell keeps the
  lines each cache holds, from its misses and the policy, and checks every
  lookup's hit or miss and every memory-side access the caches make: a miss
  refills exactly its line, a store, atomic or I/O access goes to memory
  exactly once and unchanged, a hit or an error not at all (else
  `CACHE_MISMATCH`). Every run of the cached core has it on, and the data is
  checked as before, in lockstep with Spike and by signature. The shell's
  protocol checks then see the caches' memory side, so the shell runs them
  on the core's side too (requests stable while they wait, well formed, at
  most two data requests in flight), and the load check follows the core's
  loads into the data cache (each must retire exactly once). Self-tests
  show each fails when it should: the model kept through `fence.i`
  (`CACHE_MISMATCH`), a load recorded twice (`LOAD_MISMATCH`, both cores),
  and three rules broken between the core and the caches, as the PicoRV32
  adapter breaks them on its ports (`D_REQ_UNSTABLE`, `I_REQ_UNSTABLE`,
  `D_REQ_MALFORMED`). The data cache withholds readiness while it holds two
  requests, which the core never presents a third behind, or for the one cycle
  a replaying load rereads the array, when the core rarely presents one (a
  fetch bubble behind the load): in the gate's runs the core's data request
  waited for one cycle in ten runs of seven programs (the shell counts it,
  `core_d_waits`; suites under back-pressure and long stalls, a random
  program, an ACT4 program), holding steady each time. So the first self-test withholds the
  cache's readiness itself.
  Beside it, each cache has a random unit test against a flat memory
  (`verification/core/l1`, from the milestone's watchdog review): every
  answer's data, the error timing, every memory-side access, and for the
  instruction cache memory rewritten and invalidated at random — 200 seeds
  of a million cycles each.
- **Back-pressure.** The cached core runs every suite (97 tests), arch-test,
  random programs with coverage, random interrupts, ACT4 (102/102), the
  firmware and the kernels in the memory modes of 18.1–18.5 (arch-test on
  two, the kernels on the one-cycle memory) and in a new long-stall mode
  (`+long_stall`: one access in sixteen is answered 16–63 cycles late, as
  behind a slow memory); the unit tests' memory answers up to 67 cycles late.
  Under long stalls with an interrupt about every 20 cycles a handler often
  outlasts the gap to the next, so most cycles go to handlers: those random
  programs need up to about 213,000 cycles, and that mode runs with ten
  times the usual 200,000. The long-stall mode reaches what 18.1 and 18.3
  said it would need to: a finished division waits in Execute behind a load
  (8 of 20 random programs, 184 cycles, its result asserted to hold; the run
  now requires it); the other case, a trap record still in W when its handler writes
  `mepc` or `mcause`, can no longer occur once CSR instructions wait for M2
  (W is empty at a CSR write; asserted).
- **The firmware regression.** v1's runtime is ported to the core — `mtvec`
  (direct) with one handler that saves v1's caller-saved registers and sends
  an interrupt to v1's `aster_irq_dispatch` and an exception to
  `aster_exception`, `mret`, the hart from `mhartid`; `aster.h` enables
  interrupts with CSRs under `ASTER_CORE`. v1 firmware built with it runs on
  the core with and without its caches, in every memory mode (and with
  long stalls on the cached core): `runtime.c`
  (data copy, `.bss`, stack — now checked against the layout's stack symbols,
  which v1's `make runtime` still passes) in lockstep; `timer_interval.c` and
  `timer_interrupt.c` (a software and a timer interrupt through the ported
  handler and v1's interrupt controller) in the shell alone, with v1's timer,
  hart-control page, interrupt controller and performance block modelled
  from their RTL (`+v1_devices`); and the CPU kernels, v1's benchmark
  sources, in lockstep on the cached core (9/9, matching their Phase 17
  records). Not run, and why: cpu.md §9.
- **Random programs** now follow 30% of plain fences with an AMO, an idiom
  whose (fence, atomic) interrupt pair was otherwise rare: with CSR
  instructions waiting for M2 the interrupts landed elsewhere, and one mode
  of the uncached core's gate reached 99 of its 100 required pairs. Every
  random-interrupt mode on both cores now reaches all 100 (the pair 21 to
  74 times per run of 20 programs).

Results (window CPI; "1-cycle" is the one-cycle shell memory of the 18.7
gate, "long stalls" the long-stall mode with stall seed 3 on the two-cycle
memory — `core-aster-l1-tests`'s `kernels` and `kernels-long-stall` modes, and
for the core without its L1 `run_core_tests.py --dut aster --kernels
--stall-seed 3 --shell-arg +long_stall --max-cycles 60000000`):

| Kernel | No L1, 1-cycle | L1, 1-cycle | No L1, long stalls | L1, long stalls |
| --- | ---: | ---: | ---: | ---: |
| CoreMark (1 iteration) | 1.567 | 1.704 | 5.806 | 2.007 |
| Dhrystone | 1.591 | 1.940 | 6.207 | 2.797 |
| sort/search | 1.654 | 1.810 | 5.916 | 2.556 |
| FFT | 1.199 | 1.452 | 4.888 | 1.911 |
| strided | 1.508 | 1.699 | 5.690 | 2.168 |
| scalar Conv2D, coherent SoC | 1.397 | 1.683 | 5.590 | 2.215 |
| scalar reduction | 1.385 | 1.483 | 5.419 | 1.920 |
| Conv2D, minimal top (not in the gate) | 1.574 | 1.588 | 5.808 | 1.647 |
| DOT8 Conv2D (not in the gate) | 1.193 | 1.436 | 5.215 | 1.924 |

Behind a slow memory the caches cut CPI by 2.2–3.5×; on the gate's
one-cycle SRAM they can only cost cycles (1–22%): misses (Dhrystone's and
the coherent Conv2D engines' data misses are 6,100–6,600 each over the
whole run) and stores, which
write through and are answered only once the memory side has — at least
five cycles after acceptance, against two (the review's finding, below).
The 18.7 gate measures the core without its L1 (cpu.md §7), so this does not
touch it.

- **Planted bugs** (assertions off), 24 new ones on the cached core:
  - in the instruction cache: the valid bit ignored, a tag bit dropped,
    `fence.i` ignored, a refill not poisoned, a waiting fetch not marked
    stale, a miss answered with the line's first word, the kept word ignored,
    refill words written to the wrong slot;
  - in the data cache: a store hit not written into the array, a store miss
    written, all bytes written, an AMO keeping its line, `lr` invalidating
    its line, an I/O load refilled, the error a cycle late, a stale word
    answered (three ways, the reviewer's among them), a miss answered with
    the line's first word, refill words written to the wrong slot, the kept
    word ignored, a store answered before the memory side;
  - in the core: `fence.i` not invalidating, and CSR instructions waiting
    for M1 only.

  All 24 are caught: 23 by the core-level runs (seven first by the cache
  reference model, `CACHE_MISMATCH`; the CSR one only by ACT4), and the
  reviewer's only by the unit tests. The core's 77 rerun on the final RTL:
  72 caught. The five not caught are 18.3's four and `sys-retire-at-commit`,
  which 18.6's serialization makes equivalent: a CSR instruction now enters
  M1 with M2 empty and leaves it in its first cycle (asserted), so counting
  its retirement then or as it leaves is the same. In all, 101 planted,
  96 caught. `make check` passes (338 PASS lines).

- **Review.** The milestone's watchdog review found no RTL bug in the
  caches. It wrote the random unit tests (now in the repository, above) and
  with them found a rare case the core-level runs may never reach: a load
  accepted at the edge of a refill's last write, which only the stale flag
  keeps from answering the array's old word (now a planted bug, caught by the
  unit tests alone). It also found:
  - the shell's protocol and load checks watching the caches' memory side
    in cached runs (now run on the core's side too, the load check on the
    data cache's lookups);
  - the array reads and stage-1 registers enabled by the core's late request
    valid (now by the caches' own readiness);
  - the CSR change unrecorded (now cpu.md §9);
  - the long-stall mode's coverage unproven (now counted, above);
  - the gate's targets missing from `make check` (added).

  It raised two questions for the owner, below. The pre-push review found
  no RTL bug either. It found:
  - the long-stall mode with random interrupts failing on its cycle limit
    (raised, above);
  - the unit tests passing a cache that never answers (they now require
    progress);
  - nothing in the repository showing the new checks fail (the self-tests,
    above);
  - two overstated sentences in this record (the firmware and random modes
    were then fewer than it said; they are now run in every mode);
  - the firmware's Spike plugin missing from its target (added);
  - the long-stall kernel numbers not reproducible (a mode, and the command
    above);
  - the division-wait coverage not required (now required).

  A third review, of those fixes, found the reason given for the core's data
  request never waiting wrong (corrected, and now counted) and a killed
  division counted as a wait (no longer).

**For the owner's decision with 18.6:**

1. **Coherence with the other masters (Phase 20).** Write-through keeps memory
   current for the other hart, DMA and the NPU, but nothing removes this
   core's lines when they write memory: v1 firmware that reads a DMA or NPU
   result, or another hart's data, from cacheable memory would read stale
   lines once the SoC has them (the shell has no other master, so no test
   here can show it). v1 kept shared globals uncached, and its DMA and NPU
   drivers already end with `fence iorw,iorw`. Options: (a) a `fence` whose
   predecessor set includes device input (`i`) invalidates the whole data
   cache in one cycle — v1's drivers then work unchanged, and `fence rw,rw`
   (the mailboxes) costs nothing; (b) shared and DMA buffers outside the
   cacheable region, as v1's shared globals; (c) the Phase 20 fabric
   invalidates the lines other masters write (full coherence, more hardware);
   (d) decide in Phase 20. Recommended: (a) with (b), now or in Phase 20.
2. **Store cost.** A write-through store is answered once the memory side
   has: at least five cycles after acceptance against a hit's two, and back
   to back about one per five cycles — most of the L1's 1–22% on the
   one-cycle memory above. Options: (a) leave it (the 18.7 gate measures the
   core without its L1); (b) answer a store to cacheable memory when the
   memory side accepts it (it never errs there; I/O stores still wait, so
   devices keep the ordering the CSR rule relies on) and send a store or a
   miss in its first cycle at the head instead of a cycle later — stores as
   fast as hits, with a count of the memory side's answers to drop.
   Recommended: (b), in 18.6 or with the Phase 20 memory side.

**Timing** (FPGA, out of context, 10 ns; `2a3b3cf`): the core with its
caches and a two-cycle block RAM behind them meets 10 ns at 101.1 MHz
(+0.104 ns; 4,189 LUTs, 2,417 flip-flops, 34 block-RAM tiles — the caches'
two arrays and the 128 KiB memory — and 8 DSPs). Its worst path is inside the
core and touches no cache signal: Decode's instruction through its own decode
(illegal, then operand use, then the hazard check) into the enable of
Decode's registers. The
paths across the core-cache boundaries all have margin: the data cache's
error decode into the core's kill +0.973 ns, the core into the data cache
+2.175 ns, the instruction cache into the core +3.027 ns, and the data
cache's memory side +2.661 ns and up (the instruction cache's refill paths
are not named; they are within the top's +0.104 ns). The core alone runs at 105.0 MHz (+0.472 ns;
2,821 LUTs, 1,361 flip-flops), the §5 form at 101.1 MHz (+0.106 ns) and the
request-registered form at 103.8 MHz (+0.363 ns), against ACT4's 105.1,
105.3 and 105.0 MHz. These are one run per top; Vivado's placement varies by
a few hundred picoseconds from one netlist to the next (the §5 form gave
101.6 MHz at 18.5), and the worst paths are of the earlier kinds. The paths
§4 names: `d_rsp_valid` to the next request +2.416 ns, the `d_rsp_error` kill
+2.139 ns (§5 form). Evidence:
[`results/phase18/aster-18.6`](results/phase18/aster-18.6/README.md).

## Milestones and gates

| Milestone | Content | Exit gate |
| --- | --- | --- |
| **18.0** | Spike; CPU shell with PicoRV32 and RVFI trace; lockstep comparator; `riscv-tests` in the shell; `riscv-arch-test` harness; timing scripts; PicoRV32 baseline timing | PicoRV32 passes lockstep on `riscv-tests`; the comparator catches an injected mismatch in every compared field; timing scripts report PicoRV32 on both targets — **met** (checklist below) |
| 18.1 | RV32I pipeline | `riscv-tests` rv32ui and arch-test I in lockstep; random programs in lockstep; first timing report — **met** (owner, 1 October 2026; "Milestone 18.1" below) |
| 18.2 | M extension | um/arch-test M, multiply/divide corner cases, lockstep, timing — **met** (owner, 1 October 2026; "Milestone 18.2" below) |
| 18.3 | Zicsr, traps, interrupts, counters | arch-test Zicsr; directed traps in every stage; interrupt tests in every pipeline state — **met** (owner, 2 October 2026; "Milestone 18.3"; riscv-arch-test 3.10.0 has no Zicsr suite: its privilege suite and riscv-tests rv32mi stand in, as the owner accepted) |
| 18.4 | A extension, `fence`, `fence.i` | ua/arch-test A and Zifencei; atomic and self-modifying-code tests — **met** (owner, 2 October 2026; "Milestone 18.4"; the litmus tests of cpu.md §8 are single-hart until two cores share memory, as the owner accepted) |
| 18.5 | Xasterdot8 | v1 DOT8 reference tests on the core — **met** (owner, 2 October 2026; "Milestone 18.5") |
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
- [x] **By 18.3:** CSR write point and `minstret` read semantics (a
  serializing instruction writes and retires at the end of its first M1
  cycle), the `pc_wdata` check extended to trap records (a trap record's
  `pc_wdata` is the handler's address, matched by the next record), and
  `ma_data` (the environment's handler emulates the misaligned access) —
  "Milestone 18.3"
- [x] **By 18.4:** `fence.i` draining in-flight data accesses and flushing
  F1, F2, the buffer, D and the instruction entering E (`directed/smc` under back-pressure, with the
  shell's fetches blind to unanswered writes), AMO operands (address and
  data) as hazard consumers and atomic results as producers, and `fence_i` —
  "Milestone 18.4"
- [ ] **By the milestone named:** a cover point that a data-port error
  really waits in M1 and that a division really runs before a trap kills it
  (both depend on the stall seed; `traps/m1_traps` reaches them in today's
  modes, measured with an instrumented copy of the RTL, but nothing enforces
  it)
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
- [x] **Aster-core shell, 18.3:** the same errors taken as traps to a
  handler (`traps/m1_traps`, `traps/fetch_traps`)
- [x] **Aster-core shell, later:** from 18.6, the signature read through the
  cache hierarchy — needs nothing with the write-through data cache (18.6):
  memory holds every store, each checked by the cache reference model to
  reach it exactly once, so the signature the shell reads from its memory is
  the hierarchy's
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
- [x] **A long-stall memory mode, by 18.6:** the shell's back-pressure adds at
  most two cycles, so a finished division never waits in Execute (its result
  is asserted to hold); 18.6's cache misses will need a mode with long stalls,
  which will exercise it — and a trap record still in W when its handler
  writes mepc or mcause (18.3 carries the trap's values with the record for
  that case; nothing reaches it yet). Done in 18.6 (`+long_stall`, in
  `core-aster-l1-tests`): a finished division waits in Execute in 8 of 20
  random programs on the cached core (184 cycles, the shell's `div_waits`),
  its result held; the trap record in W can no longer occur, since CSR
  instructions now wait for M2 (asserted)
- [x] 18.2 as in the table above — complete (owner, 1 October 2026)
- [x] 18.3 as in the table above — complete (owner, 2 October 2026), with
  the added CSRs, `time`/`timeh` as the core's own time counter, and the
  privilege suite and rv32mi standing in for "arch-test Zicsr" (cpu.md §9)
- [x] riscv-arch-test 4.x (ACT4): reconsidered when the ISA was final, after
  18.5, and adopted (owner, 2 October 2026): `make core-aster-act4`, 102/102 in
  six memory modes, with `time`/`timeh` reading the platform's `mtime` (the
  owner's choice when ACT4 showed 18.3's own counter departing from the
  specification) — "ACT4 adopted"
- [x] 18.4 as in the table above — complete (owner, 2 October 2026), with
  cpu.md §9's 18.4 clarifications (single-hart litmus tests; the
  reservation rules the memory side must keep)
- [x] 18.5 as in the table above — complete (owner, 2 October 2026), with
  cpu.md §9's 18.5 clarifications
- [ ] 18.6 … 18.7 as in the table above

## Non-goals

Compressed instructions, supervisor/user modes, an MMU, floating point, dynamic
branch prediction before it is measured to pay, and multi-issue execution
(see [`cpu.md`](cpu.md) §1). SoC integration of the new core is Phase 20.
