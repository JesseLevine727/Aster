# Milestone 20.3 — the DMA

The text of record is the "Milestone 20.3" section of
[`../../../phase20.md`](../../../phase20.md), with soc.md §13's
"Clarifications in 20.3". `SHA256SUMS` covers every file here.

The exit (phase20.md's milestone table) has three parts: soc.md §10.5, the
DMA against CPU copies at every size and alignment in the SoC, and 10 ns in
context. All of it is on the final RTL, g2. Six RTL files changed:
- `rtl/dma/aster_dma2.sv`;
- `rtl/soc/aster_soc.sv` and `rtl/soc/aster_soc_devices.sv`;
- `rtl/aster_core/aster_l1d.sv` and `rtl/aster_core/aster_l1i.sv`;
- `rtl/fabric/aster_fabric.sv`.

Concatenated in that order they hash to sha256 `c3c4a8fc1f8ba189…`, for the
simulations and the builds alike. Each build's source snapshot is
byte-identical to the commit's.

**`capture-1/`, `capture-2/`** (`make soc-tests`, run twice; every file is
the same in both, as §10.4's determinism asks):
- **`dma.soc_dev.log`, `dma.soc_dev_w3.log`:** the DMA program
  (`software/tests/soc_dma.c`, through v1's driver, unchanged), on the
  device builds without and with three added memory waits:
  - **The copies:** 3,392 copies, each against a CPU copy of the same
    source: every length 0–40 at all 64 alignment pairs, and 12 larger sizes
    up to 4 KiB at all 64.
  - **The limits:** copies from LIMIT_LO, a source and a destination each
    ending exactly at LIMIT_HI, and a 16,000-byte misaligned copy.
  - **Through the driver:** the errors, ABORT's prefix, and the interrupt.
  - **The counters:** each job's tallies, plus their reads and writes
    accepted, stalls and invalidated lines against the fabric counters'.
  - **Hart 1:** refused by the driver, and its 64 cached lines invalidated
    by the DMA's writes.
  - **Contention:** hart 1's traffic, and an NPU GEMM still running when a
    4 KiB job ends.
  - **RESUMEs while counting** during a job.
  - **The DMA against the CPU copy** in cycles: phase20.md's table.
- **`soc-tests.log`:** the two-hart programs on both device builds: litmus,
  hart 1's reset stress, the runtime's dispatch and join, the devices and
  the DMA. The testbench also checks every port R answer against memory at
  its acceptance, with the bank rule (about 97,000 a build), and every port
  W write through the memory checker and the snoop checks (about 94,000).
  The litmus, reset-stress and dispatch figures are 20.2's exactly.
- **`soc-board.log`:** 18.7's 99 programs on the regression build, each as
  in the CPU shell, cycle for cycle and record for record. The CPU shell's
  caches keep full tags, the SoC's narrow ones.
- **`soc-gates.log`:** soc.md §10.4's comparison on the gate programs,
  every cycle traced; the figures are 20.2's exactly.
- **`make-soc-tests.out`:** also covers the devices against 8809f72's, in
  lockstep for 3 million cycles, with `window_adds` against the golden's
  own counter in every cycle.

In every simulation here, the caches' narrow tags are asserted equal to full
ones every cycle.

**`suites/`** (the final RTL):
- **`dma-tests.log`** (`-detail`): the DMA in its shell, 96 runs:
  - every job against an oracle of the whole memory;
  - request acceptance and JOB_CYCLES against a cycle model;
  - the counters against the shell's own counts;
  - jobs up to 48,119 bytes.
- **`dma-mutants.log`** (`-detail`): 39 planted bugs in the engine, each
  caught.
- **`fabric-tests.log`:** every mode, with `equiv_fabric`, the fabric
  against 20.2's golden every cycle. The shell now also aims
  store-conditionals 64 KiB from their reservation (a required bin).
- **`fabric-mutants.log`:** 37 of 37 caught in the fabric (with the new
  top-bit mutant), and 31 of 31 in the reference.
- **`core-aster-l1-unit.log`:** both caches, 200 seeds each, including both
  at TAG_SPAN 17 with every kept tag bit varied.
- **`l1-span-mutants.log`** (`make l1-span-mutants`): each kept tag bit
  dropped from each narrow compare, 15 of 15 caught on every seed. The
  unmutated caches pass the same seeds.
- **`core-shell-equiv.1.log`, `.2.log`** (run twice, identical): the CPU
  shell from 8809f72 and the current one on 879 runs, cycle for cycle and
  RVFI record for record.
- **`host-tests.log`:** 355 host tests (in `make check`), passing again;
  the mutation campaign's anchors had gone stale in 20.2.
- **`core-aster-mutants.log`:** the core's planted-bug campaign on the final
  RTL, 109 of 113 caught. `core-aster-mutants-reanchored.log` holds the ten
  re-anchored mutants alone, the 114th among them (`csr-write-on-trap`).
  - In all, 109 of 114 are caught.
  - The five not caught are Phase 18's accepted ones (18.3's four
    equivalent or unobservable, 18.6's `sys-retire-at-commit`), unchanged.
- **The rest:** the NPU suites, the core's suites, the performance gate and
  the board simulation.

**`fpga/`: the final RTL's builds** (`make fpga-aster-soc`, Vivado 2025.1,
the whole device with the Zynq PS, 10 ns):

| Build | Directives | Setup (WNS) | Hold (WHS) | Worst path |
| --- | --- | ---: | ---: | --- |
| `g2-o5end` (the sign-off build: the owner, 8 October 2026) | `over=0.5;place=ExtraNetDelay_high` | **+0.375 ns** | +0.022 ns | hart 0's result into its DOT8 multiplier; only 8 endpoints within +0.45 ns |
| `g2` | default | +0.031 ns | +0.020 ns | a data cache's tag lookup into its request registers |

- **Both are clean:** every net routed, no DRC error (DSP pipelining
  warnings only), and the netlist guard met.
- **`g2-o5end`:** 33,975 LUTs (63.9%), 23,677 flip-flops, 77 block RAM
  tiles and 27 DSPs.
- **`reproducibility.txt`:** g2-o5end built twice from the same sources,
  with the same configuration data and slack.
- **The margin holds for this build, not across placements.** g2's other
  strategies give +0.031 to +0.188 ns, with a median of about +0.1.
- **`bitstream.sha256`:** each bitstream's hash; the bitstreams are not
  kept.

**`timing/builds.csv`:** all 30 in-context builds of 20.3, with slack and
directives:
- six RTL revisions;
- the strategy sweep on g2;
- the repeats;
- o5 END on f1's RTL, built from a worktree of 99ef503.

phase20.md's timing tables are drawn from it. d1's first attempts are not
listed: they stopped at the netlist guard, before timing.
