# Milestone 20.5, step 3: the tuning

The text of record is the "Step 3: the tuning" section of
[`../../../../phase20.md`](../../../../phase20.md). The plan is
[`../../../../tuning.md`](../../../../tuning.md) §3–§5. `SHA256SUMS` covers
every file here.

**The captures** (`scripts/matrix.py run ... --out build/matrix/NAME`), all
from the clean commit 410fbce. The manifests are xz-compressed copies of each
run's `manifest.json`; the raw archives hold each entry's records and
console.
- **`t3-dma-fine`:** the copy helper's finer sweep on the eight layouts:
  30,848 entries, all captured, determinism 155 of 155. `--family dma_fine --layouts L1,...,L7 --repeat-every 200`.
- **`t3-split-fine`:** the split policy's sweep on the eight layouts:
  2,112 entries, all captured, determinism 43 of 43. `--family split_fine --layouts L1,...,L7 --repeat-every 50`.
- **`t3-ecg`, `t3-dsp`, `t3-coherence`, `t3-ml`:** each family in full,
  with the tuned variants in every configuration their originals have:
  ECG 2,142 captured and 126 unsupported, DSP 752 and 22, coherence 708 and 217, ML 234 and 12, none failed. Every unsupported entry carries its reason (the one-hart SoC
  for two-hart programs, and the like). `--family F --repeat-every 30`.
- **`t3-judge`:** the tuned variants and their originals at R on the eight
  layouts, warm and cold: 5,344 captured, 16 unsupported, none failed,
  determinism 54 of 54. The unsupported are GEMM 128×64×128 at the layouts
  that do not fit main memory: its two-worker original at L4, L6 and L7, its
  one-worker method at L4, and `__place` at L4 to L7. So `__place` is judged
  on four layouts.
  `--family ecg --family ml --family dsp --family coherence --sims soc_dev
  --no-stamped --layouts L1,...,L7 --repeat-every 100`, with
  `--match '^(ecg/|ml/(cifar_cnn_npu|mnist_mlp_npu)|dsp/(conv2d_\w+_npu|dot(__split)?/multicore)|coherence/(reduce_|gemm_dot8_|atomic_add|lrsc_counter|cas_counter|lock_sum|false_shared|padded|shared_mix))'`.

**The analyses** (`scripts/`, at this step's last commit; the captures, at 410fbce, do not depend on them):
- **`crossover.txt`, `crossover.json`:** the copy helper's thresholds from
  `t3-dma-fine` (`matrix_crossover.py`), each with its range of crossovers
  and the cached/uncached split.
- **`split.txt`, `split.json`:** the split policy re-derived from
  `t3-split-fine` and checked against `software/runtime/aster_split.h`
  (`matrix_split.py`). Each size's verdict too: one worker, two, or mixed
  (the bands below the thresholds).
- **`judge.txt`, `judge.json`:** every tuned variant against its original
  over the layouts, and each stacked change against the variant it extends
  (`matrix_gates.STACKED_ON`). `matrix_judge.py t3-judge --check-adopted`
  checks the adopted set (`matrix_gates.ADOPTED`, `ADOPTED_POINTS`) against
  the judgment: PASS. It also lists R's sweep points where an adopted variant
  is not adopted.
- **`others.txt`, `others.json`:** the same judgment over the families' full
  captures, at L0, in every configuration. It lists the adopted variants'
  configurations that do not win, which phase20.md reports beside the win
  (tuning.md §3). `matrix_judge.py t3-ecg t3-dsp t3-coherence t3-ml
  --check-adopted`.
- **`gates.txt`, `gates.json`:** the scaling and v1 gates at R, at 8 KiB,
  with the adopted set (`matrix_gates.py` over the four families' runs).
  The CPU kernels are not in these runs, since step 3 changed none of them.
  Their v1 figures are step 2's at 8 KiB (`../geometry/gates-8k.txt`).
- **From `matrix_tuning_report.py build/matrix OUT`:**
  - **`mnist-batching.txt`:** MNIST's cycles an image and a batch's
    latency, per batch size, on one and two harts (`t3-ml`).
  - **`ecg-stages.txt`:** the stamped ECG pipelines' median stage times and
    chunk period at v1's case (`t3-ecg`).
  - **`ecg-outputs.txt`:** §5's outputs at v1's case: samples a second, and
    each chunk's worst and mean latency against its 360 Hz deadline.
  - **`dma-freed.txt`:** §4.1's freed time: the DMA's busy cycles in the
    variants' copies.
  - **`bank-conflicts.txt`:** §4.4: the fabric's bank conflicts in the
    reductions and the GEMMs, at L0 and over the layouts (`t3-judge`).
  - **`icache.txt`:** §4.4's code against the instruction cache: the warm
    windows' instruction misses at L0 and their fewest over the layouts, for
    the originals and the adopted variants (`t3-judge`).

Every figure in phase20.md's record comes from these files, with three
exceptions:
- the "features on hart 1" trial, which was not kept in the tree;
- the 7,092 entries whose firmware 410fbce left unchanged and whose cycles
  equal the 74bc781 capture's, which is not in this bundle;
- the 160 short counts that led to the DMA oracle's stack exemption, found
  in an earlier capture of the sweep (472e822). The exemption applies here
  too; the oracle's message does not say which entries it covered.
