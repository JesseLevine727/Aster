# AsterBench workloads (v10 and v11)

AsterBench v10 is the generic workload record family added after the Phase 11
closeout. It covers the README workload catalog beyond the phased experiments.
Earlier record versions (v2–v9) are unchanged. Since Phase 17-A4 the
`aster_minimal` workloads (strided, sort/search, FFT, `conv2d`, CoreMark,
Dhrystone) still emit v10, while the coherent-SoC workloads (reduction, the
Conv2D engines, ECG, CIFAR) emit [AsterBench v11](asterbench-v11.md), which adds
per-hart DOT8 events and cumulative, requester-attributed DMA/NPU totals.

Phase 17 adds `conv2d_scalar_coh` as an auxiliary same-top baseline for the
coherent DOT8/NPU Conv2D runs. The original `conv2d` workload remains the legacy
`aster_minimal` CPU data point; these two scalar results are not interchangeable.
`scripts/phase17_conv_baseline.py` audits the coherent scalar/DOT8/NPU records as
one matching diagnostic matrix, including NPU totals against an independent
cumulative shape oracle.

Each v10 workload emits one line (the v11 field list is in
[`asterbench-v11.md`](asterbench-v11.md#record-shape)):

```text
ASTERBENCH,version=10,name=<name>,category=<cpu|memory|dsp|ml|system>,status=PASS,
size=<bytes>,iterations=<n>,param=<p>,seed=0x........,checksum=0x........,
clock_hz=...,l1=...,sync_memory=...,line_words=...,line_count=...,memory_wait=...,
cycles=0x................,retired=0x................,memory_transactions=0x...,
backing_transactions=0x...,cache_accesses=0x...,cache_misses=0x...,dma_bytes=0x...,
accelerator_cycles=0x...
```

`scripts/asterbench_v10.py` and `scripts/asterbench_v11.py` validate the
structure strictly; `scripts/workload_reference.py verify [--version 11]`
recomputes each workload's checksum independently. Run one workload with:

```sh
make workload WORKLOAD=fft
make coremark
make dhrystone
make reduce REDUCE_WORKERS=1        # or 2
make conv-engine CONV_ENGINE=npu    # or dot8
make conv-engine CONV_ENGINE=scalar_coh # coherent-top CPU baseline
make phase17-conv-matrix            # same top/data plus configuration/counter audit
make workloads                      # all of the above; part of make check
```

## Workloads

| Name | Category | Engine(s) | Semantics | Independent check |
| --- | --- | --- | --- | --- |
| `strided` | memory | CPU | one word every `param` words | checksum oracle |
| `sort_search` | cpu | CPU | insertion sort + binary search | checksum oracle |
| `coremark` | cpu | CPU | official CoreMark (Apache-2.0) | CoreMark CRC `0xe714` |
| `dhrystone` | cpu | CPU | Dhrystone 2.1, `iterations` runs | canonical final values |
| `fft` | dsp | CPU | N=256 fixed-point radix-2 FFT | checksum oracle |
| `conv2d` | dsp | CPU | 32×32 image, 5×5 kernel | checksum oracle |
| `conv2d_scalar_coh` | dsp | scalar CPU on `aster_coherent_soc` | Same Conv2D as DOT8/NPU, but on the same top | checksum oracle |
| `conv2d_dot8` | dsp | Xasterdot8 | im2col GEMM via packed INT8 | checksum oracle |
| `conv2d_npu` | dsp | NPU | im2col GEMM via the 4×4 array | checksum oracle |
| `reduce_scalar` | cpu | 1 hart | sum of a shared-RAM array | checksum oracle |
| `reduce_parallel` | cpu | 2 harts | split sum, partial published | checksum oracle |
| `streaming_ecg` | system | CPU + DMA + DOT8 + NPU | per chunk: stage, DMA, DOT8 FIR, features, NPU classify | checksum oracle |
| `cifar_cnn` | ml | NPU | 16×16 tiny CNN: conv 3→16, conv 16→32, ReLU/pool, fc 128→10 | artifact reference |

`conv2d_scalar_coh`, `conv2d_dot8` and `conv2d_npu` run on the coherent
all-engine SoC with the same logical input and must produce the same checksum.
`conv2d` remains the legacy `aster_minimal` scalar workload and is not a direct
cycle-speedup baseline for those coherent-top methods. The Phase 17 matrix uses
the coherent scalar path for same-top comparisons; its host audit rejects
configuration mismatches and DMA/NPU counter cross-attribution.

## Known limitations (documented, not hidden)

- **Dhrystone** is timing-first. It now checks the canonical Dhrystone 2.1 final
  values (`Int_Glob=5`, `Bool_Glob=1`, `Ch_1='A'`, `Ch_2='B'`, `Arr_1_Glob[8]=7`,
  `Arr_2_Glob[8][7]=runs+10`, `Discr=0`, `Enum_Comp=2`, `Int_Comp=17`, and the
  string), and folds them into the checksum. The target has no FPU and the
  toolchain ships no soft-float, so the vendored file is compiled with `float`
  as integer; that computation is outside the timed loop, so the measured cycles
  are unaffected.
- **CoreMark** runs the official `core_main.c` with an Aster port. Ticks are
  treated as milliseconds so CoreMark's "must run for at least 10 s" reporting
  rule passes; the record reports raw cycles, not that derived figure.
- **FFT** is a scaled fixed-point transform: each stage shifts right by one, so
  the output is `DFT/N`. The firmware and the oracle use the identical integer
  operations.
- **Reduction** reports the primary hart's counters; its cycles include the join
  wait, so the wall-clock is captured. CPU counters are the primary hart's; v11
  adds per-hart DOT8 events only.
- **Conv2D** engine variants materialize an im2col matrix in shared RAM; the
  materialization cost is inside the measured window. On the same coherent
  top, `conv2d_dot8` is roughly equal to (slightly slower than) the coherent
  scalar path; the older "0.59×" figure compared it with the `aster_minimal`
  scalar run.
- **Streaming ECG** is the heterogeneous demo (Phase 12). Each of the 16 chunks
  of 64 samples is staged by the CPU, moved by DMA, filtered by Xasterdot8, and
  classified by the NPU while the primary hart orchestrates and the secondary
  runs the FIR. The samples are a real PhysioNet MIT-BIH Arrhythmia Database
  record 100 MLII segment (samples 0–1023, ADC zero 1024, scale 4), baked by
  `scripts/gen_ecg_data.py` into `software/benchmarks/workload_ecg_data.h` and
  `docs/results/phase12/ecg_segment.json`. "Real time" means sustained per-chunk
  throughput, not hard deadlines: the workload does not use the Phase 12.5
  timer or Phase 12.6 interrupts. Historical v10 captures may include NPU
  device stores in `dma_bytes`, and v10 `accelerator_cycles` is the last NPU
  job's active-array cycles; v11 records report cumulative, requester-owned
  DMA and NPU totals.
- **CIFAR-10** is a tiny quantized CNN: the 32×32 RGB input is downscaled 2×2
  to 16×16 to fit the 32 KiB NPU-visible RAM, and the network is two 3×3
  convolutions (3→16, 16→32), each with ReLU and 2×2 max pool, and a 128→10
  fully connected layer.
  Weights and activations are per-tensor INT8 with frozen requantization. The full CIFAR-10 test
  accuracy is 58.1% and the integer model matches the float model on the
  20-image balanced subset; this is a small CNN demonstration, not a
  competitive CIFAR-10 result. `scripts/cifar_train.py` retrains, `cifar_reference.py`
  recomputes the integer network, and `cifar_export.py` emits the headers.

## Provenance

`vendor/coremark/` and `vendor/dhrystone/` retain their upstream sources and
`UPSTREAM.md`; the Aster ports live under `software/benchmarks/`. v10 and v11
records bind the clock, cache geometry and memory timing so a captured record
is self-describing.
