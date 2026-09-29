# Phase 17 retained same-top v1 baseline (56067a15815a)

P17-A5/A6 evidence: every supported method of the v1 workloads on one declared
all-engine `aster_coherent_soc` configuration (2 harts, L1 4×16, L2 off, DMA,
DOT8, 4×4 NPU), plus the `aster_minimal` CPU workloads as a separately named
data point. Each capture ran once plus one determinism repeat under two memory
models. `scripts/audit_phase17_baseline.py` re-validates every record with its
strict validator and independent oracle, re-checks engine counters against
shapes from the workload sources, and binds the source hash; see `manifest.json`.

| Memory model | Meaning |
| --- | --- |
| `sync1` | physical target: synchronous memory, one wait cycle (PYNQ-Z1 and SKY130 tops) |
| `async0` | idealization: asynchronous zero-wait memory (no physical target) |

Cycle counts are RTL simulation cycles at the recorded 31,250,000 Hz configuration clock; they are not a timed
operating point.

## `sync1`

| Workload | Method | Cycles | Speedup vs scalar |
| --- | --- | ---: | ---: |
| Conv2D 32×32, K=5 | coherent scalar | 9,729,350 | 1.00× |
| | DOT8 | 10,338,912 | 0.94× |
| | NPU | 4,837,408 | 2.01× |
| Reduction 1024 words ×4 | 1 worker | 301,794 | 1.00× |
| | 2 workers | 233,112 | 1.29× |
| MNIST MLP 784→32→10 (per image) | scalar | 2,066,084 | 1.00× |
|  | multicore | 1,054,607 | 1.96× |
|  | dot8 | 884,629 | 2.34× |
|  | npu | 433,903 | 4.76× |
| Streaming ECG, 16 chunks × 64 | heterogeneous | 1,528,505 | — |
| CIFAR-10 CNN, 20 images | NPU | 65,568,218 | — |

- Conv2D NPU: 19,600 array-step cycles over 4 jobs; the array computes in 0.41% of the workload's cycles, and 25% of its PEs do useful work when it does (N=1).
- CIFAR: the NPU is busy 48.1% of the time but its array computes in 0.42% of the cycles.
- MNIST accuracy on the 32 retained images: 30/32 (identical logits for every method).

`aster_minimal` (separate top, not a direct engine speedup baseline): strided 9,087, sort_search 2,183,301, fft 3,057,473, conv2d 5,820,652, coremark 1,922,272, dhrystone 3,128,553 cycles.

## `async0`

| Workload | Method | Cycles | Speedup vs scalar |
| --- | --- | ---: | ---: |
| Conv2D 32×32, K=5 | coherent scalar | 9,586,170 | 1.00× |
| | DOT8 | 10,016,476 | 0.96× |
| | NPU | 4,644,700 | 2.06× |
| Reduction 1024 words ×4 | 1 worker | 289,394 | 1.00× |
| | 2 workers | 222,512 | 1.30× |
| MNIST MLP 784→32→10 (per image) | scalar | 2,040,337 | 1.00× |
|  | multicore | 1,030,843 | 1.98× |
|  | dot8 | 867,505 | 2.35× |
|  | npu | 403,571 | 5.06× |
| Streaming ECG, 16 chunks × 64 | heterogeneous | 1,470,057 | — |
| CIFAR-10 CNN, 20 images | NPU | 61,016,482 | — |

- Conv2D NPU: 19,600 array-step cycles over 4 jobs; the array computes in 0.42% of the workload's cycles, and 25% of its PEs do useful work when it does (N=1).
- CIFAR: the NPU is busy 46.1% of the time but its array computes in 0.45% of the cycles.
- MNIST accuracy on the 32 retained images: 30/32 (identical logits for every method).

`aster_minimal` (separate top, not a direct engine speedup baseline): strided 8,039, sort_search 2,062,008, fft 2,949,470, conv2d 5,764,692, coremark 1,757,429, dhrystone 2,737,452 cycles.

## Unsupported combinations

| Workload | Method | Reason |
| --- | --- | --- |
| conv2d | multicore | v1 has no two-worker Conv2D firmware |
| reduction | dot8/npu | an integer sum has no dot-product or GEMM mapping |
| ecg | scalar/dot8/npu-only | v1 ECG is one fixed heterogeneous pipeline; per-engine variants are not implemented |
| cifar | scalar/multicore/dot8 | v1 CIFAR firmware is NPU-only |

## Notes

- `minimal_coremark` is a one-iteration, fixed-work CRC correctness check; it is not
  a CoreMark score (the port treats cycles as milliseconds and the run is far shorter
  than CoreMark's ten-second rule). Dhrystone is an adapted port; raw cycles only.
- MNIST uses the per-image AsterBench v9 record. Its NPU fields describe only the
  last (fc2) NPU job of each image; the audit checks them against that layer's
  shape rather than treating them as totals.
- The `sync1` model is the memory timing of the physical tops; `async0` is an
  idealization that no physical target implements.
