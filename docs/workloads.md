# AsterBench workloads (v10)

AsterBench v10 is the generic workload record family added after the Phase 11
closeout. It covers the README workload catalog beyond the phased experiments.
Earlier record versions (v2–v9) are unchanged.

Each workload emits one line:

```text
ASTERBENCH,version=10,name=<name>,category=<cpu|memory|dsp|ml|system>,status=PASS,
size=<bytes>,iterations=<n>,param=<p>,seed=0x........,checksum=0x........,
clock_hz=...,l1=...,sync_memory=...,line_words=...,line_count=...,memory_wait=...,
cycles=0x................,retired=0x................,memory_transactions=0x...,
backing_transactions=0x...,cache_accesses=0x...,cache_misses=0x...,dma_bytes=0x...,
accelerator_cycles=0x...
```

`scripts/asterbench_v10.py` validates the structure strictly;
`scripts/workload_reference.py` recomputes each workload's checksum
independently. Run one workload with:

```sh
make workload WORKLOAD=fft
make coremark
make dhrystone
make reduce REDUCE_WORKERS=1        # or 2
make conv-engine CONV_ENGINE=npu    # or dot8
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
| `conv2d_dot8` | dsp | Xasterdot8 | im2col GEMM via packed INT8 | checksum oracle |
| `conv2d_npu` | dsp | NPU | im2col GEMM via the 4×4 array | checksum oracle |
| `reduce_scalar` | cpu | 1 hart | sum of a shared-RAM array | checksum oracle |
| `reduce_parallel` | cpu | 2 harts | split sum, partial published | checksum oracle |
| `streaming_ecg` | system | CPU + DMA + DOT8 + NPU | per chunk: stage, DMA, DOT8 FIR, features, NPU classify | checksum oracle |

`conv2d`, `conv2d_dot8` and `conv2d_npu` must all produce the same output and
therefore the same checksum; that equality is the cross-engine correctness check.

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
  wait, so the wall-clock is captured. Per-hart fields are not part of v10.
- **Conv2D** engine variants materialize an im2col matrix in shared RAM; the
  materialization cost is inside the measured window and is why `conv2d_dot8`
  is slower than scalar for this shape.
- **Streaming ECG** is the heterogeneous demo (Phase 12). Each of the 16 chunks
  of 64 samples is staged by the CPU, moved by DMA, filtered by Xasterdot8, and
  classified by the NPU while the primary hart orchestrates and the secondary
  runs the FIR. The sample source is a deterministic synthetic ECG-like
  waveform (a real PhysioNet segment can be substituted); "real time" means
  sustained per-chunk throughput, not hard deadlines, because there are no
  interrupts or timers. The record's `dma_bytes` is the DMA engine's byte-event
  count and `accelerator_cycles` is the last NPU job's active-array cycles.

## Provenance

`vendor/coremark/` and `vendor/dhrystone/` retain their upstream sources and
`UPSTREAM.md`; the Aster ports live under `software/benchmarks/`. The v10 record
binds the clock, cache geometry and memory timing so a captured record is
self-describing.
