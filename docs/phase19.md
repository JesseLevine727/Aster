# Phase 19: high-utilization NPU and data movement

Status: **in progress — milestone 19.1 (the tile mapping) complete, awaiting
the owner's sign-off.** 19.0 (the NPU shell) signed off by the owner on
5 October 2026. The NPU
specification [`npu.md`](npu.md) was approved by the owner on 5 October 2026.
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
  | 64×64×64 | **86.8%** | 86.9% | 78.6% | 53.1% |
  | 96×96×96 | **91.3%** | 91.4% | 85.1% | 69.7% |
  | 128×64×128 | **90.4%** | 90.4% | 83.7% | 69.5% |

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
- [ ] 19.1 as in the table above
- [ ] 19.2 as in the table above
- [ ] 19.3 as in the table above
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
