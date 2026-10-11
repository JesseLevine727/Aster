# Milestone 20.5, step 3b: the owner's four decisions on step 3

The text of record is the "Step 3b" section of
[`../../../../phase20.md`](../../../../phase20.md). The plan is
[`../../../../tuning.md`](../../../../tuning.md) §3–§5. Step 3's evidence,
which this step's supersedes where they differ, is in [`../step3/`](../step3/).
`SHA256SUMS` covers every file here.

**The captures** (`scripts/matrix.py run ... --out build/matrix/NAME`), all
from the clean commit 6bc46c2. Each has an xz-compressed copy of its
`manifest.json`, and a raw archive of its records and consoles.
- **`t3b-dma-fine`:** the copy helper's sweep on the eight layouts. It has
  v1's fair copy, the DMA, and at different offsets the shift copy
  (`copy__aster`), at six offset pairs: 85,632 entries, all captured,
  determinism 429 of 429. `--family dma_fine --layouts L1,...,L7
  --repeat-every 200`.
- **`t3b-split-fine`:** the split policy's sweep on the eight layouts: 2,112
  entries, all captured, determinism 43 of 43. `--family split_fine
  --layouts L1,...,L7 --repeat-every 50`.
- **`t3b-ecg`, `t3b-dsp`, `t3b-coherence`, `t3b-ml`:** each family in full,
  with the tuned variants and the code-placement twins (`__hot`, `_hot`) in
  every configuration. None failed:

  | Run | Captured | Unsupported | Determinism |
  |---|---|---|---|
  | `t3b-ecg` | 3,411 | 207 | 114 of 114 |
  | `t3b-dsp` | 1,470 | 42 | 49 of 49 |
  | `t3b-coherence` | 1,320 | 370 | 44 of 44 |
  | `t3b-ml` | 446 | 22 | 15 of 15 |

  Each unsupported entry carries its reason. `--family F --repeat-every 30`.
- **`t3b-judge`:** the tuned variants, the twins and their originals at R on
  the eight layouts, warm and cold: 9,272 captured, 24 unsupported (GEMM
  128×64×128 at the layouts that do not fit main memory), none failed,
  determinism 93 of 93. `--family ecg --family ml --family dsp --family
  coherence --sims soc_dev --no-stamped --layouts L1,...,L7 --repeat-every
  100`, with
  `--match '^(ecg/|ml/(cifar_cnn_npu|mnist_mlp_npu)|dsp/(conv2d_\w+_npu|dot(__split)?(__hot|_hot)?/multicore)|coherence/(reduce_|gemm_dot8_|atomic_add|lrsc_counter|cas_counter|lock_sum|false_shared|padded|shared_mix))'`.

**The analyses** (`scripts/`, at this step's last commit; the captures do not
depend on them):
- **`crossover.txt`, `crossover.json`:** the copy helper's four thresholds
  re-derived from `t3b-dma-fine` and checked against
  `software/runtime/aster_copy.c` (`matrix_crossover.py`): PASS, 113, 89, 61
  and 15 bytes.
- **`copy-judge.txt`, `copy-judge.json`:** the shift copy against v1's fair
  copy at each different-offset point, over the layouts
  (`matrix_judge.py t3b-dma-fine`).
- **`split.txt`, `split.json`:** the split policy re-derived from
  `t3b-split-fine` and checked against `software/runtime/aster_split.h`
  (`matrix_split.py`): PASS.
- **`judge.txt`, `judge.json`:** every tuned variant and twin against its
  original over the layouts, each stacked change and each variant's twin
  against the variant it extends. `matrix_judge.py t3b-judge
  --check-adopted` checks the adopted set (`matrix_gates.ADOPTED`,
  `ADOPTED_POINTS`): PASS.
- **`twins.txt`:** the twins' own verdicts, drawn from `judge.json`.
- **`others.txt`, `others.json`:** the same over the families' full
  captures, at L0, in every configuration. It lists the adopted variants'
  configurations that do not win, reported beside the win. PASS.
- **`gates.txt`, `gates.json`:** the scaling and v1 gates at R, at 8 KiB,
  with the adopted set. The CPU kernels are not in these runs (step 2's
  figures: `../geometry/gates-8k.txt`).
- **From `matrix_tuning_report.py build/matrix OUT --prefix t3b`:**
  `mnist-batching.txt`, `ecg-stages.txt`, `ecg-outputs.txt`,
  `dma-freed.txt`, `bank-conflicts.txt`, `icache.txt` (step 3's figures,
  from step 3b's captures, the twins counted).

Every figure in phase20.md's step 3b record comes from these files or from
step 3's, with two exceptions the record marks:
- the CPU loops tried against v1's fair copy, a scratch sample at L0;
- the shift copy's host correctness check, the watchdog's.
