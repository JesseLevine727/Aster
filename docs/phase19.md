# Phase 19: high-utilization NPU and data movement

Status: **in progress — milestone 19.4 (the Phase 19 SoC and the gates)
complete, awaiting the owner's sign-off.** 19.0 (the NPU shell), 19.1 (the
tile mapping), 19.2 (the K-split mapping) and 19.3 (direct convolution, with
CIFAR's channels last) were signed off by the owner on 5 October 2026. The
NPU specification [`npu.md`](npu.md) was approved by the owner on 5 October
2026.
The owner's four decisions of 5 October 2026 (platform,
gate cases, baseline, output format) are recorded below and in npu.md §9.
The phase sits in the [v2 plan](phase17-plus.md#6-phase-17-sequence) after
[Phase 18](phase18.md) (complete, 5 October 2026). As in Phase 18, every
milestone passes its verification layer and records its timing before the
next starts, and the owner signs off each milestone.

## Goal

Make the array useful on real shapes rather than merely functional (the v2
plan). v1's 4×4 NPU does useful work in 0.33–0.80% of its PE-cycles (its
array steps in 0.9–1.5% of its job cycles) because nothing feeds it; Phase 19 replaces its data path — operand buffers filled by
whole-word reads, reuse of A and B, pipelined address generation, whole-word
result writes, a mapping for N = 1 — and proves, on the Aster core's SoC:

- at least **50% utilization** over the whole job on the declared dense GEMM
  cases (64×64×64, 96×96×96, 128×64×128), and the N = 1 mapping's
  utilization measured on MNIST's first layer and Conv2D;
- at least **5×** end to end over the best CPU code (DOT8 included) on those
  GEMM cases, and at least **2×** per image on the batch-one MNIST MLP;
- **10 ns** on the PYNQ-Z1, out of context per milestone and in context in
  the SoC, within 80% of the device's resources, and the gate workloads run
  on the board at 100 MHz as simulated.

The full contract — semantics, ABI 2, microarchitecture, interfaces,
verification and gates — is [`npu.md`](npu.md).

## Decided by the owner, 5 October 2026

The v2 plan left four things open that had to be fixed before any design
or measurement (the "predeclared" cases had never been declared, "optimized
scalar" never defined, and the end-to-end gates needed a system with the
Aster core, which the plan builds only in Phase 20):

1. **Platform:** the NPU in its own shell first, then a Phase 19 SoC — the
   Aster core, its caches, the NPU and on-chip memory, grown from the 18.7
   board design — for the speedup gates, in simulation and on the board.
   Phase 20 adds the second core, the shared fabric and the full workload
   matrix.
2. **Gate cases:** dense GEMM 64×64×64, 96×96×96 and 128×64×128 (each fits
   beside its program in the 96 KiB main memory); the batch-one MNIST MLP
   784 → 32 → 10; N = 1 measured on 32×1×784 and 784×1×25.
3. **Baseline:** the best CPU code on the Aster core, DOT8 included (the
   strictest option), for the GEMM's 5× and the MLP's 2× alike (the owner
   confirmed the MLP with the specification).
4. **Output:** raw int32, as v1; the CPU keeps scaling and activation.

## Verification architecture

The Phase 18 method (npu.md §6): a Verilator shell with the CPU shell's
memory model and protocol checks; an independent C++ oracle comparing the
whole memory after every job; an exact cycle model on the memories that
answer on time; seeded random descriptors with coverage bins; abort, reset
and back-pressure tests; harness self-tests; v1's engine as the shell's
first DUT; a mutation campaign; in the SoC, the CPU in lockstep where no
NPU result is read, self-checking programs where one is, and a snoop
checker for every NPU write.

## Milestone 19.0: the NPU shell (5 October 2026)

**The shell** (`verification/npu/`; `make npu-v1-tests`, in `make check`).
An NPU runs on the CPU shell's memory model (`verification/core/shell_ports.h`):
answers in order after one or two cycles, random back-pressure, extra and
long latencies, room for more requests in flight, garbage outside answers and
the error in the cycle after acceptance. The shell drives the NPU job by job
through its register port. Every cycle it checks the memory port: a waiting
request held unchanged; every access in the window; reads overlapping A or B;
writes only to C's bytes; no access in a job that must end in a descriptor
error; nothing presented or owed while the NPU shows DONE or is not busy.

After every job it checks:
- STATUS, exactly, and ERROR_CODE;
- the **whole** memory window, against the reference: every byte, not only
  C. After an abort, each C element must be unchanged or final; after a
  reset, each C byte;
- JOB_CYCLES against the cycles the shell saw the NPU busy;
- BYTES_READ and BYTES_WRITTEN against the reads and written bytes the shell
  accepted;
- for a whole job, v1's documented counters;
- ACK clearing STATUS.

**The reference** (`npu_model.h`) is written from the documents, not the RTL:
npu.md §2's arithmetic, and phase9.md's limits, regions and counters for v1.
v1's error-code numbers are documented only in its RTL, so the v1 profile
takes them from there.

**The generator** produces 1,000 random jobs per run:
- dimensions 0–40, with a longer K now and then;
- one dimension at the limit (1,024) in one job of 32;
- one region pinned to the window's base or top in one job in four;
- unaligned A, B and C, and minimal and padded strides;
- each descriptor error;
- ABORT during a job and after it ended;
- a reset during a job;
- descriptor writes and START while busy;
- a malformed CONTROL write.

A run fails unless it hits all 40 coverage bins.

**Exit gate met:**
- **v1 passes** (the shell's first DUT, through an adapter, `shell_npu_v1.sv`)
  in its 32 KiB window: 4 seeds × 1,000 jobs in each of six memory modes —
  two-cycle, one-cycle, stalls, one-cycle with stalls, long stalls, and room
  for three requests (only another stall pattern for v1, which takes one
  transaction at a time).
- **Every self-test is rejected, each with its own failure:**

  | Planted fault | Failure |
  | --- | --- |
  | a corrupted C byte | `MEMORY_MISMATCH` |
  | a stray write | `STRAY_WRITE` |
  | a misread counter | `COUNTER_MISMATCH` |
  | DONE before the last write's answer | `DONE_EARLY` |
  | a waiting request changed | `REQ_UNSTABLE` |

- **Planted bugs:** beyond the self-tests, 13 bugs were planted in scratch
  copies of v1's RTL (not retained), and every one is caught:
  - sign handling: a zero-extended product, an unsigned multiply;
  - addressing: B addressed by the row tile, C by A's stride;
  - computation and counting: the last K step skipped, BYTES_READ miscounted,
    no drain on abort;
  - bounds: no A-stride check, a region ending at the window's top or a
    dimension of 1,024 wrongly rejected;
  - status: an ABORTED the shell never asked for, STATUS bit 4 miswired.
- **v1's same-shell utilization** (useful MACs ÷ 16 × JOB_CYCLES), on the
  two-cycle memory:

  | Case (M×N×K) | JOB_CYCLES | Array steps | Utilization |
  | --- | ---: | ---: | ---: |
  | 64×64×64 | 492,546 | 16,384 | 3.33% |
  | 32×1×784 (MNIST's first layer) | 132,226 | 6,272 | 1.19% |
  | 784×1×25 (Conv2D) | 115,446 | 4,900 | 1.06% |

  This is higher than in v1's SoC (0.33–0.80%), because here no fabric
  serializes the NPU behind the CPU. It is the baseline the new data path is
  measured against in the same shell. The other two gate GEMMs (96³ and
  128×64×128) do not fit v1's window.

**Review.** The milestone's watchdog review found three holes, each proven
with a planted bug that passed, and all fixed:
- an ABORTED the shell had not asked for was accepted, with its partial
  result: such a bug printed a 64×64×64 "baseline" of 14%;
- the generator never placed a region at the window's top or a dimension at
  1,024, so bound checks off by one there passed;
- STATUS bit 4 was never compared.

It also led to:
- exact read and write counts;
- the no-access-while-idle check;
- coverage credited only when a reset or a write really landed while busy;
- 1,000 jobs per run (the rarest error was missed once at 300).

**Carried to 19.1** (the ABI 2 DUT): the register map and counter widths into
the profile; the register port's access size and error answer (npu.md §3's
sub-word rule); memory-error injection (error 6) and its partial-result
check; overlapping A and B rows and N = 1 in each MODE in the generator;
JOB_CYCLES measured from START's acceptance; each C word written exactly
once; `irq`, ABI and GEOMETRY read back. More than one request in flight is
first exercised by the new NPU: v1 takes one transaction at a time.

## Milestone 19.1: the tile mapping (5 October 2026)

**The NPU** (`rtl/accelerator/aster_npu2.sv`, `aster_npu2_engine.sv`,
`aster_npu2_ram.sv`; npu.md §3–§5) is the ABI 2 register page and an engine
that runs one job at a time:
- **CHECK** (16 cycles): the descriptor's errors in widened arithmetic, the
  regions' extents from registered products, and the B panel's width,
  floor(4096 / K) groups of four columns, from a 13-step divider.
- **For each panel of columns:** the panel of B is loaded once; then, for
  each strip of four rows of A, the strip is loaded and the array computes
  the strip's tiles one after another, K steps of one cycle each, through a
  pipelined 4×4 array (buffer read, operands, products, accumulate).
- **Output banks:** a tile's sums go to one of two output banks, which the
  writer empties as whole-word writes while the next tiles compute.

The loader is the NPU's own DMA. It reads the aligned words that cover each
operand row. It writes each word's bytes, rotated by the row's byte
alignment, into the two buffer words they belong to, in one cycle, through
the buffer RAM's two write ports. Any alignment and stride is handled with no
realignment pass. Every request address comes from a register advanced by
additions. The memory port follows the core's data-port rules, with two
requests in flight. npu.md §9 records what building it clarified: two output
banks; 19.1 runs every job as tiles; CHECK's length; JOB_MACS after an abort;
the 64-bit latch; one late request after a memory error; a job ends with its
pipeline empty.

**Verification** (`make npu-tests`, in `make check`). The 19.0 shell, now
profile-driven (ABI 1 for v1, ABI 2 here; `shell_npu_v2.sv`), adds the
following checks:

| Area | What it checks |
| --- | --- |
| Reference | ABI 2's error rules; the words a job must read, from npu.md §4.2's mapping |
| Memory port | whole-word writes; each C byte written once; no more requests in flight than OUTSTANDING (the CPU shell's rule) |
| Status and counters | `irq` never while busy; no counter counting while not busy; a job's counters unchanged after its end; ABI 2's counters against the reference; the cumulative counters against the sums of the jobs' counters |
| Register page | swept out of reset (every word offset of the page, read-only and unmapped writes, MODE's readback) |
| Job kinds | sub-word register accesses, answered with an error; a memory error injected on a random access of a job (error 6); CLEAR_TOTALS |

The NPU passes, in each of the six memory modes:
- 4 seeds × 1,000 random jobs, with every coverage bin: 50 on the two
  memories that answer on time, where the cycle model's bin is required too,
  49 in the stall modes. The bins are 19.0's, without v1's unaligned C and
  overlap error, plus:
  - a sub-word access, a memory error on a read and on a write, CLEAR_TOTALS;
  - overlapping A and B rows, each MODE, more than one panel;
  - an extent past 2^32, the register map, the cycle model;
- the 15 directed edge jobs (`+edges`):
  - K at its limit with panels of one group;
  - five panels;
  - both strides 0;
  - M and N at their limits;
  - MODE 2;
  - empty regions outside the window;
  - extents past 2^32.

**The cycle model** (`npu_model.h`'s `v2_job_cycles`, `+cycle_check`) predicts
every completed job's JOB_CYCLES from the schedule. On the two memories that
answer on time, every job matches it exactly. A model off by one cycle is
caught on the first job. It models the schedule as built, so it locks
performance against regression; it does not check the schedule
independently. The independent checks are the busy cycles the shell counts
and the words read.

**Planted bugs.** 14 bugs were planted in scratch copies of the RTL (not
retained), and all are caught:
- in the loader: the byte rotation, a group bound;
- in the buffers: the B entry step, the panel width;
- in the array: a step;
- in the bank handshake (by the RTL's assertion);
- in the writer: the row step (as a double write);
- in the checks: the overlap check, 32-bit extents (by the edge list and the
  random wrapping extents);
- in error and end handling: memory errors ignored, the job ending before
  its pipeline drains (by the counting-while-idle check);
- in the counters: a total's increment.

Two cases needed more than a run on the default memory:
- **Removing the hold on a presented request:** caught only under
  back-pressure. Without stalls a request is never left waiting, so the bug
  cannot show there.
- **Raising the in-flight limit:** went unnoticed until the shell checked
  the limit itself (INFLIGHT, added for it).

**Exit gate met:**
- **Random and edge descriptors pass the reference in every memory mode**
  (above).
- **The cycle model is exact** on the memories that answer on time (above).
- **Utilization at least 50% on the three GEMM cases in the shell**: useful
  MACs ÷ (16 × JOB_CYCLES), on the two-cycle memory (the gate) and under the
  shell's other memories:

  | Case | Two-cycle (gate) | One-cycle | Stalls (seed 5) | Long stalls (seed 3) |
  | --- | ---: | ---: | ---: | ---: |
  | 64×64×64 | **86.8%** | 86.9% | 78.2% | 55.0% |
  | 96×96×96 | **91.3%** | 91.4% | 85.2% | 70.2% |
  | 128×64×128 | **90.4%** | 90.4% | 83.7% | 70.3% |

  The stall columns are one stall pattern each: the pattern a seed gives
  depends on everything the shell runs before the job, so they move by a
  point or two as the shell grows. (Corrected in 19.2: the first figures,
  78.6/85.1/83.7% and 53.1/69.7/69.5%, came from a shell before the
  register-page sweep.)

  v1 reaches 3.33% on 64×64×64 in the same shell (19.0); its 32 KiB window
  cannot hold the other two. The N = 1 cases, still run as tiles, are
  measured beside them: 11.6% on 32×1×784 and 9.3% on 784×1×25 (v1: 1.19%
  and 1.06%). 19.2's K-split mapping is for these.
- **10 ns out of context** ([`results/phase19/npu-19.1`](results/phase19/npu-19.1/README.md)):

  | Top | Setup slack | LUTs | Block RAM tiles | DSPs |
  | --- | ---: | ---: | ---: | ---: |
  | The NPU alone | +0.793 ns (108.6 MHz) | 4,564 | 8 | 7 |
  | With a two-cycle 96 KiB block RAM (back-pressure, registered register port) | +0.622 ns (106.6 MHz) | — | — | — |

  The NPU uses 4,564 LUTs (8.6% of the device) and the 8 block-RAM tiles of
  its buffers. For comparison, v1's NPU added about 4,500 LUTs and 24 DSPs,
  measured differently (its marginal cost in v1's SoC after synthesis).

  The worst paths:
  - the NPU alone: the tile's MAC count at a tile's start;
  - with its memory: the descriptor check's registered product.

  The data path's paths are wider:
  - the array's multiply: +2.64 ns;
  - the memory's answer to the next request: +3.46 ns;
  - its readiness to the next request: +4.34 ns.

**Review.** The milestone's watchdog review found one RTL bug, and holes in
the evidence; all are fixed:
- **The RTL bug:** after an abort or a memory error, a job's counters could
  still count after its end showed (breaking §3: the job's counters hold
  until the next START). The job now ends with its pipeline empty, and the
  shell checks that no counter counts while not busy.
- Nothing tested extents past 2^32 (a 32-bit check passed). The edge list
  and the generator now make them.
- The edge cases ran on one memory only; they now run in every mode.
- The timing top claimed its register port was timed when it was not, and
  tied readiness high. Both are now registered. This assumes 19.4's arbiter
  registers its readiness, as the timing top does.
- Register-page rules were untested; a sweep now covers them.
- The 64-bit latch cannot be reached in simulation: recorded in npu.md §9.

Also from 19.1's own work:
- the first timing top had no output that depends on the data, so synthesis
  removed the whole data path: 0 block RAMs, an untrue +0.869 ns. Caught from
  the block-RAM count and fixed with an `observe` output;
- the shell's traffic model looped on an invalid K;
- the shell did not count an access answered with an error.

**Carried to 19.2:**
- the K-split mapping, with the loader packing B for it;
- MODE-aware traffic and cycle models;
- abort and reset tests by phase (load, tiles, drain);
- the counters audit;
- N = 1's utilization;
- OUTSTANDING above 4 would need a deeper tag ring; it is already checked at
  elaboration to be 1–4.

## Milestone 19.2: the K-split mapping, abort and reset, the counters (5 October 2026)

**The K-split mapping** (npu.md §4.3) runs every job with N = 1 under
MODE 0, and every MODE 2 job:
- **B, the vector,** is loaded once, packed four bytes a word.
- **Each strip of four rows of A** is loaded as for tiles, then computed in
  ceil(K/4) steps. At step t, PE (r, c) takes A(i_r, 4t+c) × B(4t+c), so all
  16 PEs work instead of one column of four.
- **The writer** adds each row's four partial sums as it writes C(i_r, 0).

To do it the RTL needed:
- an A operand per PE (each column takes its own lane of the row's word);
- the products past K zeroed on a strip's last step;
- the row sums in the writer.

The loader was generalized. Each byte now goes to an exact buffer position
with exact byte enables, so B can be gathered a byte per read when its rows
are not contiguous. The tile mapping's loads are unchanged in words and
cycles: the gate cases' cycle counts are 19.1's to the cycle. npu.md §9
records the 19.2 clarifications. The error codes and `irq` of 19.2's
content row were built and tested in 19.1.

**Verification** (`make npu-tests`):
- The reference, the traffic model and the cycle model now know the mapping
  each job runs. K-split jobs are checked for their own counters: strips as
  tiles, ceil(K/4) steps each, one word written a row.
- The generator makes N = 1 jobs often, with B packed or gathered and each
  MODE.
- The coverage bins add:
  - K-split under MODE 0 and MODE 2, with B packed and gathered, at each K
    mod 4;
  - N = 1 run as tiles (MODE 1);
  - ABORT landing in CHECK, in a load, in the tiles and between phases;
  - a reset landing in a load and in the tiles.

  The wrapper exposes the engine's state for these.
- Each abort or reset is aimed at a phase of the job, using the engine's
  state, and fires in it. Drawing a random cycle over an estimate of the
  job's length (19.1) missed the rarer phases in 11 of 240 runs. Over seeds
  1–40 in all six modes (240 runs), every run now hits every bin.
- 8 directed N = 1 edge jobs join the list (23 in all), 7 of them K-split:
  - K at its limit, with B gathered and with B packed under MODE 2;
  - K of 1, 2 and 3, one with B_STRIDE 0;
  - overlapping A rows;
  - K = 0 with M at its limit;
  - and one N = 1 job as tiles (MODE 1).

The NPU passes, in each of the six memory modes, 4 seeds × 1,000 random jobs
with every coverage bin (66 on the memories that answer on time, 65 in the
stall modes) and the 23 edge jobs, every completed job exactly as the cycle
model on the memories that answer on time.

**Planted bugs.** 10 bugs were planted in the new paths (not retained):
- in K-split: the last step's mask, the A lane per PE, the row sum, the
  strip's step count, MODE 0's choice of K-split;
- in the loader: the gathered B's byte step, the first and the last word's
  lane masks, B packed when its stride is not 1;
- the signed word index.

Nine are caught in every run. The tenth, an unsigned shift of the word
index, is equivalent: the index then differs only in bits 14–15, and the
buffers use at most the low 12 bits.

**Exit gate met:**
- **The 19.1 gates on the new paths:**
  - random and edge descriptors pass in every memory mode;
  - the cycle model is exact, K-split included;
  - the dense GEMM cases are unchanged: 86.8%, 91.3%, 90.4%;
  - 10 ns out of context (below).
- **Abort and reset tests,** required by coverage in every memory mode:
  ABORT landing in CHECK, in a load, in the tiles, and between phases
  (panel, strip, drain or finish); ABORT of a descriptor that is an error;
  a reset landing in a load and in the tiles. Each one is checked:
  - outside C unchanged; each C element old or final;
  - the counters holding after the end; STATUS, `irq`, ERROR_CODE;
  - after a reset, every register of the page as out of reset.
- **The counters audited against the per-job sums:** after every job,
  TOTAL_JOBS and each total equal the sums of the jobs' counters the shell
  read since the last reset or CLEAR_TOTALS. A job's counters must hold after
  its end, and no counter may count while the NPU is not busy. For a whole
  job each counter equals the reference: MACs, tiles or strips, array steps,
  words read (npu.md §4's mapping), words written. JOB_CYCLES must equal both
  the busy cycles the shell saw and the cycles from START to the end.
- **N = 1 utilization, published** (useful MACs ÷ (16 × JOB_CYCLES)):

  | Case | 19.1 (tiles) | K-split, two-cycle | One-cycle | Stalls (seed 5) | Long stalls (seed 3) | v1 |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: |
  | MNIST's first layer, 32×1×784 | 11.6% | **19.2%** (8,171 cycles) | 19.2% | 11.3% | 5.4% | 1.19% |
  | Conv2D, 784×1×25 | 9.3% | **12.7%** (9,634 cycles) | 13.0% | 8.1% | 4.3% | 1.06% |
  | MNIST's second layer, 10×1×32 | 7.4% | **11.4%** (175 cycles) | 11.8% | 7.6% | 4.9% | 0.97% |

  These are bound by memory, as npu.md §4.3 says. MNIST's first layer reads
  all 25,088 weights once at four bytes a cycle, so the array can be at most
  25% busy. The first layer reaches 19.2%, against the 19.5% estimated:
  loading each strip is not yet overlapped with computing it (npu.md §4.6's
  second A strip, evaluated in 19.5). Conv2D's K of 25 leaves each strip only
  7 steps against its load and drain. The memory stalls cost these mappings
  directly: they are reported, not gated.
- **10 ns out of context** ([`results/phase19/npu-19.2`](results/phase19/npu-19.2/README.md)):

  | Top | Setup slack | LUTs | Block RAM tiles | DSPs |
  | --- | ---: | ---: | ---: | ---: |
  | The NPU alone | +0.581 ns (106.2 MHz) | 4,879 | 8 | 7 |
  | With a two-cycle 96 KiB block RAM | +0.486 ns (105.1 MHz) | 4,780 (the top) | 40 (the top) | 7 |

  The K-split mapping and the generalized loader added about 300–400 LUTs
  (4,564 to 4,879 alone, 4,385 to 4,780 with the memory). Vivado's counts
  move by a few hundred between runs of nearly the same RTL, so the deltas
  are approximate. The worst paths:
  - the NPU alone: a panel's setup, its width and its first row's word count
    in one cycle, once per panel;
  - with the memory: the writer's K-split row sum, +1.24 ns as the
    named bank-to-write path.

  If the SoC's in-context build in 19.4 needs margin, the panel setup can
  take a second cycle and the row sum a register.

**Review.** The milestone's watchdog review found no RTL bug; its 240 extra
runs found no functional or cycle-model failure. It found:
- the fragile coverage gate (fixed above);
- overclaims about the abort and reset phases and the page after a reset
  (now precise, and the whole page is checked after every reset);
- a redundant multiplexer on the B buffer's read address (removed: with one
  group, the tile mapping's entry is K-split's step);
- the area with the memory attached, unstated (now given);
- 19.1's stall columns, which had moved (corrected above).

Aiming aborts at CHECK first exercised aborting an erroneous descriptor. The
shell's partial check then read C elements outside the window: a shell bug,
fixed. Such a job writes nothing, and the shell now requires exactly that. The
pre-push review added one more expectation, so an ABORT that the NPU
ignores only sometimes cannot pass. An ABORT the NPU took while busy must
end the job ABORTED, unless the job ended in that same cycle (npu.md §3).
ABORTED is allowed only after an ABORT that reached a busy NPU.

**Carried to 19.3:** direct convolution, with two-level A addressing.

**Carried to 19.5:** the second A strip, which K-split's loading needs
most.

## Milestone 19.3: direct convolution (5 October 2026)

**A in two levels** (npu.md §4.5):

    A(i,k) = A_BASE + (i div A_M0) × A_STRIDE_M1 + (i mod A_M0) × A_STRIDE
                    + (k div A_K0) × A_STRIDE_K1 + (k mod A_K0)

Each level is off when its count is 0. A convolution's row i is an output
pixel and its k a kernel position, so the NPU reads the input image itself,
with no im2col matrix. The RTL has:
- the four registers;
- in CHECK, A's extent from two 12-step dividers and three registered
  products, so CHECK keeps its 16 cycles;
- in the loader:
  - a row stepper, which adds A_STRIDE, or at the end of a block of A_M0
    rows jumps by A_STRIDE_M1;
  - a segment stepper, a kernel row of A_K0 bytes at a time, each placed at
    its byte of the row's bank by 19.2's exact-placement writes.

npu.md §9 records the 19.3 clarifications. The main one: a window that is
two runs deep is one channel or channels last, so CIFAR is measured channels
last, a choice for the owner.

**Verification** (`make npu-tests`, `make npu-im2col-cost`, both in
`make check`):
- The reference, the traffic model and the cycle model address A in two
  levels.
- The generator makes two-level jobs and convolution-shaped ones (one
  channel, or channels last, up to 4 channels, 3×3 kernels).
- New bins: each level, both levels at once, A_K0 not dividing K, and blocks
  of A_M0 rows crossing a strip.
- 14 edge jobs join the list (37 in all):
  - Conv2D and CIFAR's two convolutions, direct;
  - a convolution over two B panels (K = 1,024);
  - levels of 1, levels larger than M or K, and levels far beyond them
    (A_M0 = 0x8000_0003, A_K0 = 0x1_0005);
  - M at its limit with A_M0 = 1;
  - A_K0 not dividing K, including a last segment that starts a byte into a
    word;
  - a two-level extent past 2^32;
  - A ending exactly at the window's top, and one byte past it.
- `+conv` runs a convolution both ways: direct, and im2col, with the shell
  writing the matrix as the CPU would. It requires the two results equal.
- `scripts/npu_im2col_cost.py` times the workloads' own im2col loops on the
  Aster core with its caches.

The NPU passes, in each of the six memory modes:
- 4 seeds × 1,000 random jobs with every coverage bin: 71 on the memories
  that answer on time, 70 in the stall modes;
- the 37 edge jobs;
- every completed job exactly as the cycle model on the memories that
  answer on time.

Over seeds 1–40 in all six modes (240 runs) every run passes with every bin.

**Planted bugs.** 10 bugs were planted in the new paths (not retained):
- the row stepper's wrap and its jump;
- a segment's length and buffer byte;
- the extent's k bound and m quotient;
- the panel's first strip;
- the bytes left in a row;
- the strip's first row;
- the registered wrap test (after the timing work).

All 10 are caught. Three were at first caught only by some random runs, until
three edge jobs were added that catch them by construction: a last segment's
over-read crossing a word, and A ending at the window's top and one byte past
it.

**Exit gate met:**
- **The two-level addressing passes the reference** in every memory mode
  (above).
- **The im2col and direct lowerings, measured and published.** On the
  two-cycle memory, each result the same both ways. CIFAR is channels last
  for direct and channel planes for im2col, its workload's layout.

  | Convolution | Direct: NPU | im2col: CPU builds the matrix | im2col: NPU | im2col, in all | Direct is |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | Conv2D, 32×32, 5×5, 1 output | **11,986** (10.2%) | 121,649 (19,600 bytes) | 9,634 (12.7%) | 131,283 | **11.0× faster** |
  | CIFAR conv1, 16×16×3, 3×3, 16 outputs | **8,461** (62.5%) | 36,566 (5,292 bytes) | 8,167 (64.8%) | 44,733 | **5.3× faster** |
  | CIFAR conv2, 7×7×16, 3×3, 32 outputs | **10,309** (69.8%) | 24,037 (3,600 bytes) | 10,309 (69.8%) | 34,346 | **3.3× faster** |

  These are cycles, with the NPU's utilization in brackets. The CPU's im2col
  is the Aster core with its caches, running the workloads' own loops, at
  6.2–6.9 cycles a byte written.

  On the NPU alone, direct can cost more. Conv2D's kernel rows are 5-byte
  segments, 2 words each at any alignment, so a row takes 10 words against an
  im2col row's 7 (25 contiguous bytes): 24% more NPU cycles. CIFAR conv1's 9-byte segments cost
  3.6% more, and conv2's 48-byte segments nothing. Building the matrix costs
  the CPU 2.3 to 12.6 times the NPU job it feeds, so direct wins on every
  convolution measured. Phase 20 runs the workloads end to end.
- **10 ns out of context** ([`results/phase19/npu-19.3`](results/phase19/npu-19.3/README.md)):

  | Top | Setup slack | LUTs | Block RAM tiles | DSPs |
  | --- | ---: | ---: | ---: | ---: |
  | The NPU alone | +0.591 ns (106.3 MHz) | 5,438 | 8 | 11 |
  | With a two-cycle 96 KiB block RAM | +0.710 ns (107.6 MHz) | 5,639 (the top) | 40 (the top) | 11 |

  The first 19.3 run missed by 1.55 ns: CHECK formed min(M − 1, A_M0 − 1)
  in the cycle that multiplied it. Four further runs moved the work off the
  paths that set the slack, one at a time, until the final run. The steps:
  - register the bounds in CHECK's first cycle;
  - latch M − 1 and K − 1 at START, and use them in every extent product;
  - keep the row stepper's wrap test in a register beside the row index;
  - drop a chained adder from the loader's next address.

  The evidence README lists every run. The worst paths are now setup logic:
  - the NPU alone: a tile's MAC count at its start;
  - with its memory: a panel's setup.

  Against 19.2, two-level addressing added 559 LUTs to the NPU alone (4,879
  to 5,438) and 859 to the top with its memory (4,780 to 5,639). Vivado's
  counts move by a few hundred between runs. It added four DSPs: the
  extent's three products in place of 19.2's one.

**Review.** The milestone's watchdog review found no RTL bug; it re-derived
every measured number. It found test gaps, now closed:
- no test combined two levels with several panels, or used level counts
  beyond 4,096 (edge jobs added);
- the generator sized two-level regions the plain way, so it never pinned
  one to the window's top (fixed);
- a loose bin;
- writes while busy that never touched the new registers;
- `+conv` not refusing an erroneous descriptor.

It also found stale spec text (§2, §3's table, §3.2's region) and an area
claim that needed qualifying, both now fixed. It proposed the two timing
steps that made the final run.

**Carried to 19.4:** the Phase 19 SoC, the DOT8 baselines and the speedup
gates. CIFAR's and Conv2D's end-to-end runs are Phase 20's workload matrix.

## Milestone 19.4: the Phase 19 SoC and the gates (5 October 2026)

**The SoC** (`rtl/soc/aster_npu_soc.sv`, grown from 18.7's board design
`aster_core_pynq.sv`): the Aster core, its two 4 KiB caches and the v2 NPU on
96 KiB of block-RAM main memory, with the register page, driven from the ARM
side through the same AXI4-Lite port (magic "ASTN"). npu.md §5.2's contracts:
- main memory's port A answers the instruction cache; port B is shared by
  the data cache (first) and the NPU (any other cycle, not while an AMO holds
  the port), each in its acceptance order;
- each NPU write to main memory is snooped in the next cycle, its line
  invalidated in the data cache, and answered the cycle after that; it also
  ends the core's lr reservation on that word;
- the NPU's registers are the data cache's second I/O window
  (0x4000_0000–0x4000_0FFF), now word-only: the data cache faults any other
  access (sub-word, lr, sc, AMO) in its first stage, as for an unmapped
  address, so npu.md §3's access fault holds in the SoC (`IO_WORD_ONLY`, a
  new aster_l1d parameter, off by default);
- the NPU's interrupt drives the core's external interrupt input.

**Software:** `software/drivers/aster_npu2.h` (descriptor, START, wait,
ACK, 64-bit counters) and four programs in `software/npu2`:
- `npu2_gemm_gate.c`: each GEMM case by the best CPU code and by the NPU,
  then the N = 1 cases on the NPU;
- `npu2_mnist_gate.c`: the MNIST MLP on its 32 test images, both ways, each
  image's logits checked against the reference on both paths;
- `npu2_coherence.c`: the coherence tests (below);
- `npu2_faults.S`: sub-word and atomic accesses to the NPU's registers, each
  an access fault (a trap test, as Phase 18's).

**The DOT8 baselines** (the owner's "best CPU code, DOT8 included"):
- **GEMM:** B packed inside the timed window, four columns a block, a word
  holding four consecutive k (`dot8` needs them). A needs no packing: four
  of its rows are 4K contiguous bytes, read in place at immediate offsets
  (a kernel built for each K). Each 4×4 block of C sits in 16 registers;
  per 8 k the loop is 16 loads, 32 `dot8`s and 32 adds, scheduled by hand
  so it never stalls: **85 cycles per 8 k, measured** (the loop's ceiling,
  1.51 MACs a cycle). B's groups of columns (3.5 KiB each) sit 4 KiB apart,
  so a group stays in the direct-mapped data cache while every row block
  meets it, and the 512 bytes left keep the stack's top and the globals.
- **The MLP:** each layer a DOT8 matrix–vector product, **16 neurons a
  pass**, so the input vector is read twice in the first layer, not eight
  times. The weights are interleaved 16 neurons a word: the model's CPU
  layout, prepared before the windows, as the NPU's row-major weights are.
  It is hand-scheduled the same way: 52 instructions per 4 k, no stall. Its
  first layer is 34,210 cycles for 25,088 MACs, about 40% of them cache
  misses streaming the 25 KB of weights, which each image uses once.

  The GEMM baseline was tuned until the remaining cycles were packing, C's
  stores and misses (cycles for 64³ / 96³ / 128×64×128):

  | Step | 64³ | 96³ | 128×64×128 | MACs a cycle |
  | --- | ---: | ---: | ---: | ---: |
  | Compiler-scheduled 4×4 blocks, A and B packed | 300,955 | 1,033,371 | 1,224,347 | 0.86–0.87 |
  | The loop hand-scheduled, B's columns in cache-sized groups | 253,873 | 803,626 | 962,618 | 1.03–1.10 |
  | Groups 4 KiB apart, A's block copied beside them, the loop unrolled to 8 k, the first 4 k into the accumulators | 229,969 | 737,903 | 902,792 | 1.14–1.20 |
  | **A read in place (no copy)** | **219,684** | **714,614** | **853,188** | **1.19–1.24** |

  The final row is in the SoC; the third was measured in the CPU shell.
  That shell runs the core cycle for cycle as the SoC does. Ordering a
  group's column blocks to keep A's rows in the cache saved 11% of the
  misses but cost more loop overhead, so it was not kept. npu.md §7 estimated
  1.2–1.5 MACs a cycle for this code. The MLP's first baseline, four neurons
  a pass, took 43,218 cycles an image; this one takes 36,520.

**Verification** (`make npu-soc-tests`, in `make check`):
- The SoC testbench (`verification/npu/tb_npu_soc.cpp`) drives the SoC
  through AXI as the board script does. Every cycle, independently of the
  RTL, it follows a copy of main memory and checks:
  - every NPU job against npu_model.h's reference on the memory as it was at
    START: every C element of a completed job, and the error code of a job
    not aborted;
  - every NPU write: inside its C, each byte once, none in a job that must
    end with an error; and each end after a start;
  - every snoop: exactly the cycle after each NPU write, of its line, and no
    other;
  - **every load the data cache answers from main memory**, as Phase 18's
    L1 unit test checks (1.08 million in the coherence program). A load
    must return the word memory held at its lookup, or a newer one written
    before its answer. (A defensive rule never fired: no load was looked up
    while a store of the core's to its word was still on its way to memory,
    as the cache answers a posted store only once memory takes it);
  - each GEMM the CPU computed and asked it to check.
- **The gates' programs** pass with every check. The GEMM program's 5 jobs
  and 3 CPU results are checked; the MLP's 65 jobs; and the coherence
  program's 162 completed jobs and 4 ended ones.
- **Coherence** (`npu2_coherence.c`, checked by the program from what it
  stored, since the testbench's start snapshot would follow a late store):
  - 160 jobs of random shapes (M, N ≤ 20, K ≤ 40), strides and mappings
    (tiles and K-split), one after another;
  - before each, the CPU reads C and its padding (their lines cached); after
    each, it reads them back. Every C word must be the job's, and every
    padding word unchanged, so each line the NPU wrote must have been
    invalidated;
  - in 80 of them the CPU reads C over and over while the job runs,
    refilling lines the NPU is writing. Between passes it adds to a counter
    with `amoadd` and with `lr`/`sc` (282 adds, the total checked), so the
    port's AMO hold is exercised against the NPU;
  - A and B are stored just before START, with no fence;
  - mip.MEIP rises at the end and falls after ACK;
  - two error jobs (a C past the window; MODE 3) end with their codes and
    write nothing; two aborts (in CHECK and mid-job) end aborted; a
    completed job follows each;
  - an `lr` of a word the next job writes, then `sc`: it fails, and the word
    keeps the NPU's value; with the job writing another word, it succeeds.
- **The core is unchanged in the SoC:** 18.7's board programs (the 9 CPU
  kernels and 90 self-checking programs, by `scripts/aster_board.py
  --design npu`) end as in the CPU shell, **99/99, cycle for cycle, every
  RVFI record as the shell's**. `c/dot8_jobs` runs at 1,743,609 cycles, as
  on the board in 18.7. Against Phase 18's Spike-compared traces of the same
  programs (npu.md §9), 97 of the 99 are byte for byte the same. The other
  two:
  - `rv32ua/lrsc` differs from record 4,996 on, at an `sc` that Phase 18's
    run answered as Spike did (failed) and the SoC, like the shell on its
    own reservation, let succeed;
  - `selfcheck/dot8_arith` is self-checking, never compared with Spike.

  One program is not run: `traps/m1_traps`, whose "outside memory"
  (0x4000_0000) is the NPU's register page here.

**Exit gate met** (in the SoC, on its main memory):
- **Utilization** (JOB_MACS / (16 × JOB_CYCLES), gate 50%) and **speedup**
  end to end (gate 5×). The NPU's window runs from before the descriptor's
  first store to the CPU seeing DONE. The CPU's runs from A and B in memory
  to C in memory, packing included:

  | GEMM | Best CPU code (DOT8) | NPU, end to end | **Speedup** | NPU job cycles | **Utilization** |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | 64×64×64 | 219,684 | 18,990 | **11.57×** | 18,871 | **86.8%** |
  | 96×96×96 | 714,614 | 60,670 | **11.78×** | 60,551 | **91.3%** |
  | 128×64×128 | 853,188 | 72,654 | **11.74×** | 72,535 | **90.4%** |

  The NPU's job cycles equal the NPU shell's on its two-cycle memory (19.1).
  The CPU polls STATUS through its I/O window, so the job has port B to
  itself.
- **The MLP** (784 → 32 → 10, batch one, the workload's window: from the
  image in memory to its prediction, gate 2× per image): **3.53× on the worst
  image**, 3.54× over all 32. The best CPU code takes 36,520 cycles an image
  and the NPU path 10,309: two jobs, with the CPU's requantization, ReLU and
  argmax between and after, which both paths share. Logits are as the
  reference on both paths for all 32 images; 30 of 32 are labelled
  correctly, the model's own accuracy.
- **N = 1 utilization, measured** (K-split): MNIST's first layer 32×1×784
  **19.2%** (8,171 cycles), Conv2D 784×1×25 **12.7%** (9,634 cycles), both as
  in the shell.
- **10 ns in context** (`make fpga-aster-npu`: the whole device with the
  Zynq PS, 18.7's flow and sign-off; [`results/phase19/npu-19.4`](results/phase19/npu-19.4/README.md)):
  - worst setup slack **+0.267 ns**, hold slack +0.028 ns;
  - no failing endpoint among 30,601; all 21,420 nets routed; no DRC
    error;
  - 12,625 LUTs (23.7%), 8,982 flip-flops, 47 block-RAM tiles (33.6%),
    19 DSPs (8.6%), all within the 80% limit.

  The worst path (16 logic levels) runs from the data cache's stage-1
  address through its window decode into the error answer it must give in
  that cycle (cpu.md §4). From there it goes through the core's M1 trap
  logic and Execute's advance into the forwarding select. 18.7's worst path
  ended at the same select, from M1's result (+0.140 ns). The NPU's own paths
  are not the worst. The build before the review's fixes met +0.074 ns on
  the same path family; placement moves it.

**Planted bugs** (each in a copy of the RTL, the four programs run; not
retained). A dash means that program passed:

| Bug | Coherence program | MLP, GEMM | Fault program |
| --- | --- | --- | --- |
| No snoops | SNOOP | SNOOP | — |
| No snoops (snoop check off) | STALE_LOAD | STALE_LOAD (MLP) | — |
| Snoop a cycle late | SNOOP | SNOOP | — |
| Snoop a cycle late (snoop check off) | STALE_LOAD | — | — |
| Snoop of the next line | SNOOP | SNOOP | — |
| Snoop dropped when the cache takes the page | SNOOP | SNOOP | — |
| NPU's answer a cycle early | NPU_MISMATCH | NPU_MISMATCH | — |
| NPU before the data cache on port B | NPU_MISMATCH | — | — |
| No AMO hold for the NPU | NPU_MISMATCH | — | — |
| Interrupt not connected | MEIP check | — | — |
| NPU write keeps the reservation | sc check | — | — |
| NPU window not word-only | — | — | stopped by the SoC's assertion |
| Cache: lookup hits in its snoop's cycle (snoop check off) | STALE_LOAD | — | — |
| Cache: refill installs though snooped (snoop check off) | STALE_LOAD | — | — |
| Cache: refill never poisoned (snoop check off) | — | — | — |

All but the last are caught in the SoC. With the snoop check off
(`+no_snoop_check`), the load check alone catches a late snoop and the
cache's faults. The
last one is caught by Phase 18's L1 unit test (`make
core-aster-l1-unit`, 39 of 40 seeds). In the SoC it needs a refill to read
a word just before the NPU overwrites it, a timing the programs cannot aim
at.

**Review.** The watchdog review re-derived the gates and found them fair:
both sides timed end to end, the MLP's window the workload's. It found:
- **two RTL gaps, fixed:**
  - an NPU write did not end the core's `lr` reservation on that word;
  - a sub-word or atomic access to the NPU's registers could not fault as
    npu.md §3 requires. The cache took no late errors, so a sub-word load
    would have silently read 0 on the board;
- **test gaps, closed:**
  - no AMO or `lr`/`sc` raced the NPU, so a missing AMO hold went unseen;
  - an error job's "nothing written" was checked at the wrong address, and
    the testbench allowed writes in an error job;
  - the gate script required only the GEMM records;
  - the SoC tests did not check loads, so a late snoop was caught only by
    the snoop check;
- AXI reads past main memory returned the page RAM's output (now 0).

It noted that the MLP's baseline could gain about 10% from more neurons a
pass. The 16-neuron kernel, made alongside the review, gained 15%.

**Carried to 19.5:** npu.md §4.6's options, measured against these numbers,
and the board run: the gate programs on the PYNQ-Z1 at 100 MHz, cycle for
cycle as simulated, with `aster_board.py`'s board path taught the SoC.

## Milestones and gates

| Milestone | Content | Exit gate |
| --- | --- | --- |
| 19.0 | NPU shell, oracle, descriptor generator, harness self-tests; v1's engine through an adapter | v1 passes the oracle in its 32 KiB window in every memory mode; every self-test rejected; v1's same-shell utilization recorded on the cases that fit its window (64×64×64, 32×1×784, 784×1×25) |
| 19.1 | ABI 2; the tile mapping (checks, address generators, loader, buffers, pipelined array, C writer); the cycle model | random and edge descriptors pass the oracle in every memory mode; the cycle model exact; ≥50% utilization on the three GEMM cases in the shell; 10 ns out of context |
| 19.2 | K-split (N = 1); abort and reset; cumulative counters; error codes; interrupt | the 19.1 gates on the new paths; abort/reset tests; counters audited against per-job sums; N = 1 utilization published; 10 ns |
| 19.3 | Direct convolution (two-level A addressing) | the new addressing passes the oracle; im2col and direct lowerings measured on Conv2D and CIFAR's convolutions, both published; 10 ns |
| 19.4 | The Phase 19 SoC; the `aster_npu2` driver; the DOT8 baselines; the gate workloads | outputs verified independently; coherence tests; npu.md §7's utilization and speedup gates in the SoC; 10 ns in context |
| 19.5 | Evaluation of npu.md §4.6's options (a second A strip, a 64-bit port, 8×8); the board run | each option adopted or rejected on measurements; the gate workloads on the PYNQ-Z1 at 100 MHz, cycle for cycle as simulated; the Phase 19 report |

## Checklist

- [x] The owner's decisions of 5 October 2026 (above)
- [x] npu.md approved by the owner (5 October 2026)
- [x] 19.0 as in the table above — complete (owner, 5 October 2026)
- [x] 19.1 as in the table above — complete (owner, 5 October 2026), with
  npu.md §9's 19.1 clarifications
- [x] 19.2 as in the table above — complete (owner, 5 October 2026), with
  npu.md §9's 19.2 clarifications
- [x] 19.3 as in the table above — complete (owner, 5 October 2026), with
  npu.md §9's 19.3 clarifications (CIFAR channels last for direct convolution)
- [ ] 19.4 as in the table above
- [ ] 19.5 as in the table above

## Risks

- **The MLP's 2× over DOT8.** Its first layer is a matrix–vector product:
  every weight is used once, so the NPU is bound by its memory port (at most
  a quarter busy at four bytes per cycle) while DOT8 runs it at about one MAC
  per cycle; the estimate is about 2–3× per image once the CPU's work
  between layers, which both methods share, is counted. npu.md §4.6's
  second A strip and 64-bit port are its levers, measured in 19.2 and 19.5.
- **Timing.** The array's sixteen MACs and the loader's byte funnel are new
  10 ns paths; the core's margin in context is thin (+0.140 ns, 18.7), so
  the SoC's fabric meets the core and the NPU at registers.
- **Memory.** The gate cases and their programs must fit the 96 KiB main
  memory (the frozen memory point); the largest, 128×64×128, needs 56 KiB
  of operands and results.

## Non-goals

Floating point; requantization or activation in the NPU (owner decision);
sparsity; a job queue; a second core, the shared fabric and the full
workload matrix (Phase 20).
