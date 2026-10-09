# The Phase 20 workload matrix (milestone 20.4)

Status: **approved by the owner, 8 October 2026,** with the decisions in §9.
It fixes what 20.4 runs. The matrix's manifest is complete only when every
combination planned here is captured, marked unsupported with its reason, or
recorded as failed.

The matrix is soc.md §9's, made concrete. Its gates are soc.md §11's:
- correctness;
- records;
- overlap and totals;
- wins and losses;
- in 20.4, the scaling and v1 gates are measured, not yet required.

The records are AsterBench v12 ([`asterbench-v12.md`](asterbench-v12.md)).

## 1. The reference configuration

Every case is first run on **R**, the Phase 20 SoC as signed off:
- two harts;
- each with a 4 KiB direct-mapped instruction and data cache (16-byte
  lines), with warm caches;
- the physical two-cycle block-RAM memory (WAIT 0);
- the 8×8 NPU with a 64-bit port and two A strips;
- the new DMA;
- 100 MHz in the records' clock.

## 2. The axes

| Axis | Values (R's in bold) | How | Build variant |
| --- | --- | --- | --- |
| Harts and workers | 1 hart, 1 worker; **2 harts, 1 worker**; 2 harts, 2 workers | `HARTS` (RTL; a SoC build never made before); software | `soc_h1` for 1 hart |
| Data cache | **on**; off | `DCACHE` (new RTL in the data cache, owner's decision §9: off sends every main-memory access uncached, keeping its main-memory flag, which lr/sc and the AMOs need) | `soc_dc0` |
| Cache state | cold (the first pass after reset); **warm** (a pass after a warm-up pass) | software | — |
| Memory | **+0** (the physical two-cycle read); +1, +2, +4 wait cycles on every answer | `WAIT` (RTL, simulation only; the SoC has been built at +3 only, and the DMA shell has not run at +4) | `soc_w1`, `soc_w2`, `soc_w4` |
| NPU | **8×8, 64-bit port, 2 strips**; 8×8, 64-bit, 1 strip; 4×4, 64-bit, 2 and 1 strips; 4×4, 32-bit, 2 and 1 strips | `NPU_DIM`, `NPU_PORT_BYTES`, `NPU_A_STRIPS` (RTL; to be passed through the simulation's top). A 32-bit port needs a lane adapter on port N, which is 64 bits: new RTL, owner's decision §9 | `soc_n8s1`, `soc_n4p8s2`, … |
| Cache geometry | 2 KiB, **4 KiB**, 8 KiB | RTL (soc.md §9: **20.5**, with the L1 tests at each size) | 20.5 |
| Methods | scalar, multicore, DOT8, DMA, NPU | software | — |
| Placement and size | per family (§4): aligned and unaligned, small and large working sets, several seeds | software | — |

Crossed builds are built where a family crosses two RTL axes, for example
`soc_w2_dc0` for memory waits with the cache off.
- **New build variants are verified before they are measured.** Each
  variant runs `soc-tests`' two-hart programs, so the memory checker,
  litmus and the DMA all run on it.
- **Cache geometry (20.5)** also needs ABI 4's line-geometry metadata
  (`0x90`, `0x94`) to follow the parameter; today those words are fixed.

## 3. How the axes cross

The full cross-product is about 3 × 2 × 2 × 4 × 6 = 288 configurations per
case and method, before sizes and seeds: about 10^5 runs. Most of them
would answer no question in the catalog. The matrix crosses the axes
instead in two ways:
- **Every case and method:** R, plus each applicable axis varied alone
  (each other value of it, the rest at R's). An axis applies where it can
  change the result:
  - harts and workers for multicore methods;
  - the NPU's geometry for NPU methods;
  - memory, cache state and the data cache for every method.
- **Named crosses,** where a family's question is about an interaction:
  - **CPU baseline, memory hierarchy, DMA:** memory × data cache × cache
    state (4 × 2 × 2 = 16 points) for every case;
  - **multicore and coherence:** harts and workers × memory (3 × 4);
  - **NPU GEMM and quantized ML's NPU methods:** NPU geometry × every
    shape.

This is about 8,000 records.
- **Simulations:** a simulation runs one case and method in one
  configuration and sweeps its sizes and placements, two records each when
  warm. A cold point is its own simulation (the first pass after reset).
  That makes a few thousand simulations.
- **Cycles:** about 10^10 in total. The longest runs are scalar CIFAR and
  MNIST, ECG, and the CPU kernels with the cache off at +4 waits.
- **Time:** the SoC simulates at about 0.5 M cycles/s with every check on
  (measured: the DMA program's 32.3 M cycles in 63 s). That is about six
  CPU-hours, under an hour on parallel simulations.
- **Seeds:** the memory, DMA, DSP and NPU GEMM families run three seeds at
  R and one in their axis sweeps. ML and ECG use frozen inputs.
- **Firmware:** memory is tight for MNIST (84 KiB already) and the GEMM
  (94 KB), so each method of a case is its own firmware.

## 4. The families

Each case names its v1 source (ported, its computation and inputs kept) or
marks itself new.

**Measurement windows,** as phase17-plus.md §3 requires: each record names
its window.
- **Kernel:** the computation, once its data is in its documented place.
- **End to end:** dispatch, packing, DMA or NPU set-up, movement, compute,
  wait or join, and the result in place.
- Never compared across: one method's kernel window is never set against
  another's end-to-end window.
- **Warm runs:** both windows, in separate passes after a warm-up pass.
- **Cold runs:** the end-to-end window, as the first pass after reset.
- Input generation and checking stay outside both windows.

Each family's oracle is independent of the implementation (§3): a Python
model of the computation on the same seeded inputs.

### 4.1 CPU baseline

| Case | Size | Source |
| --- | --- | --- |
| CoreMark (its CRC test, 1 iteration: a correctness and cycle run, not an official score) | v1's | `coremark_port/` |
| Dhrystone (adapted, raw cycles) | 1,000 iterations | `dhrystone_port/` |
| sort/search | 256 items | `workload_sort_search.c` |
| strided | 1,024 words, stride 16, ×4 | `workload_strided.c` |
| FFT | 256 points, Q15 | `workload_fft.c` |
| direct Conv2D | 32×32, K = 5 | `workload_conv2d.c` |

- **Method:** scalar, on hart 0.
- **Window:** v1's, one interval around `run()`.
- **Outputs:** cycles, retired instructions, IPC, and the code and data
  footprint (the ELF's sections).
- **The port:** to ABI 4's counters and v12 records (soc.md §8); the code
  and inputs are unchanged.
- **Oracle:** `workload_reference.py`'s checksums, Dhrystone's canonical
  values, and CoreMark's own CRC (0xe714).
- **Crosses:** memory × data cache × cache state, plus 1 hart.
- **The official CoreMark score** needs a timed run of at least ten seconds
  (phase17-plus.md §3), and is 20.5's, on the board.

### 4.2 Memory hierarchy

| Case | Sizes and parameters | Source |
| --- | --- | --- |
| memcpy (the CPU's word copy, byte prefix and tail) | 64 B, 256 B, 1, 4, 16 and 32 KiB; aligned and unaligned | `memcpy_bench.c` |
| sequential read | 256 B to 32 KiB | `memory_walk.c` (sequential) |
| strided read | strides 1, 2, 4, 8, 16, 64 words over 16 KiB | new (v1's strided kernel is §4.1's) |
| random walk / pointer chase | 1, 4, 16 and 32 KiB rings (seeded Fisher–Yates; v1's code keeps a permutation beside the ring, so 32 KiB is its largest that fits) | `memory_walk.c` (random) |
| working-set sweep | 512 B to 48 KiB, repeated reads | new |
| two harts streaming | each hart its own half: the same bank, and different banks | new |

- **Outputs:** cycles per access, bytes a cycle, and the fabric's bank
  conflicts.
- **Crosses:** memory × data cache × cache state for every case.

### 4.3 DMA

- **Cases:**
  - the CPU copy (v1's fair kernel) against the DMA, through v1's driver;
  - sizes 0, 1, 3, 4, 7, 8, 15, 16, 63, 64, 255, 256 B, 1, 4, 16 and
    32 KiB;
  - alignments (source, destination) = (0, 0), (1, 0), (0, 3), (3, 6);
  - the destination's lines cached before the copy, or not.
- **The overlap cases:** the DMA copies while hart 0, then hart 1, does
  unrelated useful work (a checksum over another buffer). Each is compared
  with the same work and copy done one after the other. Polling alone does
  not count as freed time (phase17-plus.md §4).
- **Outputs:** the crossover, bytes a cycle, the CPU's busy and polling
  cycles, and the DMA's counters reconciled with the record.
- **Source:** v1's `dma.c` cases are a subset.
- **Crosses:** memory × data cache × cache state for each size and
  alignment.

### 4.4 Multicore and coherence

| Case | Methods | Source |
| --- | --- | --- |
| reduction, v1's (1,024 words ×4; hart 0 refills inside the window) | 1, 2 workers | `workload_reduce.c` |
| **reduction, parallel** (each worker fills and sums its half; the gate's, soc.md §9) | 1, 2 workers | new |
| **multicore GEMM** (npu.md §7's 64³, 96³, 128×64×128; DOT8; B's packing split, a barrier, half the rows of C each; the gate's) | 1, 2 workers | from `npu2_gemm_gate.c` |
| atomic counters: amoadd, lr/sc, a CAS by lr/sc, a lock-protected sum | 1, 2 workers | `coherent.c` |
| false sharing and its padded control | 1, 2 workers | `coherent.c` |
| ping-pong, SPSC queue | 2 harts | `coherent.c` |
| producer/consumer (a buffer handed over by a flag) | 2 harts | new |
| a shared mix | 1, 2 workers | `coherent.c` |

- **Sizes:** v1's (2, 129 and 1,024 items).
- **Outputs:** the per-hart counters, dispatch and join cycles, the fabric's
  snoops, invalidations, reservations ended and AMOs, and the overlap from
  each hart's busy interval.
- **Crosses:** harts and workers × memory; cache state, and the data cache
  off, one at a time.

### 4.5 DSP and custom ISA

| Case | Sizes | Methods | Source |
| --- | --- | --- | --- |
| dot product | K = 0, 1, 7, 8, 64, 256, 1,024, 4,096 | scalar, 2 workers, DOT8, NPU (a 1×1×K job, K split) | `cross_engine.c` |
| FIR | 256 samples; taps 4, 8, 16, 32, 64, 256 | scalar, 2 workers, DOT8, NPU (as a GEMM) | `cross_engine.c` |
| FFT | 256 points, Q15 | scalar, 2 workers | `workload_fft.c` |
| direct Conv2D | 32×32, K = 5 | scalar, 2 workers, DOT8, NPU (19.3's direct lowering) | `workload_conv2d_engine.c` |
| im2col Conv2D | the same | scalar, 2 workers, DOT8, NPU (M = 784, N = 1, K = 25) | `workload_conv2d_engine.c` |

- **Windows:** both are v12's common counter window.
  - The kernel window starts with the data in place and, for the NPU, the
    descriptor written, so it runs from START to the job's end.
  - The end-to-end window adds packing, im2col and the NPU's set-up.
- **Crosses:** each axis alone; the NPU's geometry for its methods.

### 4.6 NPU GEMM

- **Shapes:**
  - v1's twelve study shapes, (1,1,1) to (32,32,32);
  - npu.md §7's three cases;
  - an N sweep: M = K = 64 and N = 1, 2, 4, 8, 16, 32, 64, 128;
  - partial tiles: M and N of 9, 15, 17 and 33;
  - N = 1 at a large K: 32×1×784 and 784×1×25.
- **Methods:** the NPU; DOT8 on 1 and 2 workers for each shape; scalar for
  the shapes under 32³.
- **Windows:** both are v12's common window.
  - The kernel window runs from START to the job's end (the CPU's compute,
    for DOT8 and scalar).
  - The end-to-end window adds the descriptor, packing where needed, and
    the result in place.
- **Outputs:** PE utilization, tiles, operand reuse (bytes per MAC), bytes
  a cycle, set-up cost, and the NPU's totals reconciled.
- **Crosses:** NPU geometry × every shape; memory and cache state one at a
  time.

### 4.7 Quantized ML

| Case | Methods | Source |
| --- | --- | --- |
| MNIST MLP (784→32→10, 32 images, frozen) | scalar, 2 workers, DOT8, NPU; the NPU's fc1 batched by 1, 4, 8 and 32 images | `mnist_infer.c`, `npu2_mnist_gate.c` |
| CIFAR-10 CNN (16×16×3, two convolutions, fc, 20 images, frozen; channels last, 19.3's choice) | scalar, 2 workers, DOT8, NPU direct, NPU im2col | `workload_cifar.c` |

- **Windows:**
  - v1's: MNIST per image, from the image in RAM to the class; CIFAR over
    the 20 images with their staging;
  - kernel windows beside them.
- **Accuracy:** every logit equal to the frozen model's, by
  `phase11_reference.py` and `cifar_reference.py`; also the accuracy
  against the labels.
- **Outputs:** latency per image, images a second, the batch crossover, and
  the NPU's busy and idle time.
- **Crosses:** NPU geometry × each NPU method; harts and workers for the
  multicore ones; the other axes one at a time.

### 4.8 Streaming ECG

- **Cases:** the frozen MIT-BIH segment (record 100, 1,024 samples).
  - Chunks of 16, 32, 64 (v1's) and 128 samples.
  - FIRs of 8, 16 (v1's) and 32 taps.
  - Two classifier models: v1's, and one with twice its features.
- **Methods:**
  - v1's heterogeneous pipeline, ported: CPU stages, the DMA, a DOT8 FIR on
    hart 1, and the NPU's classifier;
  - scalar on one hart;
  - DOT8 on one hart;
  - a two-hart pipeline.
- **Outputs:**
  - sustained samples a second;
  - each chunk's latency, against a hard deadline: the chunk's period at
    the record's 360 Hz, reported apart;
  - each engine's and hart's busy intervals.

  **The overlap proof and its tuning are 20.5's;** 20.4 ports and measures.
- **Oracle:** `ecg_checksum`'s independent re-run of the pipeline.

## 5. The gate workloads

soc.md §11's gates, measured in 20.4 and required in 20.5:
- **Scaling:** 2 workers against 1 on the parallel reduction and on each of
  the three DOT8 GEMM cases (§4.4).
- **Against v1:** each v1-retained workload's best v2 method against v1's
  best, in cycles, with v1's inputs and window:

| Workload | v1's best (sync1) | v2's candidates |
| --- | ---: | --- |
| Conv2D 32×32 K=5, ×4 | NPU 4,837,408 | scalar, DOT8 (about 1.48 M on the Phase 19 SoC), NPU direct |
| reduction, 1,024 words ×4 | 2 workers 233,112 | 1 worker (about 75 K), 2 workers |
| MNIST MLP, per image | NPU 433,903 | NPU (5,460 in Phase 19's gate), DOT8 (36,520) |
| streaming ECG, 16 × 64 | 1,528,505 | the ported pipeline and its variants |
| CIFAR-10, 20 images | NPU 65,568,218 | NPU direct, NPU im2col |
| the CPU kernels | aster_minimal's: CoreMark 1,922,272, Dhrystone 3,128,553, sort/search 2,183,301, FFT 3,057,473, strided 9,087, Conv2D 5,820,652 | the Aster core (18.7: 466,606, 857,421, 696,877, 317,562, 2,722, 1,111,121) |

## 6. Records, oracles and the manifest

- **Records:** AsterBench v12 ([`asterbench-v12.md`](asterbench-v12.md)),
  one line on the console per window. Each is validated by the Python and
  C++ validators, which share one mutation corpus.
- **Oracles:** each record's checksum or output is checked by its family's
  Python model.
- **The manifest** (`docs/results/phase20/matrix-20.4/manifest.json`, schema
  `aster.phase20.matrix.v1`) lists every planned combination:
  - its family, case, method, axes and window;
  - its status: captured, unsupported (with the reason), failed (with the
    failure), or planned for 20.5;
  - its record, its oracle result, and the firmware's and the simulation
    build's hashes.

  The bundle holds the raw records and console logs, the oracles' output
  and the source tree's hash, as Phase 17's baseline did.
- **The runner** (`scripts/matrix.py`):
  1. builds each simulation variant, and each case's firmware;
  2. runs the simulations in parallel;
  3. validates and checks each record;
  4. writes the manifest.

  A subset is run twice and compared, for determinism.

## 7. Unsupported, with the reason

soc.md §9's list, and per family:
- **The instruction cache off:** the core fetches only through its cache.
- **A 2×2 NPU:** the NPU v2 is built 4×4 or 8×8.
- **8×8 on a 32-bit port:** the 8×8 array needs the 64-bit port.
- **An L2:** set aside by the owner.
- **Zero-wait memory:** the memory is block RAM with a two-cycle read.
- **v1's private-region permissions and warm stop:** v2 has neither.
- **The NPU for FFT, sort/search, CoreMark, Dhrystone, strided and the
  coherence cases:** no GEMM in them.
- **The DMA as a compute method:** it only moves bytes. It appears in the
  DMA family and in ECG's pipeline.
- **Multicore CoreMark, Dhrystone, sort/search and strided:**
  single-threaded v1 kernels. Scaling is the multicore family's question.
- **DOT8 for the FFT:** its Q15 butterflies are not int8 dot products.
- **Energy per inference:** not part of Phase 20's exit (phase20.md's
  non-goals; soc.md §11 has no energy gate).
- **Cache geometries other than 4 KiB:** planned for 20.5, not unsupported.

## 8. The steps of 20.4

1. **Infrastructure:**
   - a C header for every counter page;
   - AsterBench v12 with its validators and corpus;
   - the record emitter;
   - a 96 KiB workload layout;
   - the build variants, with the new `DCACHE` parameter verified in both
     modes, and the NPU's parameters passed through;
   - the runner and the manifest.

   Proven on §4.1.
2. **The v1-retained and gate workloads:**
   - the CPU kernels on ABI 4;
   - Conv2D in every method;
   - both reductions;
   - MNIST and CIFAR in every method;
   - ECG ported;
   - the multicore GEMM.

   The scaling and v1 gates are measured.
3. **The other families:** memory hierarchy, DMA, coherence, DSP and the
   NPU GEMM sweep.
4. **The full matrix:**
   - the manifest complete;
   - the overlap and totals reconciled;
   - the evidence;
   - timing in context for any RTL change, by the owner's rule of 8 October
     2026: several strategies, the best reproducible build signed off, the
     spread recorded.

Each step has its watchdog review before it is pushed.

## 9. Decided by the owner, 8 October 2026

1. **The crossing scheme in §3:** R and each axis alone, plus the named
   crosses (about 8,000 records). A full cross-product, and R alone, were
   set aside.
2. **The configuration in the records:** a hart reads what it can (the
   clock, the harts, the caches' geometry, the memory's waits, the NPU's
   size). The runner fills the rest from the build (the data cache, the
   NPU's port width and strips) and checks them against the testbench's
   readback of the ARM side's configuration words (`0x3F058`, `0x3F05C`,
   with `DCACHE` added). No new register is added for the harts.
3. **The data cache off is built:** a `DCACHE` parameter in the data cache.
   Off, every main-memory access goes uncached, keeping its main-memory flag.
   The default build is unchanged, and it is verified in both modes.
4. **The NPU's 32-bit port is built:** a lane adapter on port N, used only
   by the 4×4/32-bit simulation variants, so the full NPU axis is kept.
5. **AsterBench v12 as drafted** ([`asterbench-v12.md`](asterbench-v12.md)).
6. **CoreMark:** the CRC test's cycles in 20.4, and the official, ten-second
   score on the board in 20.5.

## 10. Clarifications found in 20.4, for the owner's review

Found while building step 2. Each is how the plan above was read where it
did not settle a point; none loosens a gate.

1. **A v1-retained case's end-to-end window is v1's own.** §4 keeps input
   generation outside both windows, but the v1 gate (§5) needs "v1's inputs
   and window", and v1's Conv2D window builds its inputs inside it. So the
   e2e record of a v1-retained case is v1's window, as §4.7 already says for
   MNIST. The kernel window beside it follows §4: the computation alone, its
   inputs in place.
2. **A two-worker kernel window** cannot leave out the hand-over, since the
   window is common to both harts. Hart 1 is dispatched before the window
   and waits at a release flag. Hart 0 opens the window, sets the flag, does
   its share, waits for hart 1's done flag, and closes it. The window holds
   the two shares and the two flags' hand-over, not the runtime's dispatch
   and join, which the dispatch-and-join case measures on its own (§4.4's
   output). The two-hart ECG pipeline is the exception: being a pipeline,
   it hands each chunk to hart 1 through the runtime's dispatch and join in
   both its windows.
3. **Cold and warm builds are one binary:** cold or warm is one data word
   (`software/matrix/matrix_cold.h`). A compile-time switch had moved code
   and data, and MNIST's cold windows came out up to 0.24% faster than its
   warm ones. The runner checks that each warm and cold pair differs only in
   that word.
4. **Between passes, each output is poisoned by the hart that writes it.**
   Without poisoning, a timed pass that wrote nothing could pass on the
   warm-up's results. `make matrix-poison-check` plants such a pass in
   Conv2D and shows it caught, and uncaught with the poisoning compiled out.
   The writer poisons rather than hart 0, because hart 0's stores would
   invalidate hart 1's warm lines and make its warm run colder. That cost
   the gate's reduction 1.7% until it was changed.

   Two exceptions are poisoned by hart 0, a handful of lines each: MNIST's
   two-worker rows of hart 1, and v1's reduction's partial sum. In both,
   hart 1 runs v1's own loop, which takes no other job.
5. **MNIST's batched NPU method batches fc2 as well as fc1** (§4.7 names
   fc1). Its weights are stored transposed at build time, as 19.3 did for
   CIFAR, in place of the original layout, which does not fit in 96 KiB
   beside them.
6. **Each warm window has its own warm-up pass,** the same code untimed just
   before it, where §4 has one warm-up before both windows. With one
   warm-up, a kernel window ran its own code for the first time. Its data
   was warm but its code was not: a GEMM's kernel window took 66 to 76
   instruction-cache misses against its e2e window's 1 or 2.
7. **The work intervals.** v12 asks for each hart's interval around its
   useful work. Hart 1 stamps its first stretch's start and its last
   stretch's end, once each. Hart 0 opens every window, so its interval
   starts at 0, and it ends as follows:
   - at the window's end when hart 0 works after its last wait, such as the
     checksum, requantization or class of a v1 window, a reduction's add of
     the two halves, or a one-worker engine;
   - at its last share's end, stamped, where it only waits after it;
   - 0 and 0 in an NPU kernel window, where it only starts the job and polls
     it.

   An interval holds no gaps. Where hart 0 polls the NPU between its stages
   in an NPU e2e window, its interval spans the polls, so overlap with an
   engine is not read from it. A stamp costs about 20 cycles (the matrix's
   `stamp_cost` case). Hart 1's two stamps, and hart 0's end stamp where it
   has one, are inside every two-worker window, v1-retained ones included;
   they count against v2.
8. **The computation's functions are compiled out of line** (`noinline`)
   in every one of the matrix's own programs, so that their code is the
   same in every caller and build. Inlined, a small change elsewhere in Conv2D's program
   changed the code GCC generated for its hot loop: 12 more instructions an
   output row, 7.3% more cycles on two workers. v1's kernels (`xe_kernels.c`)
   are out of line already.
9. **Two small additions inside v1's windows.**
   - ECG reads hart 0's cycle CSR at each end of a chunk, and stores the
     chunk's latency, for the deadline (§4.8: "reported apart").
   - The work stamps of item 7.

   Each is a few instructions a chunk or a window, and counts against v2.
   Otherwise each v1-retained window holds v1's code, its checksum
   included (CIFAR's is folded and its classes checked inside the window,
   as v1 did).
