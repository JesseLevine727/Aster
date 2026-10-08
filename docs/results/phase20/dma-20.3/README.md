# Milestone 20.3 — the DMA

The text of record is the "Milestone 20.3" section of
[`../../../phase20.md`](../../../phase20.md), with soc.md §13's
"Clarifications in 20.3". `SHA256SUMS` covers every file here.

The exit (phase20.md's milestone table) has three parts: soc.md §10.5, the
DMA against CPU copies at every size and alignment in the SoC, and 10 ns in
context. All of it is on the final RTL. The four RTL files that changed
(`rtl/dma/aster_dma2.sv`, `rtl/soc/aster_soc.sv`,
`rtl/soc/aster_soc_devices.sv`, `rtl/aster_core/aster_l1d.sv`), concatenated
in that order, hash to sha256 `16dca43fd73eefeb…`, for the simulations and
the builds alike. The fabric's RTL is 20.2's, unchanged, so 20.2's fabric
mutants (36 of 36, and 31 of 31 in the reference) still stand.

**`capture-1/`, `capture-2/`** (`make soc-tests`, run twice; every file is
the same in both, as §10.4's determinism asks):
- **`dma.soc_dev.log`, `dma.soc_dev_w3.log`:** the DMA program
  (`software/tests/soc_dma.c`, through v1's driver, unchanged), on the
  device builds without and with three added memory waits:
  - **The copies:** 3,392 copies, each against a CPU copy of the same
    source: every length 0–40 at all 64 alignment pairs, and 12 larger sizes
    up to 4 KiB at all 64.
  - **The limits:** copies from LIMIT_LO, and a source and a destination
    each ending exactly at LIMIT_HI.
  - **Through the driver:** the errors, ABORT's prefix, and the interrupt.
  - **The counters:** each job's tallies, plus their reads and writes
    accepted, stalls and invalidated lines against the fabric counters'.
  - **Hart 1:** refused by the driver, and its 64 cached lines invalidated
    by the DMA's writes.
  - **Contention:** hart 1's traffic and an NPU GEMM run during the copies.
    The NPU is still running when a 4 KiB job ends.
  - **RESUMEs while counting** during a job.
  - **The DMA against the CPU copy** in cycles, by size, aligned and not.

  Each run's first line gives the contention figures (the DMA's stalls, the
  others' accesses, the reads the RESUMEs dropped). Its `DMA COPY` lines are
  phase20.md's timing table.
- **`soc-tests.log`:** the two-hart programs on both device builds: litmus,
  hart 1's reset stress, the runtime's dispatch and join, the devices and
  the DMA. The testbench also checks the ports:
  - every port R answer against memory at its acceptance, with the bank
    rule (about 95,000 a build);
  - every port W write through the memory checker and the snoop checks
    (about 92,000);
  - the DMA's accesses inside its job's ranges;
  - while the harts are held, only the ARM side's accesses on the ports.

  The litmus, reset-stress and dispatch figures are 20.2's exactly: the DMA
  changed no hart's cycle.
- **`soc-board.log`:** 18.7's 99 programs on the regression build, each as
  in the CPU shell, cycle for cycle and record for record. Each is loaded
  through the DMA's port registers.
- **`soc-gates.log`:** soc.md §10.4's comparison on the gate programs,
  with every cycle traced. The figures are 20.2's exactly.
- **`make-soc-tests.out`:** also covers the devices against 8809f72's, in
  lockstep for 3 million cycles:
  - `window_adds` against the golden's own counter, every cycle;
  - 49,902 counting cycles not added, and 971,711 added to.

**`suites/`** (the final RTL):
- **`dma-tests.log`** (`-detail`): the DMA in its shell, 96 runs (WAIT 0,
  1 and 3; 4 seeds; 8 modes), every job against an oracle of the whole
  memory. Request acceptance and JOB_CYCLES are checked against a cycle
  model, and the counters against the shell's own counts.
- **`dma-mutants.log`** (`-detail`): 38 planted bugs in the engine, each
  caught.
- **`core-shell-equiv.1.log`, `.2.log`** (run twice, identical): the CPU
  shell from 8809f72 and the current one on 879 runs, cycle for cycle and
  RVFI record for record. The current data cache keeps its answer in a
  register, asserted equal to its definition every cycle.
- **The rest:** the fabric suites (every mode, with `equiv_fabric`), the
  NPU suites, the core's suites, the L1 unit tests, the performance gate and
  the board simulation.

**`fpga/`: the final RTL's three in-context builds** (`make fpga-aster-soc`,
Vivado 2025.1, the whole device with the Zynq PS, 10 ns):

| Build | Directives | Setup (WNS) | Hold (WHS) | Worst path |
| --- | --- | ---: | ---: | --- |
| `f1` | default | +0.010 ns | +0.024 ns | hart 1's data cache's request address, through the fabric's arbitration, to bank 1's write enable (7 levels, 7.3 ns route) |
| `f1-o5` | `over=0.5` | +0.162 ns | +0.034 ns | `paths/` |
| `f1-o5asm` | `over=0.5;place=AltSpreadLogic_medium` | +0.270 ns | +0.021 ns | hart 1's data cache, within itself |

- **All three are clean:** every net routed, no DRC error (only DSP
  pipelining warnings), and no failing endpoint among about 78,000.
- **None reaches the +0.3 ns margin the owner set in 20.2.** The best is
  +0.270 ns (`f1-o5asm`); the default build gives +0.010 ns. phase20.md
  records that 20.3 continues with restructuring that keeps every cycle.
- **`reproducibility.txt`:** `f1` and `f1-o5asm`, each built twice from the
  same sources. The configuration data and the slack are the same; only the
  header's date differs.
- **Utilization** (`f1`): 34,731 LUTs (65.3%), 23,713 flip-flops, 77 block
  RAM tiles and 27 DSPs. Against 20.2's r19 that is 1,046 LUTs and 1,587
  flip-flops more.
- **The netlist guard** now counts port W's 64 data registers in place of
  the ARM side's 32, which are gone.
- **Each build's `signoff.txt`** includes the netlist guard: NPU block RAMs
  40, NPU write-data registers 32, port W's data registers 64.
- **`bitstream.sha256`:** each bitstream's hash. The bitstreams are not
  kept.

**`timing/builds.csv`:** every in-context build of 20.3 that reached
sign-off, the last under each tag, with its slack and directives:
- four RTL revisions × the three strategies;
- the repeats of d3, f1 and f1-o5asm.

phase20.md's timing table is drawn from it. d1's first attempts are not
listed. They stopped at the netlist guard, which still counted the ARM
side's write-data registers, before reaching timing.
