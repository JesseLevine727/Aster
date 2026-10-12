# Phase 21: 100 MHz closure and energy per operation

Status: **draft for the owner's approval.** No work starts before it is approved. Its decisions are D1 to D11
(§10). Two of them amend earlier decisions:
- **D10:** phase17-plus.md §6's Phase 21 exit gains energy, and tuning.md's decision 9 (a short Phase 21) is
  superseded.
- **D11:** whether CIFAR's CPU paths are in the energy set. Leaving them out would amend phase17-plus.md §4's ML
  row.

## 1. Why this phase

[`phase17-plus.md`](phase17-plus.md) §6 plans Phase 21 as the FPGA's 100 MHz closure. Its exit asks for:
- all-engine routed timing at 10 ns with no failing endpoints;
- clean routing and methodology checks;
- resources within the limits;
- the acceptance subset, a valid CoreMark included, run on the board at that clock on repeated boots.

Phase 20's last milestone met all of it on the final image except boots with the board powered off between them
(D8):
- **The final build's reports** (results/phase20/matrix-20.5/timing/final-build):
  - +0.306 ns setup and +0.040 ns hold, with no failing endpoint;
  - every net routed, and no methodology finding;
  - no DRC error; 62 DRC warnings, all DSP pipelining advisories (DPIP-1, DPOP-1, DPOP-2);
  - 72.75% of LUTs.
- **The board run** (results/phase20/board-20.5): every R program as simulated on two separate loads, and
  CoreMark's performance and validation runs on two more.

tuning.md's decision 9 (approved 10 October 2026) therefore kept Phase 21 short.

**One frozen v2 target is still open.** phase17-plus.md §2's Energy row asks for "Measured or vector-based energy
per workload, not one global power number divided among workloads". Its evidence is to be "Workload-specific
post-route activity, or a clearly labeled board measurement. Separate FPGA PL from PS. State the corner of every
power number." Phase 20 set energy aside, so it falls to Phase 21.

On 12 October 2026 the owner chose the method: a **SAIF dynamic-power estimate**. Each workload's own switching
activity, simulated on the routed design, is given to Vivado's power analysis. This answers the energy side of
research question 1: which engine does the same work for the least energy.

## 2. Scope

**In scope:**
1. **The formal 100 MHz closure record** of the final image, and cold boots (§7).
2. **Energy per operation** for the workloads of §4: a SAIF dynamic-power estimate of the final image's
   programmable logic (§3).
3. **A Phase 21 report and its closeout audit.**

**Out of scope:**
- **Any RTL change.** The image is 20.5's final one, so its timing stands.
- **The PS's power, and power optimization.**
- **A board power measurement,** unless the owner adds one (D9). The PYNQ-Z1 cannot measure its own power. A check
  on the board on 12 October 2026 found:
  - no hwmon device and no I2C device;
  - one sensor, the XADC (`iio:device0`), which reads supply voltages and the die temperature, not current.

  Step 1 saves that check's output in the evidence.

## 3. The method

### 3.1 The design simulated

1. **Bind the checkpoint.** The routed checkpoint of the final image is `aster_soc_routed.dcp`
   (build/fpga/pynq_z1/aster_soc-c8k-default-o5asm-mgi). It is first bound to the signed-off image: a bitstream
   written from it must have the configuration data of record (0bf4b4c3…).
   - Its hash and the netlist's go into the evidence.
2. **Export the SoC's cell.** Its functional netlist is written out (`write_verilog -mode funcsim`), for the block
   design's `aster` cell (`aster_soc_board_i/aster/inst`):
   - the SoC, its memory and its devices;
   - not the PS, the AXI interconnect or the reset block.
3. **The testbench,** for Vivado's simulator (xsim), instantiates the netlist at the same hierarchical path under
   its own top. The SAIF's instance names then match the routed design once that top is stripped
   (`read_saif -strip_path <testbench top>`).
   - It drives the 10 ns clock and the reset.
   - It loads the program through the cell's AXI port, as the PS does.
   - It reads the console ring and the register page through that port, as the board's script does.
   - It uses Vivado's simulation libraries (unisims, glbl). Step 1 checks their behaviour against the RTL
     simulation: the reset pulse, and the block RAMs' collision models.

### 3.2 Each run must reproduce its references

Every program runs whole, from load to tohost, and must end as on the board. Each check is against a named
reference:
- **20.5's board reference** (results/phase20/board-20.5/reference.json.xz, and its CoreMark one):
  - the tohost cycle;
  - hart 0's instructions retired at tohost;
  - the register page's window (0x3F020 and 0x3F028): the cycles and instructions from the first START to the first
    FREEZE.
- **The bundle's archived console** (results/phase20/matrix-20.5's raw archives): the console, byte for byte, so
  every record and counter in it, the records' own windows included.
- **The idle programs (§4)** have no board reference. They are captured with the matrix's runner, and are checked
  against that RTL simulation instead.

A run that differs is not used. The modified testbench of §3.3 (tb_soc with its interval lister) must also end
every program as the reference.

### 3.3 The activity, over exactly the counted cycles

A record's window is often several counting intervals, not one span: START, then FREEZE and RESUME in turn
(software/matrix/matrix_window.h). For example, MNIST's e2e window has one interval for each image.

**The intervals.**
- The intervals' first and last cycles come from the RTL simulation of the same program. A tb_soc option lists the
  devices' counting intervals: a testbench change, not an RTL one.
- They are given in the SoC's cycles counted from hart 0's release (the register page's 0x3F010). The xsim
  testbench counts clock edges from its own release write, and checks that count against the counter's flip-flops
  in the netlist. So its load phase may differ from tb_soc's.
- The netlist simulation reproduces the RTL simulation cycle for cycle (§3.2), so the intervals are the same.

**One SAIF per interval.**
- The simulation runs to each interval's first cycle, records the switching activity of every net in the cell until
  its last cycle, and closes the file.
- The testbench reads the console between intervals, never inside one. Each program's console fits the ring between
  those reads, which is checked.

**The merge.** A SAIF file gives, for each net, the time it spent low (T0), high (T1), unknown (TX) and floating
(TZ), and its number of toggles (TC and IG). A checked script merges a window's interval files into one:
- it requires the same set of nets in every file;
- it sums each net's T0, T1, TX, TZ, TC and IG, and the durations;
- it checks T0 + T1 + TX + TZ equals the duration for every net, and TX is zero;
- the merged duration must equal the record's window cycles × 10 ns.

**The alignment check.** Bit 0 of hart 0's cycle counter toggles once in each counted cycle, so its toggle count
in the merged file must equal the window's cycles. That catches an interval shifted by a cycle. Where the devices'
counting-enable net survives in the routed netlist, its time high must also equal the merged duration.

### 3.4 The power

1. **Read the SAIF.** Vivado reads the merged SAIF into the routed checkpoint.
   - Its coverage is recorded: the share of the `aster` cell's nets matched, with the unmatched list.
2. **Report power by hierarchy** (`report_power -hier`):
   - each hart's core and caches;
   - the fabric and the banks;
   - the NPU, the DMA and the devices;
   - the SoC's own top-level logic: the console ring, the register page, and the NPU's and devices' glue.

   The parts must add up to the cell.
3. **The clock network.** Step 1 fixes from `report_power`'s clock table which clock power lies inside the cell's
   hierarchy (its leaf clock pins) and which outside it: the global clock buffer and its net, driven from the PS's
   FCLK0. Each is counted once.
   - The global buffer and net also feed the AXI interconnect and the reset block, a small share of their loads.
   - They are counted in full with the cell, and step 1 reports the load split from the clock table.
4. **"Total dynamic"** is the `aster` cell's dynamic power with the clock power that drives it, counted as step 1
   fixes.
   - The cells outside it get Vivado's default activity, not the workload's: the AXI interconnect, the reset block,
     and the PS. They are reported apart and never mixed in.
5. **The confidence.** The design's only top-level pins are the PS's (DDR and MIO), which no SAIF covers.
   - So Vivado's overall confidence will read below high, and it is not gated.
   - The gate (§9) is on the categories the SAIF can reach: the coverage threshold (D5) and Vivado's confidence for
     internal nodes and clocks.
   - Step 1 shows the gate can be reached before any energy run.
6. **The conditions,** stated with every figure:
   - typical process;
   - the supply voltages;
   - a fixed junction temperature (D6), so static power is the same for every run;
   - Vivado's board settings, as used.
7. **Static power** is reported once, beside the tables. It is not in the per-op figures.

### 3.5 Energy per operation

- **The energy.** A window's dynamic energy is its dynamic power times its time: the record's window cycles at
  10 ns.
- **The figure.** That energy is divided by the window's operations (§4).
- **Two forms of each figure:**
  - **total dynamic:** everything in the cell, and its clock, that switched in the window;
  - **above idle:** less the matching idle program's dynamic power over the same time (D4), which is the energy the
    work itself added.
- **What every figure includes.** The counters switch in every window: each record carries about 107 values (18 a
  hart, 13 for the DMA, 10 for the NPU, 48 for the fabric). The register page switches too. Both are in every
  figure.
  - They sit inside the devices and the engines beside other logic, so the hierarchy cannot isolate them.
  - Their share is bounded instead by the idle programs, whose windows count too.

### 3.6 What the estimate is

- **It is** Vivado's power model applied to the real routed design and each workload's real switching. So it is
  phase17-plus.md's "vector-based energy per workload" from "workload-specific post-route activity".
- **It is not a measurement.** Every figure is labelled "SAIF estimate, PL dynamic", with its conditions.
- **It misses glitches.** The functional netlist runs at zero delay, so glitches are not counted, and the figures
  are low by that share. The alternative is D1.

## 4. The workloads

### 4.1 The rule

A workload is in the energy set when it is one of these:
1. **An engine comparison of record:** each gate workload and each v1-retained application at its R
   configuration. Each method's original is in, and so is its fastest adopted variant at R, cold: the variant its
   gate counted. These are:
   - the GEMMs of npu.md §7;
   - MNIST, CIFAR (D11 for its CPU paths) and Conv2D direct;
   - ECG v1's case;
   - the reductions, v1's and the parallel one, and the FFT, the matrix's two-core comparisons where no engine
     applies.
2. **A sweep bracketing a crossover** Phase 20 found in cycles: the dot product, the FIR, the small GEMMs, the
   copies.
3. **A kernel of the CPU set** (cpu.md §7: its seven kernels, one hart).
4. **The idle programs.**

Every program in the set runs whole, cold (D3), at its default seed. It is a final-capture R program, so its energy
pairs with its record's cycles; the idle programs are the exception, being new. Each run's firmware hash must equal
its manifest entry's (`firmware_sha256`).

**Out, with the reasons:**
- **im2col Conv2D:** the direct lowering wins on every engine in the final capture. CIFAR's im2col stays in,
  though slower than direct, as a method of a v1-retained application.
- **The coherence programs and the memory microbenchmarks:** they measure the memory system, not an engine.
- **Every configuration other than R:** the routed design is R's alone, not the memory waits, the cache sizes or
  the NPU geometries.
- **CIFAR's CPU paths,** unless D11 puts them in: 31 to 57 million cycles each, cold.

### 4.2 The set

The operations are the records'. The ECG case is 1,024 samples in chunks of 64, with a 16-tap FIR.

| Workload | Methods (case) | Operation |
| --- | --- | --- |
| GEMM 64³, 96³, 128×64×128 | the NPU; packed DOT8 on one and two cores (`gemm_dot8_*`); v1's unpacked DOT8 on one and two cores | a MAC (two operations, as GOPS) |
| GEMM 1×1×1, 3×1×7, 4×4×16, 16×16×64, 32×1×784 | the NPU; scalar; DOT8; two cores (not at 1×1×1: unsupported) | a MAC |
| Dot product, K = 0, 1, 7, 8, 64 and 1,024 | scalar, DOT8, two cores (`dot/multicore`), the NPU | a MAC; at K = 0, which has none, a call (the fixed cost) |
| FIR, 4, 16 and 64 taps | scalar, DOT8, two cores, the NPU | a MAC |
| FFT (`dsp/fft`) | one and two cores | a butterfly |
| Conv2D direct | scalar, DOT8, two cores, the NPU, the NPU with two harts (`__2h`) | a MAC |
| MNIST MLP | scalar, DOT8, two cores; the NPU, one image at a time and in batches of 32, each on one hart and with two harts (`__2h`) | an image |
| CIFAR-10 CNN | the NPU, direct and im2col, on one hart and with two harts (`npu_direct__2h_copy`, `npu_im2col__2h_next`); the CPU paths by D11 | an image |
| Streaming ECG | one core scalar (`ecg_scalar`); DOT8 (`ecg_dot8`, `ecg_dot8__fir4`); v1's pipeline (`ecg_pipeline_v1`, `ecg_pipeline_v1__fir4`); the two-hart pipeline (`ecg_pipeline_overlap`, `ecg_pipeline_overlap__fir4_split`) | a sample |
| The reductions: v1's (`reduce_v1`) and the parallel one (`reduce_fill`) | one and two cores, and each two-core one's adopted `__loop` | a word |
| Copies of 64, 128, 4,096 and 32,768 bytes, aligned, not cached | the CPU's copy, the DMA | a byte |
| The CPU set: CoreMark (one iteration), Dhrystone, sort/search, FFT, strided, Conv2D, and the scalar reduction (`reduce_v1`, above) | one hart | the kernel's unit |
| Two idle programs (new, D4) | hart 0 spinning in a cached loop, the engines idle; hart 1 in reset in one, spinning in the other | (the "above idle" baselines) |

**The crossovers, cold.** The cold runs' own crossovers differ a little from Phase 20's warm ones:
- **The dot product:** scalar wins at K = 0, and the NPU from K = 1. DOT8 and two cores lose to scalar through K =
  64 and win at 1,024. Warm, they already win at 64.
- **The FIR:** DOT8 loses to scalar at 4 taps, warm and cold.
- **The copies:** the CPU wins at 64 bytes, and the DMA from 128. They bracket the DMA's 113 bytes.
- **The small GEMMs:** DOT8 loses to scalar at 1×1×1 and 3×1×7, warm and cold. Two cores lose at 3×1×7 cold, but
  win there warm.

The energy crossovers are compared with these.

**The size.** The set is about 115 programs and 103 million cycles, cold, plus the idle programs; CIFAR's CPU
paths would add 133 million. The longest is MNIST scalar, 9.7 million cycles; with CIFAR's CPU paths it is CIFAR
scalar, 57 million. The runs go in parallel, up to the
machine's 32 cores and 60 GB.

## 5. The steps

1. **The SAIF flow:**
   - the checkpoint bound to the image (§3.1);
   - the netlist and the testbench;
   - the libraries checked;
   - the interval lister in tb_soc, which must end all 5,254 R programs as 20.5's reference;
   - a smoke set reproduced exactly (§3.2): a GEMM on the NPU, MNIST on the NPU (32 intervals), Conv2D direct (4
     intervals), a two-hart program and the ECG pipeline;
   - the interval SAIFs merged and read back, with the coverage and the clock accounting fixed (§3.4);
   - the confidence gate shown reachable;
   - the simulator's speed measured;
   - the board's sensor check saved.

   Then a return to the owner (§6) before the energy runs.
2. **The idle programs,** written and captured with the matrix's runner.
3. **The energy runs:** every workload of §4, each reproducing its references, with its SAIFs and power report
   saved.
4. **The cold boots** (§7).
5. **The Phase 21 report and its closeout audit:**
   - **The report:** energy per operation by engine and size, the losses (where an engine costs more energy than
     another), and the conditions.
   - **The read-only audit:**
     - it re-derives every per-op figure from the saved power reports, the records and the references;
     - it rejects a run that did not reproduce its references, a merged SAIF whose duration is not its window's, a
       coverage or confidence under the gate, or a figure without its conditions;
     - it carries the closure's checks over from audit_phase20.py;
     - its self-test shows each check failing on a planted fault.

## 6. The run-time budget

Step 1 measures the simulator's speed in cycles a second. From it, the energy runs' time is projected.
- **The budget is 72 hours of wall time** for step 3, on the machine's cores.
- **If the projection is over it,** or the longest single run is over it, the plan returns to the owner with the
  projection and the options before step 3. The longest single run is CIFAR scalar under D11, 57 million cycles,
  which cannot be split. The options:
  - drop runs;
  - simulate from the RTL instead (D1);
  - accept the longer time.

## 7. The 100 MHz closure and the cold boots

- **The closure record** collects 20.5's evidence into phase17-plus.md's exit:
  - the final build's reports;
  - the two loads of every R program;
  - CoreMark's two runs on two loads.
- **The cold boots.** 20.5's repeated boots were separate programmings of the FPGA with the board left on. Phase 21
  adds a stricter reading: on each of three cold boots, the board is **powered off and on**, then loaded with the
  final image.
  - It runs tuning.md decision 9's acceptance subset, 20.5's whole board set: all 5,254 R programs, about 23 minutes
    a load, and CoreMark's two runs.
  - Each program must end as simulated.
  - The owner switches the power, or allows a switchable outlet (D8).

## 8. The evidence

The evidence is versioned, as phase17-plus.md §3 asks:
The directory is results/phase21/energy-21.
- **In git, per run:**
  - the merged SAIF (xz-compressed) and the power report;
  - the simulation log;
  - the path and hash of the reference it was checked against (20.5's evidence, not copied).
- **In git, once:**
  - the testbench, the Tcl and the merge script;
  - the tool versions;
  - the hashes of the checkpoint, the netlist and the image.
- **The checkpoint** (24 MB), in git: it is the input to every power report, and phase17-plus.md §3 does not
  count build/ files alone as evidence. It is bound to the image (§3.1).
- **Outside git:** the interval SAIFs. Their hashes are listed, and they can be made again by re-simulation.

Step 1 estimates the merged SAIFs' size. If they exceed about 100 MB in all, the plan returns to the owner on what
to keep.

## 9. The gates

- **Timing:** the final image's timing as signed off in 20.5, with no RTL change. The checkpoint is bound to the
  image.
- **The cold boots,** per D8: three, each running the acceptance subset as simulated.
- **Every energy run** reproduces its references (§3.2).
- **Every merged SAIF** passes the merge's checks, and its duration is its window's.
- **Every power report** meets the coverage threshold, with Vivado's high confidence for internal nodes and clocks.
- **Every workload of §4** has its energy per operation, total dynamic and above idle, with static power reported
  once, all with their conditions.
- **The audit** says PASS.

## 10. Decisions for the owner

- **D1. The activity's source:**
  - **the routed design's functional netlist** (recommended): the bitstream's own logic, at zero delay;
  - the routed design with its delays (SDF): glitches counted, but several times slower;
  - the RTL simulation: fastest, but Vivado matches it to the routed design only in part.
- **D2. The per-op figures:**
  - **total dynamic and above idle, with static reported once** (recommended; the owner chose dynamic,
    12 October 2026);
  - total dynamic alone.
- **D3. The cache state:**
  - **cold for every program** (recommended): each runs its work once, the shortest whole runs, and only the e2e
    window is used;
  - also warm, R's state, for the gate workloads, if §6's budget allows: two to four times the simulation, using the
    e2e window;
  - warm for every program.
- **D4. The idle baselines.** **Two idle programs** (recommended): hart 0 spinning in a cached loop and the engines
  idle, with hart 1 in reset in one and spinning in its cache in the other.
  - Each program's "above idle" uses the one whose hart 1 matches it: single-worker programs hold hart 1 in reset,
    and two-hart programs leave it spinning.
  - Each idle window is 100,000 cycles.
  - The alternative is one baseline, both harts spinning.
- **D5. The coverage threshold:** at least 95% of the `aster` cell's nets matched by the SAIF (recommended), or a
  threshold the owner sets.
- **D6. The junction temperature:**
  - **fixed at Vivado's computed value for the hart-1-in-reset idle program** (recommended), the same for every
    run;
  - the die temperature read from the XADC during the board runs.
- **D7. The report:**
  - **a separate Phase 21 report** (recommended), since Phase 20's report records that nothing was measured;
  - an energy section added to it.
- **D8. The cold boots:**
  - **three, with the owner switching the power** (recommended);
  - a switchable outlet the scripts drive;
  - none, keeping 20.5's loads as the repeated boots.
- **D9. A board cross-check (optional):** an inline meter on the board's power input, with each workload looped
  for seconds and the idle board subtracted.
  - It is whole-board power, labelled as such, and checks the estimate's total.
  - It needs the owner to fit the meter. Recommended only if a meter is at hand.
- **D10. The amendments:** phase17-plus.md §6's Phase 21 exit gains §9's energy gates, and tuning.md's decision 9
  (a short Phase 21) is superseded by this plan. Its acceptance subset, 20.5's board set, is kept for the cold
  boots.
- **D11. CIFAR's CPU paths** (scalar, DOT8, two cores; 31 to 57 million cycles each). phase17-plus.md §4's ML row
  asks for energy per inference on all supported execution paths.
  - **In, if §6's budget allows** (recommended). If not, the owner decides on step 1's projection.
  - Out: an amendment to phase17-plus.md §4's ML row for CIFAR.
