# Phase 19: high-utilization NPU and data movement

Status: **in progress — milestone 19.0 (the NPU shell) complete, awaiting the
owner's sign-off.** The NPU
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
- [ ] 19.0 as in the table above
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
