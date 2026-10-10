# Milestone 20.5, step 2: the cache geometry

The text of record is the "Step 2: the cache geometry" section of
[`../../../../phase20.md`](../../../../phase20.md). The plan is
[`../../../../tuning.md`](../../../../tuning.md) §4.2. `SHA256SUMS` covers every
file here.

The RTL is e86b47e's: `CACHE_BYTES` in `rtl/aster_core/aster_l1d.sv`,
`aster_l1i.sv` and `aster_l1_ram.sv`, `rtl/soc/aster_soc.sv`,
`aster_soc_devices.sv` and `aster_soc_ip.v`. f246a11 changed only the DMA
oracle (`scripts/matrix.py`), and both captures were made from it, clean.

**The captures** (`scripts/matrix.py`, from f246a11):
- **`manifest-g2-geometry.json.xz`, `raw-g2-geometry.tar.xz`:** every R warm
  entry on the default seed and the gate workloads cold, at 4, 2 and 8 KiB:
  3,372 entries, all captured, determinism 85 of 85.
  `matrix.py run --r-only --caches 2,8 --repeat-every 40`.
- **`manifest-g2-layouts.json.xz`, `raw-g2-layouts.tar.xz`:** the gate
  workloads warm and cold at each size and each layout, L0–L7: 1,776
  entries, 1,752 captured. The other 24 are GEMM 128×64×128 at L4, L6 and
  L7, which do not fit main memory. Determinism 44 of 44.
  `matrix.py run --gate-workloads --r-only --caches 2,8 --layouts L1,L2,L3,L4,L5,L6,L7 --repeat-every 40`.
- **`runs-identical.txt`:** the 222 entries both captures share, identical in
  firmware, simulation and records.

The manifests are xz-compressed copies of the runs' `manifest.json`. The raw
archives hold each entry's records and console.

**The comparisons:**
- **`geometry.json`, `geometry.txt`:** each 2 and 8 KiB entry against its
  4 KiB twin, every window. Per family and size; the gates at each size and
  layout; and each gate workload's verdict across the layouts, on the eight
  and on decision 2's five.
  `scripts/matrix_geometry.py build/matrix/g2-geometry build/matrix/g2-layouts --json geometry.json`.
- **`gates-2k.*`, `gates-4k.*`, `gates-8k.*`:** the scaling and v1 gates at
  each size, at R and L0.
  `scripts/matrix_gates.py build/matrix/g2-geometry --cache-kib K --json gates-Kk.json`.

**4 KiB unchanged:**
- **`golden-replay.txt`:** all 3,885 of 20.4's R firmware images give the
  same console and end on e86b47e's SoC. `scripts/matrix_golden.py build/matrix/m5-* --golden-rev e86b47e`.
- **`timing/reproducibility.txt`:** e86b47e's 4 KiB board design on o5asm-mgi
  is byte-identical to step 1's.
- **`checks/core-shell-equiv.log`, `checks/compare-shells.log.xz`:** the CPU
  shell against 0d8bfa0's, 879 of 879 runs the same, RVFI record for record.

**`checks/`, the tests at each size** (all on e86b47e's tree):
- **`l1-unit.log`:** `make core-aster-l1-unit`, 200 seeds on every L1 build,
  at 2 and 8 KiB too.
- **`l1-span-mutants.log`:** `make l1-span-mutants`, 45 of 45 caught at 2, 4
  and 8 KiB.
- **`l1-mutants.log`:** the core campaign's 35 L1 mutants
  (`scripts/mutation_campaign.py`), all caught.
- **`core-l1-sizes.log`:** `make core-aster-l1-sizes`, the CPU shell at 2 and
  8 KiB in lockstep with Spike.
- **`core-l1-tests.log`:** `make core-aster-l1-tests` at 4 KiB, with the
  self-tests.
- **`core-ports-tests.log`:** `make core-ports-tests`, the PicoRV32 shell,
  which shares the bench.
- **`soc-tests.log`:** `make soc-tests` in full.
- **`soc-tests-sizes.log`:** `scripts/soc_tests.py --sims soc_l1_2k,soc_l1_8k`.

**`timing/`, the board design in context at 10 ns:**
- **`builds.csv`:** each build's slack, area and configuration-data hash. It
  covers:
  - 2 and 8 KiB, each on ten strategies (`make fpga-aster-soc SOC_PARAMS=CACHE_BYTES=N SOC_DIRECTIVES=...`);
  - step 1's ten at 4 KiB;
  - the rebuilds.
- **`reproducibility.txt`:** the best build at each size, rebuilt from the
  same sources, gives the same configuration data and slack.
- **`paths-*/`:** the best builds' worst setup paths and their endpoints by
  block (`scripts/timing/soc_paths.tcl`, 200 paths, under 0.4 ns).

**`adoption/`, after the owner adopted 8 KiB** (10 October 2026; the SoC's
default is 8 KiB, 2 and 4 KiB the matrix's axis):
- **`bitstream.txt`:** the default, built with no parameters on o5asm-mgi, is
  byte-identical to step 2's 8 KiB build, so step 2's 8 KiB timing is the
  default's. Its row is in `timing/builds.csv`.
- **`replay-8k.txt`:** step 2's 8 KiB firmware, 1,708 images, gives the same
  console and end on the new default build (`replay_8k.py`, with
  `scripts/matrix_golden.py`'s replay).
- **`soc-tests.log`:** `make soc-tests` in full. The regression builds keep
  4 KiB (soc.md §10.4), and 18.7's 99 programs also run on the regression
  build at 8 KiB against the CPU shell at 8 KiB.
- **`matrix-variants.log`:** soc-tests' two-hart programs on every matrix
  variant (now at 8 KiB) and on soc_l1_2k and soc_l1_4k: 102 checks.
