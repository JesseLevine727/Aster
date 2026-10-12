# Milestone 20.5, step 4: the final configuration

The text of record is the "Step 4" section of 20.5 in
[`../../../phase20.md`](../../../phase20.md). The plan is
[`../../../tuning.md`](../../../tuning.md) §2, §8 and §11. `SHA256SUMS`
covers every file here.

**The final capture:** every family from one clean commit, b96e48b
(`build/matrix/f5-*`), on the final RTL (8 KiB caches, the owner's adoption),
each R entry also at 2 and 4 KiB (`--caches 2,4`), so the cache geometry axis
is captured, not planned.

| Family | Captured | Unsupported | Determinism | Cold/warm pairs | Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU | 132 | 0 | 5/5 | 66/66 | 27 s |
| coherence | 1,674 | 370 | 56/56 | 238/238 | 189 s |
| DSP | 1,786 | 42 | 60/60 | 168/168 | 366 s |
| ML | 564 | 22 | 19/19 | 74/74 | 4,137 s |
| ECG | 4,345 | 207 | 145/145 | 475/475 | 867 s |
| memory | 1,150 | 16 | 39/39 | 464/464 | 109 s |
| NPU GEMM | 1,801 | 56 | 61/61 | 149/149 | 520 s |
| DMA | 12,480 | 0 | 416/416 | 4,992/4,992 | 536 s |
| **All** | **23,932** | **713** | **801/801** | **6,626/6,626** | |

Each family: `scripts/matrix.py run --family F --caches 2,4 --repeat-every 30`.

**The bundle** (`scripts/matrix_bundle.py build/matrix/f5-*`: PASS):
- **`manifest.json.xz`:** the manifest, xz-compressed (34 MB as JSON).
  - 26,186 entries: 23,932 captured with 32,918 records, and 2,254
    unsupported, each with its reason. None failed, none planned.
  - The unsupported: 713 the runner's (two workers or the pipeline on one
    hart, and the like), and 1,541 matrix.md §7's configurations and
    methods.
- **`raw-<family>.tar.xz`:** each family's records and consoles, with its
  determinism repeats.
- **`overlap.json`:** `scripts/matrix_overlap.py`'s classes for every
  two-worker record. No stamping faults.
- **`gates.json`:** the scaling and v1 gates (`scripts/matrix_gates.py`, the
  adopted set).
- **`ecg-overlap.txt`, `ecg-overlap.json`:** soc.md §11's ECG gate from the
  stamped twins' stage lines (`scripts/matrix_ecg_overlap.py`). The two-hart
  pipelines overlap in all 504 of their stamped windows; v1's pipeline is
  sequential per chunk in all 378 of its.

**`checks/`:**
- **`second-capture.log`:** a second full capture from 7cee16e, clean
  (`build/matrix/f5b-*`), against the first (`scripts/matrix_compare.py
  --identical`). All 23,932 entries have the same firmware, simulation and
  records, byte for byte. 7cee16e adds only CoreMark's official run, behind
  its own flag, so no build changed.
- **soc.md §10's checks on the final RTL** (`make` each, at b96e48b):
  - `soc-tests.log`: the two-hart programs, the devices, Phase 19's gate
    programs and 18.7's board programs on the regression builds;
  - `dma-tests.log`: the DMA's shell;
  - `matrix-sims-tests.log`: the two-hart programs on every matrix
    simulation;
  - `core-shell-equiv.log`: the CPU shell against 20.4's signed-off
    87c6b0a, cycle for cycle and RVFI record for record;
  - `core-aster-l1-tests.log`, `core-aster-l1-sizes.log`,
    `core-aster-l1-unit.log`, `l1-span-mutants.log`: the L1 caches;
  - `fabric-tests.log`: the fabric's shell. The fabric has not changed
    since 20.1, so this is a re-check.

**`timing/`:**
- **`timing.txt`:** the final RTL is step 2's, with only the default cache
  size changed (shown). The final image's configuration data is hashed
  again.
- **`builds-8k.csv`:** step 2's ten 8 KiB strategies.
