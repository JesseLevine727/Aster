# Phase 19: high-utilization NPU and data movement

Status: **not started — the NPU specification [`npu.md`](npu.md) awaits the
owner's approval.** The owner's four decisions of 5 October 2026 (platform,
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
   strictest option). npu.md applies it to the MLP as well; the owner
   confirms that with the specification.
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
- [ ] npu.md approved by the owner
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
