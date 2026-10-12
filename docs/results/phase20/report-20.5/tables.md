# The Phase 20 report's tables (from b96e48b; scripts/phase20_report_data.py)

## 1. Which engine wins (R, warm, end to end, cycles; the winner in bold)

**GEMM (int8, M×N×K):** v1's kernels (scalar, DOT8, two cores of DOT8; nothing packed), 19.4's packed DOT8 GEMM on one and two cores (B packed inside the window; at npu.md §7's three sizes, the coherence family's gemm_dot8_*), and the NPU. The same inputs and checksum in each.

| Shape | Scalar | DOT8 | Two cores | DOT8 packed | Two cores packed | NPU | NPU over best CPU | Best CPU GOPS | NPU GOPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1×1×1 | 177 | 224 | — | — | — | **130** | 1.4× | 0.00113 | 0.00154 |
| 4×4×16 | 3,912 | 3,191 | 1,791 | — | — | **179** | 10.0× | 0.0286 | 0.286 |
| 16×16×64 | 207,117 | 145,998 | 69,066 | — | — | **643** | 107.4× | 0.0474 | 5.1 |
| 32×32×32 | — | 329,192 | 160,666 | — | — | **1,410** | 113.9× | 0.0408 | 4.65 |
| 64×64×64 | — | 2,235,177 | 1,115,834 | 204,636 | 105,010 | **5,321** | 19.7× | 0.499 | 9.85 |
| 96×96×96 | — | 7,388,975 | 3,700,478 | 680,971 | 342,610 | **15,273** | 22.4× | 0.516 | 11.6 |
| 128×64×128 | — | 8,531,612 | 4,265,365 | 805,300 | 401,471 | **17,741** | 22.6× | 0.522 | 11.8 |
| 32×1×784 | 293,230 | 197,035 | 97,704 | — | — | **3,509** | 27.8× | 0.0514 | 1.43 |
| 784×1×25 | 282,256 | 238,298 | 116,289 | — | — | **4,541** | 25.6× | 0.0337 | 0.863 |
| 64×1×64 | 49,736 | 35,223 | 17,877 | — | — | **758** | 23.6× | 0.0458 | 1.08 |

**Applications:** one core scalar, two cores, one core DOT8, the NPU (CIFAR: direct)

| Workload | Scalar | Two cores | DOT8 | NPU |
| --- | --- | --- | --- | --- |
| Conv2D direct, 32×32, K = 5, four iterations | 1,132,560 | 596,227 | 670,000 | **84,251** |
| Conv2D im2col (M = 784, N = 1, K = 25) | 1,506,161 | 772,922 | 1,510,015 | **573,184** |
| MNIST MLP 784→32→10, 32 images, one at a time | 9,602,117 | 4,801,167 | 6,481,451 | **186,289** |
| CIFAR-10 CNN 16×16×3, 20 images | 56,639,463 | 31,046,419 | 44,623,274 | **4,080,849** |

**Dot product (int8), by K:**

| k | dot8 | multicore | npu | scalar |
| --- | --- | --- | --- | --- |
| 0 | 159 | 326 | 114 | **100** |
| 1 | 167 | 346 | 130 | **119** |
| 7 | 251 | 376 | **132** | 217 |
| 8 | 239 | 379 | **132** | 228 |
| 64 | 666 | 686 | **150** | 869 |
| 256 | 2,100 | 1,751 | **220** | 2,989 |
| 1024 | 7,679 | 5,967 | **505** | 11,437 |
| 4096 | 30,062 | 22,993 | **1,658** | 45,370 |

**FIR (256 outputs), by taps:**

| k | dot8 | multicore | npu | scalar |
| --- | --- | --- | --- | --- |
| 4 | 29,709 | 14,857 | **893** | 29,231 |
| 8 | 37,709 | 20,489 | **1,021** | 40,495 |
| 16 | 53,709 | 31,753 | **1,277** | 63,023 |
| 32 | 85,709 | 54,277 | **1,797** | 108,079 |
| 64 | 149,709 | 99,333 | **2,831** | 198,191 |
| 256 | 533,711 | 369,676 | **9,018** | 738,863 |

**FFT (Q15), by points:**

| k | multicore | scalar |
| --- | --- | --- |
| 256 | **197,585** | 314,610 |

**The reductions:**

| Case | One core | Two cores | Speed-up |
| --- | --- | --- | --- |
| reduce_fill | 73,918 | 37,656 | 1.96× |
| reduce_v1 | 73,945 | 58,686 | 1.26× |

## 2. Working set, access pattern, banks and memory latency

**Working set (one hart, bytes):** cycles, warm

| bytes | Cycles |
| --- | --- |
| 512 | 346,796 |
| 1024 | 345,452 |
| 2048 | 344,780 |
| 4096 | 344,444 |
| 8192 | 344,276 |
| 16384 | 405,632 |
| 32768 | 405,590 |
| 49152 | 405,576 |

**Strided reads (stride, words):** cycles, warm

| stride | Cycles |
| --- | --- |
| 1 | 135,240 |
| 2 | 155,735 |
| 4 | 196,898 |
| 8 | 197,122 |
| 16 | 197,570 |
| 64 | 200,258 |

**Random walk (bytes):** cycles, warm

| bytes | Cycles |
| --- | --- |
| 1024 | 9,249 |
| 4096 | 36,907 |
| 16384 | 193,176 |
| 32768 | 424,890 |

**Sequential reads (bytes):** cycles, warm

| bytes | Cycles |
| --- | --- |
| 256 | 2,340 |
| 1024 | 9,252 |
| 4096 | 36,899 |
| 16384 | 167,971 |
| 32768 | 335,907 |

**Streams:** one hart; two harts in different banks; two harts in the same bank

| Case | Cold | Warm |
| --- | --- | --- |
| stream_diff_banks | 21,931 | 21,732 |
| stream_one_hart | 21,645 | 21,583 |
| stream_same_bank | 22,446 | 21,715 |

**Memory latency:** cycles with added waits against R's one, the geometric mean of every warm entry (their count), and the largest at +4

| Family | +1 wait | +2 waits | +4 waits | Largest at +4 |
| --- | --- | --- | --- | --- |
| cpu | 1.003× (6) | 1.007× (6) | 1.017× (6) | 1.08× (cpu/dhrystone/scalar/soc_w4/warm) |
| coherence | 1.080× (164) | 1.163× (164) | 1.304× (164) | 2.23× (coherence/lrsc_counter/multicore/soc_w4/warm/n1024) |
| dsp | 1.052× (148) | 1.105× (148) | 1.201× (148) | 2.30× (dsp/fir__hot/npu/soc_w4/warm/k256) |
| ml | 1.054× (44) | 1.107× (44) | 1.206× (44) | 1.90× (ml/mnist_mlp_npu__2h_hot/npu/soc_w4/warm) |
| ecg | 1.012× (333) | 1.025× (333) | 1.050× (333) | 1.19× (ecg/ecg_pipeline_overlap__fir4_split/pipeline/soc_w4/warm/c16t8) |
| memory | 1.055× (58) | 1.107× (58) | 1.210× (58) | 1.67× (memory/read_strided/scalar/soc_w4/warm/s4) |
| npu_gemm | 1.066× (149) | 1.122× (149) | 1.222× (149) | 2.30× (npu_gemm/gemm_64x128x64/npu/soc_w4/warm) |
| dma | 1.108× (624) | 1.175× (624) | 1.311× (624) | 2.94× (dma/copy/dma/soc_w4/warm/b32768s1d0c0) |

**The same, by method:**

| Method | +1 wait | +2 waits | +4 waits | Largest at +4 |
| --- | --- | --- | --- | --- |
| one core | 1.018× (499) | 1.035× (499) | 1.068× (499) | 1.67× (memory/read_strided/scalar/soc_w4/warm/s4) |
| two cores | 1.056× (216) | 1.114× (216) | 1.212× (216) | 2.23× (coherence/lrsc_counter/multicore/soc_w4/warm/n1024) |
| DOT8 | 1.006× (172) | 1.013× (172) | 1.029× (172) | 1.29× (npu_gemm/gemm_1x1x1/dot8/soc_w4/warm) |
| the NPU | 1.171× (108) | 1.339× (108) | 1.667× (108) | 2.30× (dsp/fir__hot/npu/soc_w4/warm/k256) |
| the DMA | 1.206× (324) | 1.337× (324) | 1.618× (324) | 2.94× (dma/copy/dma/soc_w4/warm/b32768s1d0c0) |
| ECG pipelines | 1.018× (207) | 1.037× (207) | 1.074× (207) | 1.19× (ecg/ecg_pipeline_overlap__fir4_split/pipeline/soc_w4/warm/c16t8) |

## 3. Cache capacity and coherence

**Cache capacity:** cycles with 2 and 4 KiB caches against R's 8 KiB, the geometric mean of every R entry, warm (its largest, and the count), and the largest's entry

| Family | 2 KiB | 4 KiB | The largest |
| --- | --- | --- | --- |
| cpu | 1.137× (max 1.57×, 6) | 1.004× (max 1.01×, 6) | 2 KiB: cpu/strided/scalar/soc_l1_2k/warm; 4 KiB: cpu/coremark/scalar/soc_l1_4k/warm |
| coherence | 1.039× (max 1.44×, 164) | 1.021× (max 1.44×, 164) | 2 KiB: coherence/lock_sum__split_hot/multicore/soc_l1_2k/warm/n129; 4 KiB: coherence/lock_sum__split_hot/multicore/soc_l1_4k/warm/n129 |
| dsp | 1.024× (max 1.34×, 148) | 1.014× (max 1.28×, 148) | 2 KiB: dsp/dot/multicore/soc_l1_2k/warm/k1; 4 KiB: dsp/dot/multicore/soc_l1_4k/warm/k1 |
| ml | 1.050× (max 1.37×, 44) | 1.016× (max 1.09×, 44) | 2 KiB: ml/cifar_cnn_dot8__hot/dot8/soc_l1_2k/warm; 4 KiB: ml/cifar_cnn_dot8/dot8/soc_l1_4k/warm |
| ecg | 1.094× (max 1.51×, 459) | 1.027× (max 1.33×, 459) | 2 KiB: ecg/ecg_pipeline_overlap__fir4_split_stages/pipeline/soc_l1_2k/warm/c32t16; 4 KiB: ecg/ecg_pipeline_overlap_f8__fir4_copy_stages/pipeline/soc_l1_4k/warm/c16t8 |
| memory | 1.027× (max 1.31×, 58) | 1.006× (max 1.18×, 58) | 2 KiB: memory/memcpy/cpu_copy/soc_l1_2k/warm/b4096s0d0; 4 KiB: memory/working_set/scalar/soc_l1_4k/warm/b8192 |
| npu_gemm | 1.046× (max 1.95×, 149) | 1.023× (max 1.95×, 149) | 2 KiB: npu_gemm/gemm_128x64x128/multicore/soc_l1_2k/warm; 4 KiB: npu_gemm/gemm_128x64x128/multicore/soc_l1_4k/warm |
| dma | 1.165× (max 1.91×, 624) | 1.004× (max 1.32×, 624) | 2 KiB: dma/copy/cpu_copy/soc_l1_2k/warm/b0s3d6c1; 4 KiB: dma/copy/cpu_copy/soc_l1_4k/warm/b8192s0d0c0 |

**Coherence:** one worker against two, by items (a speed-up under 1 is two workers losing)

| Case | Items | One worker | Two workers | Two's speed-up |
| --- | --- | --- | --- | --- |
| atomic_add | 2 | 46 | 508 | 0.09× |
| atomic_add | 129 | 1,203 | 1,082 | 1.11× |
| atomic_add | 1024 | 9,257 | 5,128 | 1.81× |
| cas_counter | 2 | 116 | 567 | 0.20× |
| cas_counter | 129 | 5,579 | 3,261 | 1.71× |
| cas_counter | 1024 | 44,063 | 22,605 | 1.95× |
| false_shared | 2 | 41 | 534 | 0.08× |
| false_shared | 129 | 1,071 | 1,039 | 1.03× |
| false_shared | 1024 | 8,230 | 4,618 | 1.78× |
| lock_sum | 2 | 105 | 579 | 0.18× |
| lock_sum | 129 | 3,784 | 5,058 | 0.75× |
| lock_sum | 1024 | 30,772 | 39,470 | 0.78× |
| lrsc_counter | 2 | 72 | 532 | 0.14× |
| lrsc_counter | 129 | 3,007 | 1,988 | 1.51× |
| lrsc_counter | 1024 | 23,592 | 12,337 | 1.91× |
| padded | 2 | 47 | 535 | 0.09× |
| padded | 129 | 1,204 | 1,040 | 1.16× |
| padded | 1024 | 9,253 | 4,631 | 2.00× |
| shared_mix | 2 | 264 | 708 | 0.37× |
| shared_mix | 129 | 12,594 | 6,955 | 1.81× |
| shared_mix | 1024 | 99,421 | 51,185 | 1.94× |

## 4. The DMA

**The crossover** (step 3b's sweep on the eight layouts, results/phase20/tuning-20.5/step3b/crossover.txt): the DMA beats the CPU's copy from 113 bytes aligned, 89 at the same offset in a word, and 61 at different offsets (against the shift copy, from 15 bytes).

**Copies, aligned, warm, the destination not cached:** end to end (the DMA's set-up included), and the DMA's bytes a busy cycle (peak 8)

| Bytes | CPU copy | DMA | DMA bytes/cycle | Faster |
| --- | --- | --- | --- | --- |
| 1 | 70 | 158 | 0.14 | CPU |
| 2 | 77 | 158 | 0.29 | CPU |
| 3 | 84 | 158 | 0.43 | CPU |
| 4 | 79 | 158 | 0.57 | CPU |
| 7 | 99 | 158 | 1.00 | CPU |
| 8 | 87 | 158 | 1.14 | CPU |
| 15 | 115 | 206 | 1.88 | CPU |
| 16 | 94 | 206 | 2.00 | CPU |
| 31 | 145 | 206 | 3.10 | CPU |
| 32 | 116 | 206 | 3.20 | CPU |
| 63 | 189 | 206 | 4.50 | CPU |
| 64 | 160 | 206 | 4.57 | CPU |
| 127 | 277 | 206 | 5.77 | DMA |
| 128 | 248 | 206 | 5.82 | DMA |
| 255 | 453 | 184 | 6.71 | DMA |
| 256 | 424 | 184 | 6.74 | DMA |
| 511 | 805 | 221 | 7.30 | DMA |
| 512 | 776 | 221 | 7.31 | DMA |
| 1024 | 1,480 | 315 | 7.59 | DMA |
| 2048 | 2,903 | 404 | 7.82 | DMA |
| 4096 | 5,704 | 662 | 7.91 | DMA |
| 8192 | 11,350 | 1,180 | 7.95 | DMA |
| 16384 | 29,775 | 2,216 | 7.98 | DMA |
| 32768 | 59,471 | 4,251 | 7.99 | DMA |

**Freed time:** a DMA copy beside unrelated work (a checksum over `work` words, on hart 0 or hart 1) against the same done one after the other

| Where the work runs | Bytes | Work | Serial | Overlapped | Saved |
| --- | --- | --- | --- | --- | --- |
| overlap_h0 | 1024 | 256 | 2,406 | 2,329 | 3.2% |
| overlap_h0 | 1024 | 2048 | 16,804 | 16,729 | 0.4% |
| overlap_h0 | 4096 | 256 | 2,788 | 2,319 | 16.8% |
| overlap_h0 | 4096 | 2048 | 17,174 | 16,730 | 2.6% |
| overlap_h0 | 16384 | 256 | 4,342 | 2,319 | 46.6% |
| overlap_h0 | 16384 | 2048 | 18,739 | 16,718 | 10.8% |
| overlap_h1 | 1024 | 256 | 2,964 | 2,787 | 6.0% |
| overlap_h1 | 1024 | 2048 | 20,453 | 20,275 | 0.9% |
| overlap_h1 | 4096 | 256 | 3,354 | 2,814 | 16.1% |
| overlap_h1 | 4096 | 2048 | 20,827 | 20,283 | 2.6% |
| overlap_h1 | 16384 | 256 | 4,908 | 2,846 | 42.0% |
| overlap_h1 | 16384 | 2048 | 22,392 | 20,322 | 9.2% |

## 5. NPU geometry and tile shape

**Sustained MACs a cycle in the kernel window (utilization of the array's peak),** warm, by geometry

| Shape | 4×4, 32-bit, 1 strip | 4×4, 32-bit, 2 strips | 4×4, 64-bit, 1 strip | 4×4, 64-bit, 2 strips | 8×8, 64-bit, 1 strip | 8×8, 64-bit, 2 strips (R) |
| --- | --- | --- | --- | --- | --- | --- |
| 128×64×128 | 14.4 (90%) | 15.5 (97%) | 15.1 (94%) | 15.7 (98%) | 50.6 (79%) | 59.3 (93%) |
| 96×96×96 | 14.6 (91%) | 15.3 (96%) | 15.2 (95%) | 15.6 (98%) | 51.8 (81%) | 58.2 (91%) |
| 64×128×64 | 14.4 (90%) | 15.0 (94%) | 15.1 (94%) | 15.5 (97%) | 49.7 (78%) | 52.8 (83%) |
| 64×64×64 | 13.9 (87%) | 14.9 (93%) | 14.7 (92%) | 15.4 (96%) | 44.8 (70%) | 49.9 (78%) |
| 32×32×32 | 11.6 (72%) | 13.5 (85%) | 12.7 (80%) | 14.4 (90%) | 21.8 (34%) | 24.3 (38%) |
| 16×16×64 | 9.7 (60%) | 11.5 (72%) | 11.4 (71%) | 13.0 (81%) | 23.1 (36%) | 28.6 (45%) |
| 64×8×64 | 8.9 (56%) | 14.0 (88%) | 10.5 (66%) | 14.6 (92%) | 18.8 (29%) | 27.7 (43%) |
| 32×1×784 | 3.1 (19%) | 3.7 (23%) | 5.0 (32%) | 7.1 (44%) | 6.7 (10%) | 7.3 (11%) |
| 784×1×25 | 2.0 (12%) | 2.7 (17%) | 2.6 (16%) | 3.9 (25%) | 3.6 (6%) | 4.4 (7%) |

At R the best shape sustains 59.3 MACs a cycle: 11.9 GOPS (peak 64 MACs a cycle, 12.8 GOPS). Area per sustained MAC a cycle (Phase 19, npu.md §4.6): 8×8 uses 0.0124 of the device, 4×4 0.0223.

## 6. ECG

**v1's case (16 chunks of 64 samples, 16 taps), end to end:** samples a second, and a chunk's worst latency against its deadline (the chunk's period at 360 Hz), cycles

| Pipeline | State | Cycles | Samples/s | Worst latency | Deadline |
| --- | --- | --- | --- | --- | --- |
| ecg_scalar | cold | 177,975 | 575,362 | 11731 | 17,777,777 |
| ecg_scalar | warm | 177,291 | 577,581 | 11145 | 17,777,777 |
| ecg_dot8 | cold | 200,981 | 509,501 | 13468 | 17,777,777 |
| ecg_dot8 | warm | 200,001 | 511,997 | 12586 | 17,777,777 |
| ecg_dot8__fir4 | cold | 55,471 | 1,846,010 | 4545 | 17,777,777 |
| ecg_dot8__fir4 | warm | 54,223 | 1,888,498 | 3440 | 17,777,777 |
| ecg_pipeline_v1 | cold | 201,798 | 507,438 | 13681 | 17,777,777 |
| ecg_pipeline_v1 | warm | 200,439 | 510,879 | 12575 | 17,777,777 |
| ecg_pipeline_v1__fir4 | cold | 56,177 | 1,822,810 | 4561 | 17,777,777 |
| ecg_pipeline_v1__fir4 | warm | 54,855 | 1,866,740 | 3471 | 17,777,777 |
| ecg_pipeline_overlap | cold | 176,420 | 580,433 | 14264 | 17,777,777 |
| ecg_pipeline_overlap | warm | 175,512 | 583,436 | 13006 | 17,777,777 |
| ecg_pipeline_overlap__fir4 | cold | 36,884 | 2,776,272 | 5066 | 17,777,777 |
| ecg_pipeline_overlap__fir4 | warm | 35,603 | 2,876,162 | 4289 | 17,777,777 |
| ecg_pipeline_overlap__fir4_copy | cold | 37,788 | 2,709,855 | 5314 | 17,777,777 |
| ecg_pipeline_overlap__fir4_copy | warm | 36,313 | 2,819,927 | 4380 | 17,777,777 |
| ecg_pipeline_overlap__fir4_split | cold | 33,023 | 3,100,869 | 5298 | 17,777,777 |
| ecg_pipeline_overlap__fir4_split | warm | 31,531 | 3,247,598 | 4134 | 17,777,777 |

The final configuration's ECG at v1's case is ecg_pipeline_overlap__fir4_split (the adopted set, matrix_gates.ADOPTED: the FIR on DOT8 directly, hart 1 staging and moving the next chunk).

