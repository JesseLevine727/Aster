# Phase 20 report: two Aster cores, banks, the DMA and the NPU

This report answers the README's research questions 1 to 6 (question 7 was dropped with SKY130 on 1 October 2026)
from Phase 20's final evidence, as [`tuning.md`](tuning.md) §9 asks. Every figure is from one of these:
- **the final capture** (`results/phase20/matrix-20.5`): one clean commit, b96e48b, every family;
- **its tables** (`results/phase20/report-20.5/tables.md`), generated from that capture by
  `scripts/phase20_report_data.py`;
- **step 3b's tuning evidence** (`results/phase20/tuning-20.5/step3b`);
- **the board run** (`results/phase20/board-20.5`).

The plan, the gates and the decisions are in [`phase20.md`](phase20.md), [`soc.md`](soc.md), [`matrix.md`](matrix.md)
and [`tuning.md`](tuning.md).

## The SoC measured

The final configuration (soc.md §2; matrix.md §1's R):
- **Harts:** two Aster cores (RV32IMA, Zicsr, Zifencei, and DOT8, an int8 dot product instruction).
- **Caches:** each hart has an 8 KiB instruction cache and an 8 KiB write-through data cache. Both are
  direct-mapped with 16-byte lines, and each data cache snoops every other writer.
- **Memory:** 96 KiB of main memory in four 64-bit block-RAM banks, interleaved by cache line, with the physical
  two-cycle block RAM.
- **Fabric:** connects seven requesters to the banks.
- **Engines:** the DMA, and Phase 19's NPU (an 8×8 int8 array with two A strip buffers on a 64-bit port).
- **Clock:** 100 MHz on the PYNQ-Z1 (xc7z020).
- **The final build** (`results/phase20/matrix-20.5/timing/final-build`):
  - setup slack +0.306 ns and hold slack +0.040 ns, with no failing endpoint;
  - every net routed, no DRC error, no methodology violation;
  - 72.75% of LUTs, 60.0% of block RAM and 12.3% of DSPs (27), against soc.md's 80% limit on those three;
    92.3% of slices are occupied.

## How it was measured

- **The capture:** every workload of matrix.md, with every method that applies and every axis.
  - 26,186 configurations: 23,932 run with 32,918 records; the other 2,254 cannot apply, each with its reason.
  - None failed. Every record passes AsterBench v12's validator and its workload's oracle.
  - A second full capture is identical in every record, and 801 of 801 determinism repeats match.
- **Two windows** (phase17-plus.md §3):
  - the kernel: the compute, with its data in place;
  - end to end: dispatch, packing, the engines' set-up, data movement, compute and join.

  The two windows are never compared across methods. The tables below are end to end unless they say otherwise.
- **Units:**
  - cycles are hart 0's window;
  - seconds are at 100 MHz, the records' clock; the board's FCLK0 measured 99.9991 to 100.0001 MHz on the loads of
    record, within 0.001%;
  - GOPS counts a multiply-accumulate (MAC) as two operations.
- **R and the axes:**
  - R is warm caches, at the default seed and the layout of record (L0).
  - Each axis changes one thing from R: memory waits, cache size, harts and workers, the data cache, and the NPU
    geometry.
  - Tuning was judged on eight memory layouts (tuning.md §3): a variant is adopted only where it wins on all
    eight, warm and cold.
- **The board:** every R program is run on the board, the cycle of its end, its status and its console byte for
  byte as simulated, on two separate loads of the final image (step 5, below).

## The headline figures at R

At R, each one a program that ends on the board exactly as simulated, cycle for cycle, on two loads (step 5,
below):

| Workload | Engine | Figure |
| --- | --- | --- |
| GEMM, int8, 128×64×128 | the NPU | 11.8 GOPS end to end; 59.3 MACs a cycle in the kernel window, 93% of the array's peak |
| MNIST MLP 784→32→10 | the NPU, fed by both harts | 5,389 cycles an image cold, about 18,560 images a second |
| CIFAR-10 CNN 16×16×3 | the NPU (direct), both harts | 112,103 cycles an image cold, about 890 images a second |
| Streaming ECG (1,024 samples) | both harts, DOT8 | 33,023 cycles cold: 3.10 million samples a second; worst chunk latency 53 µs against a 178 ms period |
| CoreMark (the 2K performance run; its validation run validated too, as CoreMark's run rules ask) | one hart | 218.74 iterations a second, 2.19 CoreMark/MHz |
| Two-hart scaling (the gate's workloads) | two harts | 1.95× to 2.01× over one at R, end to end (1.93× with cold caches; 1.91× to 2.02× across every configuration of the gate) |
| A 32 KiB copy | the DMA | 7.99 bytes a busy cycle (the peak is 8); 7.7 end to end, 771 MB/s |

**CoreMark's score.** The toolchain's libgcc has no soft floating point, so CoreMark runs its integer path.
- The integer path's own "Iterations/Sec : 240" is 2,400 iterations over 10 whole seconds, so it is not the score.
- The score, 218.737829, is 2,400 iterations in 10.972 s (1,097,203,904 cycles) at the nominal 100 MHz. The port
  prints it from the ticks exactly, as CoreMark's "CoreMark 1.0" line. At FCLK0 as measured on its board loads
  (99.9986 and 99.9993 MHz) it is 218.735 and 218.736.
- The run is CoreMark's 2K performance run (seeds 0, 0, 0x66), with its known CRCs and its own verdict
  ("Correct operation validated"), over the ten-second minimum.
- CoreMark's run rules also ask the validation run (seeds 0x3415, 0x3415, 0x66) to validate. It runs the same 2,400
  iterations in 11.029 s, with CoreMark's known 2K validation CRCs and its verdict.
- The matrix's oracle re-derives every printed figure from the ticks.

## The gates (soc.md §11): all met

| Gate | Result |
| --- | --- |
| Correctness | every record PASS, with its oracle; none failed |
| Records | 32,918, every one validated, from one clean commit |
| Counter attribution | the NPU's and the DMA's totals hold against the fabric's counters in every record |
| Wins and losses | published (below) |
| Two-hart scaling of at least 1.8× | at R 1.95× to 2.01× end to end (R's build warm and cold, 1.93× to 2.01×; kernel windows 1.94× to 2.01×); across every configuration of each, 1.91× to 2.02× (kernel windows from 1.90×), on the parallel reduction and the three multicore GEMMs |
| Faster than v1 | every one of v1's eleven retained workloads, by 3.13× (sort/search) to 106.8× (Conv2D) |
| ECG overlap | the two-hart pipelines overlap in all 504 of their stamped windows; v1's pipeline is labelled sequential per chunk (all 378) |
| Timing and area | +0.306 ns, no failing endpoint; 72.75% of LUTs, 60.0% of block RAM, 12.3% of DSPs (the limit is 80%) |
| The board | all 5,254 R programs as simulated on each of two loads of the final image, and CoreMark's performance and validation runs on two more (below) |

`scripts/audit_phase20.py` re-checks all of these, read-only:
- It re-derives the gates from the raw records.
- It re-runs the validator and the totals on every record.
- It reads the final build's own reports and the board's comparisons.
- Its self-test plants a fault for each check and shows each one caught.

## 1. When does one CPU, two CPUs, DOT8, the DMA or the NPU win for the same work?

The full tables are in `tables.md` §1. All are at R, warm, end to end, in cycles.

**The NPU wins every int8 matrix workload, from the smallest.** The CPU methods are of two kinds:
- **v1's kernels** (scalar, DOT8, and DOT8 on two cores), which pack nothing, at every shape;
- **19.4's packed DOT8 GEMM** on one and two cores, which packs B inside the window. It runs at npu.md §7's three
  sizes, as the scaling gate's kernel.

Each method gets the same inputs and must give the same checksum.

| GEMM | Best CPU method | The NPU | NPU's speed-up | NPU GOPS |
| --- | --- | --- | --- | --- |
| 1×1×1 | 177 (scalar) | 130 | 1.4× | 0.0015 |
| 4×4×16 | 1,791 (v1's DOT8 on two cores) | 179 | 10.0× | 0.29 |
| 16×16×64 | 69,066 (v1's DOT8 on two cores) | 643 | 107× | 5.1 |
| 64×64×64 | 105,010 (packed DOT8 on two cores) | 5,321 | 19.7× | 9.9 |
| 96×96×96 | 342,610 (packed DOT8 on two cores) | 15,273 | 22.4× | 11.6 |
| 128×64×128 | 401,471 (packed DOT8 on two cores) | 17,741 | 22.6× | 11.8 |
| 32×1×784 (matrix-vector) | 97,704 (v1's DOT8 on two cores) | 3,509 | 28× | 1.4 |

- **Packing matters as much as the engine on the CPUs.** The packed kernel is 10.6× to 10.9× faster than v1's at the
  three sizes (two cores: 4,265,365 → 401,471 cycles at 128×64×128). At the other shapes only v1's kernels were
  measured, so the NPU's lead there (1.4× to 225×) is over unpacked CPU code, and would be smaller over a packed
  one.
- **The CPUs reach 0.52 GOPS on a GEMM:** two cores with the packed kernel at 128×64×128. v1's unpacked kernels
  stay under 0.052. The NPU reaches 11.8.
- **Conv2D direct** (32×32, K = 5, four iterations): the NPU is 7.1× faster than two cores (84,251 against
  596,227 cycles).
- **MNIST** (32 images): the NPU is 26× faster than two cores (186,289 against 4,801,167).
- **CIFAR-10** (20 images): the NPU is 7.6× faster than two cores (4,080,849 against 31,046,419).
- **The FIR** (256 outputs): the NPU wins at every tap count, 16.6× over two cores at 4 taps and 41× at 256.

**The dot product shows where each engine starts to win:**
- **K ≤ 1:** one core's scalar loop wins (100 cycles at K = 0, against the NPU's 114).
- **From K = 7:** the NPU wins (132 cycles, against scalar's 217).
- **Two workers against one:** the split policy (step 3b) starts two workers from K = 33.

**Where the NPU and DOT8 do not apply, two cores win:**
- **The FFT** (Q15, not int8): 1.59× over one core.
- **The parallel reduction:** 1.96× (73,918 → 37,656).
- **v1's reduction:** 1.26×, because hart 0 refills inside the window, as in v1.

**The CPU kernels** (CoreMark, Dhrystone, sort/search, strided) run on one hart, since no engine applies
(matrix.md §7). With the FFT and Conv2D, they run 3.1× to 9.6× faster on one hart than v1's aster_minimal.

**The DMA beats the CPU's copy** from 113 bytes aligned (§4).

**The adopted tuning (step 3b) uses both harts around the engines:**
- **MNIST:** hart 1 takes half of the requantization in each interval.
- **CIFAR and Conv2D:** hart 1 takes half of the CPU work around the NPU's jobs. For CIFAR, each image is also
  staged by the copy helper.
- **ECG:** hart 1 stages and moves the next chunk.

So the v1 gate's best methods are the NPU with two harts: Conv2D 45,292 cycles, MNIST 5,389 an image, CIFAR
2,242,065 for 20 images, all cold.

## 2. How do working set, access pattern, bank conflicts and memory latency affect throughput?

`tables.md` §2. One hart, warm, unless stated.

- **Working set:**
  - the same work costs 344,276 to 346,796 cycles for every working set up to 8 KiB;
  - it costs 405,576 to 405,632 from 16 KiB (+18%), once the set no longer fits the 8 KiB data cache.
- **Access pattern:**
  - strided reads cost 135,240 cycles at stride 1 and 155,735 at stride 2;
  - from stride 4 (a line a read) they cost 196,898 to 200,258, 46% to 48% more than stride 1;
  - random walks cost the same as sequential reads up to 4 KiB, 15% more at 16 KiB, and 26% more at 32 KiB
    (424,890 against 335,907).
- **Bank conflicts are small:**
  - two harts streaming reads in the same bank take 22,446 cycles cold, against 21,931 in different banks (+2.3%);
  - the fabric counts 1,554 bank conflicts in the same bank, against 5 in different banks;
  - warm, the streams hit their caches: 11 conflicts, and under 0.1%.
- **Memory latency** (every warm R configuration with 1, 2 and 4 added waits on main memory; the geometric
  mean over each method's configurations, and the worst single configuration at +4):

  | Method | +1 wait | +2 waits | +4 waits | Worst at +4 |
  | --- | --- | --- | --- | --- |
  | DOT8 | +0.6% | +1.3% | +2.9% | 1.29× |
  | One core | +1.8% | +3.5% | +6.8% | 1.67× (strided reads, a line a read) |
  | ECG pipelines | +1.8% | +3.7% | +7.4% | 1.19× |
  | Two cores | +5.6% | +11.4% | +21.2% | 2.23× (the lr/sc counter) |
  | The NPU | +17.1% | +33.9% | +66.7% | 2.30× (the FIR at 256 taps; GEMM 64×128×64) |
  | The DMA | +20.6% | +33.7% | +61.8% | 2.94× (a 32 KiB copy) |

  Code and data that hit the caches barely notice: one core and DOT8 pay under 2% a wait. Two cores pay about 5%,
  their shared data missing by design. The engines that stream memory pay most, 15% to 21% a wait. The NPU and the DMA are as fast as they are because R's memory answers in
  two cycles. By family, the mean cost at +4 waits is 1.7% (the CPU kernels) to 31% (the DMA family), since each
  family mixes its methods (`tables.md` §2).

## 3. What are the real cache capacity, coherence and false-sharing trade-offs?

`tables.md` §3.

- **Cache capacity:**
  - Every R configuration was also run with 2 KiB and 4 KiB caches.
  - **4 KiB** costs 0.4% to 2.7% by family (geometric mean).
  - **2 KiB** costs 2.4% (DSP) to 16.5% (the DMA family, whose CPU copies miss).
  - The worst single configuration at either size is 1.95×: v1's unpacked DOT8 GEMM on two cores at 128×64×128.
    The packed kernel at the three gate sizes costs 1.17× to 1.23× at 2 KiB and 1.03× to 1.07× at 4 KiB.
  - The owner adopted 8 KiB in step 2. It closed timing at +0.306 ns and 72.75% of LUTs, against 4 KiB's
    63.9%.
- **Coherence:** one worker against two, at three sizes.

  | Program | 2 items | 129 items | 1,024 items |
  | --- | --- | --- | --- |
  | amoadd counter | 0.09× | 1.11× | 1.81× |
  | lr/sc counter | 0.14× | 1.51× | 1.91× |
  | CAS by lr/sc | 0.20× | 1.71× | 1.95× |
  | false sharing | 0.08× | 1.03× | 1.78× |
  | its padded control | 0.09× | 1.16× | 2.00× |
  | shared mix | 0.37× | 1.81× | 1.94× |
  | lock-protected sum | 0.18× | 0.75× | 0.78× |

  - Starting hart 1 and joining it costs about 450 to 500 cycles, so at 2 items two workers are 2.7× to 13×
    slower.
  - The split policy (step 3b) picks two workers only from each program's measured threshold. For the lock sum it
    never picks two: the lock serializes the harts, which then contend for it.
- **False sharing:**
  - Two workers on one line take 4,618 cycles at 1,024 items, against 4,631 padded.
  - With write-through data caches, a store goes to memory either way and a snoop invalidates the other hart's
    copy. So the penalty is the other hart's re-read, which this kernel hides.
  - Its speed-up is lower (1.78× against 2.00×) only because its one-worker run is 11% faster.
- **An L2:** none. The owner set it aside. v1.1's shared L2 study is [`l2.md`](l2.md).

## 4. At what size and alignment does the DMA beat the CPU's copy, and is CPU time freed?

`tables.md` §4. The thresholds are step 3b's, on the eight layouts, warm and cold (`crossover.txt`).

- **The crossover:** the copy helper hands a copy to the DMA from these sizes, and to the CPU below them:
  - 113 bytes aligned;
  - 89 bytes at the same offset within a word;
  - 61 bytes at different offsets.

  The CPU side is the shift copy from 15 bytes, and v1's copy below that. These are the worst case over the
  destination's cache state, by the owner's decision.
- **Throughput:**
  - The DMA's job costs 158 cycles end to end at the smallest sizes, then approaches 8 bytes a cycle:

    | Size | DMA bytes per busy cycle |
    | --- | --- |
    | 128 B | 5.8 |
    | 1 KiB | 7.6 |
    | 32 KiB | 7.99 |
  - The CPU copies 0.55 to 0.72 bytes a cycle aligned from 1 KiB up (32 KiB in 59,471 cycles, against the DMA's
    4,251: 14×).
  - The dip at 15 to 128 bytes (206 cycles end to end, against 184 at 256 bytes) is not the DMA's. That build's
    code placement has two instruction-cache misses, the layout effect that step 3 judges across eight layouts.
- **Freed CPU time: yes, the copy hides behind other work.**
  - A DMA copy beside an unrelated 256-word checksum takes 2,319 to 2,329 cycles at 1, 4 and 16 KiB alike, about
    the checksum's own time.
  - Done one after the other, the same work takes 2,406 to 4,342 cycles, so overlapping saves 3% to 47%.
  - The saving is the copy's own time: large when the copy is a large share of the work, and 0.4% to 11% beside a
    2,048-word checksum.
  - The work may run on either hart: beside the 256-word checksum, hart 0 saves 3.2% to 46.6% and hart 1 6.0% to
    42.0%; beside the 2,048-word one, 0.4% to 10.8% and 0.9% to 9.2%.

## 5. How do NPU geometry, tile shape, operand reuse and memory bandwidth affect utilization and performance per area?

`tables.md` §5. Sustained MACs a cycle in the kernel window, warm, with utilization of the array's peak:

| Shape | 4×4, 32-bit, 1 strip | 4×4, 64-bit, 2 strips | 8×8, 64-bit, 1 strip | 8×8, 64-bit, 2 strips (R) |
| --- | --- | --- | --- | --- |
| 128×64×128 | 14.4 (90%) | 15.7 (98%) | 50.6 (79%) | 59.3 (93%) |
| 96×96×96 | 14.6 (91%) | 15.6 (98%) | 51.8 (81%) | 58.2 (91%) |
| 64×64×64 | 13.9 (87%) | 15.4 (96%) | 44.8 (70%) | 49.9 (78%) |
| 32×32×32 | 11.6 (72%) | 14.4 (90%) | 21.8 (34%) | 24.3 (38%) |
| 64×8×64 | 8.9 (56%) | 14.6 (92%) | 18.8 (29%) | 27.7 (43%) |
| 32×1×784 | 3.1 (19%) | 7.1 (44%) | 6.7 (10%) | 7.3 (11%) |
| 784×1×25 | 2.0 (12%) | 3.9 (25%) | 3.6 (6%) | 4.4 (7%) |

- **Geometry:** the 8×8 array sustains 3.2× to 3.8× the 4×4's MACs a cycle on large GEMMs.
  - Its utilization falls faster as the tiles shrink: 38% at 32³, against the 4×4's 90%.
  - The fixed cost of a job and its tiles weighs more on a shorter run.
- **Operand reuse:**
  - Matrix-vector shapes (N = 1) reuse nothing across the array's columns, so every geometry is memory-bound
    there: 6% to 44% utilization.
  - The 8×8 sustains only 1.0× to 1.1× the 4×4's MACs a cycle there, with both options on both.
- **Memory bandwidth:**
  - The 64-bit port and the second A strip matter most where reuse is lowest.
  - On the 4×4 they raise 32×1×784 from 3.1 to 7.1 MACs a cycle (2.3×), and the large shapes by 7% to 11%.
  - On the 8×8 the second strip adds 6% to 47%.
- **Performance per area** (Phase 19's measurement, npu.md §4.6):
  - the 8×8 with both options uses 0.0124 of the device per sustained MAC a cycle, against the 4×4's 0.0223;
  - the area is the NPU's LUTs, flip-flops, block RAMs and DSPs, each as a fraction of the device's;
  - so the 8×8 is 1.8× better per area on the GEMM gate cases.

## 6. Does the heterogeneous ECG pipeline overlap work and meet a throughput or deadline target?

`tables.md` §6. v1's case: 16 chunks of 64 samples, a 16-tap FIR and the 4-feature classifier.

- **Overlap: yes.**
  - The stamped twins record each chunk's stages and engine jobs on the window's clock
    (`scripts/matrix_ecg_overlap.py`).
  - Every two-hart pipeline window overlaps: all 504, every later chunk's stages running beside another chunk's.
  - v1's ported pipeline does not overlap. All 378 of its windows are labelled sequential per chunk.
- **Throughput** (1,024 samples):

  | Pipeline | Cycles, warm | Samples a second |
  | --- | --- | --- |
  | One core, scalar | 177,291 | 0.58 M |
  | v1's pipeline, ported | 200,439 | 0.51 M |
  | The two-hart pipeline as first built | 175,512 | 0.58 M |
  | With the FIR on DOT8 directly (`__fir4`) | 35,603 | 2.88 M |
  | With hart 1 staging and moving the next chunk (`__fir4_split`, the adopted configuration) | 31,531 (33,023 cold) | 3.25 M (3.10 M cold) |

- **Deadline:**
  - A chunk's period at 360 Hz is 64 / 360 s, 17.8 million cycles.
  - The adopted pipeline's worst chunk latency is 5,298 cycles cold (53 µs), about 3,356 times inside it.
  - It runs 8,600 times faster than real time, so one ECG stream at 360 Hz uses about 0.01% of the SoC.

## The losses

Where a method or option loses, from the final capture and step 3b:

1. **The NPU loses at the smallest dot products.** One core's scalar loop beats it at K ≤ 1: 100 cycles against
   114 at K = 0 and 119 against 130 at K = 1, warm (cold, 125 against 143 at K = 0).
2. **Two workers lose at small sizes.**
   - At 2 items they are 2.7× to 13× slower than one, from hart 1's start and join.
   - The coherence programs lose below their thresholds, and the lock-protected sum at every size (0.18× at 2
     items, 0.75× to 0.78× from 129).
   - The dot product's two workers lose below K = 33, and still at K = 64, where the split policy runs them (the
     owner's decision 2 keeps the original's two workers in the bands): 686 cycles warm, against one core DOT8's
     666, and 1,075 cold, against scalar's 862. Step 3b's sweep marks K 40 to 96 as mixed.
   - The GEMM 3×1×7 cold: two cores take 1,003 cycles, against scalar's 738.
3. **DOT8 loses where packing outweighs it.**
   - The dot product at K ≤ 8: DOT8's 159 to 251 cycles, against scalar's 100 to 228; and at K = 64 cold (885
     against 862).
   - The FIR at 4 taps: 29,709 cycles warm, against scalar's 29,231 (cold 30,015 against 29,256).
   - Five small GEMMs, warm and cold: 1×1×1 (224 against 177 warm), 1×3×4 (447 against 420), 3×1×7 (600 against
     539), 5×7×3 (3,611 against 3,403) and 31×5×7 (23,179 against 21,643).
   - im2col Conv2D: 1,510,015 cycles, against scalar's 1,506,161.
   - v1's ECG DOT8 FIR: 200,001 cycles, against scalar's 177,291. The tuned FIR fixes this (54,223).
4. **The im2col lowering loses to the direct one on the NPU:** Conv2D at 573,184 cycles, against 84,251.
5. **Two harts lose in v1's ECG pipeline as ported:** 200,439 cycles warm, against one core scalar's 177,291 (cold
   201,798 against 177,975). Even with the tuned FIR, its two harts (54,855) are slower than one core with DOT8
   (54,223). Only the new two-hart pipeline, with hart 1 staging the next chunk, beats one core (31,531).
6. **The DMA loses below its crossover** (113 bytes aligned): its set-up costs 158 cycles.
7. **The 8×8 array's utilization drops** on small and matrix-vector shapes (7% to 45%). The 4×4 is better
   utilized there, but never faster in cycles on a GEMM: none of 80 shapes and windows.
8. **R's NPU is not always the fastest build.**
   - In ECG, the 4×4 or one-strip builds end earlier than R in 6 to 48 of 414 windows each, by up to 713 cycles
     (the original two-hart pipeline at 16-sample chunks, end to end).
   - The one-strip 8×8 ends batched MNIST (batch 4, two harts) 179 cycles earlier.
   - No GEMM, DSP or other ML window ends earlier on a smaller build.
9. **Smaller caches** cost up to 1.95× in a configuration.
10. **Memory latency costs the engines most:** at +4 waits the NPU's configurations are 1.67× slower (geometric
   mean, worst 2.30×) and the DMA's 1.62× (worst 2.94×).
11. **The FFT's two workers reach 1.59×**, below 2. Its Q15 butterflies leave no engine to use.
12. **Tuning losses** (step 3b, on the eight layouts):
   - 5 of the 456 variant configurations lose, and 178 are not distinguishable. A variant is adopted only where it
     wins, and the adopted set is checked: PASS.
   - Of the 447 code-placement twins, 6 win, 3 lose and 438 are not distinguishable.
   - The copy helper's shift copy is not distinguishable from v1's at 8 to 14 bytes in 49 of 84 configurations,
     so v1's copy is kept there.

## Out of scope

- **Energy and power:** nothing was measured.
- **An L2:** set aside by the owner (v1.1's study is [`l2.md`](l2.md)).
- **SKY130 and research question 7:** dropped on 1 October 2026.
- **Floating point:** CoreMark runs its integer path.
- **More than two harts, and external memory:** every workload fits the 96 KiB of block RAM.
- **An operating system.**

## The board run (step 5)

The final image's every R program, 5,254 of them, was run on the board and compared with its simulation. Each must
end as simulated:
- the same status;
- tohost written at the same cycle;
- the same instructions retired by hart 0 at tohost;
- the window's cycles and instructions;
- the console byte for byte, so every record and every counter in it;
- the console's lag at most 8 KiB.

Each load is a separate programming of the FPGA through the PYNQ's FPGA manager, with FCLK0 set to 100 MHz and
measured. The retired count is checked against a reference: each program simulated as the board runs it, on the
capture's own simulation.

| Load | R programs as simulated | Largest console lag | FCLK0 measured |
| --- | ---: | ---: | ---: |
| 1 | 5,254 of 5,254 | 4,890 bytes | 100.0001 MHz |
| 2 | 5,254 of 5,254 | 4,843 bytes | 99.9991 MHz |
| CoreMark's performance and validation runs, load 1 | 2 of 2 | 2,546 bytes | 99.9986 MHz |
| CoreMark's performance and validation runs, load 2 | 2 of 2 | 2,765 bytes | 99.9993 MHz |

So every figure in this report from the final capture at R is the board's as well as the simulation's, cycle for
cycle. Step 3b's tuning figures are simulation-only: the eight layouts' crossovers, thresholds and variants. The other
configurations are simulation-only builds, and are not on the board: the added memory waits, the 2 and 4 KiB
caches, the NPU geometries, the data cache off, and one hart. CoreMark prints the same validated reports on the
board as in simulation: the performance run's "CoreMark 1.0 : 218.737829", and the validation run's CRCs.

An earlier load ran 3,666 of the 5,254 programs, all as simulated, before its controlling session was cut off.
It is kept in the evidence but not counted.

## Reproducing it

- `scripts/matrix.py run --family F --caches 2,4 --repeat-every 30`, for each family.
- `scripts/matrix_bundle.py` packs the runs into the bundle.
- `scripts/phase20_report_data.py build/matrix` produces the tables.
- `scripts/matrix_board.py ... --all` runs the board, detached.
- `scripts/audit_phase20.py` runs the closeout audit; `--self-test` adds its planted faults.
