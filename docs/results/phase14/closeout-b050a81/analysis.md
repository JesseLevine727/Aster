# Phase 14 analysis: crossovers and bottlenecks

All numbers are validated AsterBench v10 records from the retained studies.
Every workload's checksum matches its independent oracle. Ratios below 1.0 mean
the specialized path is **slower**; the project reports those deliberately.

## 1. Memory latency and the memory-bound crossover

`MEMORY_WAIT_CYCLES` sweep, minimal SoC, cache on (cycles, ratio vs `wait0`):

| Workload | wait0 | wait1 | wait4 | wait16 | wait64 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `strided` | 8 039 (1.00×) | 9 087 (1.13×) | 12 231 (1.52×) | 24 807 (3.09×) | 75 111 (9.34×) |
| `sort_search` | 2 062 008 (1.00×) | 2 183 301 (1.06×) | 2 547 180 (1.24×) | 4 035 464 (1.96×) | 10 054 136 (4.88×) |
| `fft` | 2 949 470 (1.00×) | 3 057 473 (1.04×) | 3 381 482 (1.15×) | 4 678 670 (1.59×) | 9 871 262 (3.35×) |
| `conv2d` | 5 764 692 (1.00×) | 5 820 652 (1.01×) | 5 988 532 (1.04×) | 6 660 052 (1.16×) | 9 346 132 (1.62×) |

**Finding:** memory latency dominates exactly the workloads with the least
compute per byte. `strided` degrades 9.3× while `conv2d` degrades only 1.6×.
The `wait0→wait64` span moves `strided` from compute-bound to memory-bound; the
crossover point for this design sits between `wait4` and `wait16`.

## 2. L1 cache geometry: the cache can lose

Cache geometry sweep, minimal SoC (cycles):

| Configuration | `strided` | `sort_search` | `fft` | `conv2d` |
| --- | ---: | ---: | ---: | ---: |
| `nocache` | **6 728** | **1 931 799** | **2 822 210** | **5 696 577** |
| `w4n8` | 8 039 | 2 082 888 | 3 107 710 | 5 853 567 |
| `w4n16` | 8 039 | 2 062 008 | 2 949 470 | 5 764 692 |
| `w4n32` | 8 039 | 2 017 608 | 2 948 026 | 5 729 972 |
| `w8n16` | 9 060 | 2 017 009 | 3 021 436 | 5 769 172 |

**Finding:** for every workload and every geometry, the cached build is
**slower** than the uncached build. `strided` — a pure streaming access with no
reuse — is 19% slower with a cache. Growing the cache (`w4n32`) recovers a few
percent on `sort_search`/`fft` but never closes the gap, and a larger line
(`w8n16`) hurts `strided` further. On this minimal SoC the L1 is a net
slowdown; the benefit is expected to appear with larger working sets or a
shared L2. The L2 half of the README question is not measurable in v1.0.

## 3. Core scaling

`HART_COUNT` / worker sweep, coherent SoC (cycles):

| Configuration | Workload | Cycles | Retired |
| --- | --- | ---: | ---: |
| 1 hart, 1 worker | `reduce_scalar` | 289 251 | 53 299 |
| 2 harts, 1 worker | `reduce_scalar` | 289 251 | 53 299 |
| 2 harts, 2 workers | `reduce_parallel` | 222 374 | 41 111 |

**Finding:** adding a second hart with one worker changes nothing (the work is
single-threaded). Splitting the reduction across two workers gives **1.30×**.
The workload is memory-bound, so the scaling is far below 2×; this is the
coherence/bandwidth limit, not a compute limit.

## 4. Compute placement: when the accelerator loses

32×32 convolution, K=5, 4 iterations, size 1024, coherent SoC:

| Engine | Cycles | Retired | vs scalar |
| --- | ---: | ---: | ---: |
| scalar CPU | 5 764 692 | 704 346 | 1.00× |
| Xasterdot8 | 9 786 108 | 1 145 218 | **0.59×** |
| 4×4 NPU | 4 636 733 | 531 493 | 1.24× |

**Finding:** at this problem size the Xasterdot8 path is **slower** than the
scalar direct convolution. The im2col lowering retires 1.6× more instructions
than the scalar loop, and that data movement dominates the four-wide dot
product. The NPU wins only modestly (1.24×) for the same reason. Specialization
is not automatically a win; the crossover depends on problem size and on how
much the lowering costs.

## 5. Research questions not measured in this pass

| Question | Status |
| --- | --- |
| Cost of coherence and false sharing | measured in Phases 6/10; not re-swept here |
| DMA vs CPU copy crossover | measured in Phase 7; not re-swept here |
| NPU offload overhead vs problem size | partially (one size); a size sweep is 14.4 follow-up |
| Accelerator dimensions (2×2/4×4/8×8) | **not measured**; the NPU engine hardcodes 4×4 in ~10 places, so this is a bounded but non-trivial RTL change deferred to 14.5 |
| Performance per area / energy | Phase 16 |
| FPGA vs SKY130 | Phase 15/16 |

## 6. What this says for v2

- The memory-latency result is the strongest argument for a **shared L2**: the
  design is latency-bound well before it is compute-bound, and a cache that
  currently loses on small working sets should win on large ones.
- The cache and dot8 results show that **overhead, not peak throughput,
  decides** many crossovers. A v2 dot8 path should fuse im2col, and the L1
  should be evaluated on larger working sets.
- The 1.30× two-core scaling is a bandwidth ceiling worth re-measuring after L2.
