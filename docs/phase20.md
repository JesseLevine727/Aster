# Phase 20: whole-SoC workload placement and concurrency

Status: **started 7 October 2026: the owner's decisions recorded (below); the
SoC specification, [`soc.md`](soc.md), drafted for the owner's approval.** The phase sits in the [v2 plan](phase17-plus.md#6-phase-17-sequence)
after [Phase 19](phase19.md) (complete, 6 October 2026). As in Phases 18 and
19, every milestone passes its verification layer and records its timing
before the next starts, and the owner signs off each milestone.

## Goal

From the v2 plan ([`phase17-plus.md`](phase17-plus.md) §6, Phase 20): answer
the compute-placement and system-level research questions on one coherent,
timed design. Integrate two Aster cores with per-core caches that can hit in
parallel, coherence, memory banks, DMA, DOT8, the NPU, timer and interrupts,
and the software runtime. Run the complete workload matrix. Tune DMA
thresholds, cache geometry, multicore partitioning and task placement from
captured bottleneck data. Demonstrate actual stage overlap for ECG or label
the system as sequential per-chunk processing. Keep functional and
performance acceptance separate.

The plan's exit: all required workload outputs match independent oracles;
comparable records exist for every supported method; multicore overlap and
NPU/DMA byte and cycle totals are proven; all design-space results preserve
both wins and losses.

## Decided by the owner, 7 October 2026

Asked before any design or measurement:

- **Memory system: banks and private L1 caches.** The 96 KiB main memory is
  split into independent banks behind a crossbar (the Phase 17 memory point
  already calls for independent banks). Each Aster core keeps its own
  instruction and data caches, so hits run in parallel. Each data cache
  snoops every other master's writes, the snoop contract of cpu.md §9. The
  options set aside: one shared main-memory port (Phase 19's, with a second
  core), and a shared L2 cache (it adds latency and area to on-chip block
  RAM without adding capacity).
- **Workload matrix: the full catalog.** Every workload family of
  phase17-plus.md §4, with every method the v2 SoC supports — scalar, one
  and two cores, DOT8, DMA, the NPU — and the survey's axes the design can
  be configured for: the NPU's geometry and options by parameter, caches on
  and off, added memory waits in simulation. A combination the design cannot
  run is listed with its reason. The option set aside: the v1 catalog on
  the final SoC only.
- **Exit gate: the plan's exit, with stricter scaling.** Besides the plan's
  exit (above):
  - two workers reach at least **1.8×** over one on the parallel reduction and
    on the multicore GEMM;
  - every workload v1 retained has a v2 method faster than v1's best method
    for it (the same window and inputs).

  The options set aside: 1.6× scaling, and the plan's exit alone.

Asked the same day, before measurement, once the v1 workloads were read:

- **The reduction the scaling gate measures: fill and sum in parallel.** v1's
  reduction (`workload_reduce.c`) refills its 1,024-word array on hart 0
  alone inside the timed window, and only the summing is split, so two
  workers reach about 1.3× on it whatever the hardware (v1 measured 1.29×).
  The gate is measured on a version in which each worker fills and sums its
  own half: the same sizes, seed, iterations and checksum. v1's version is
  still run and reported, so the comparison with v1 stays like for like. The
  options set aside: timing the summing alone, and leaving the reduction
  out of the gate.
- **The multicore GEMM the scaling gate measures: npu.md §7's cases with
  DOT8.** 64×64×64, 96×96×96 and 128×64×128, each hart running the best CPU
  code (DOT8) on half of the rows, against one hart running all of them —
  the cases and code of the NPU's gates. The options set aside: v1's
  cross-engine multicore GEMM (the scalar kernel at v1's sizes), and both.
- **DMA: a new 64-bit engine with v1's register interface.** v1's engine
  copies a 32-bit word about every six cycles (about 0.7 bytes a cycle),
  which the Aster core's own copy loop beats at every size. The new engine
  keeps v1's registers (its driver and workloads unchanged), moves 8-byte
  units with two requests in flight (about 4–8 bytes a cycle), and is
  verified in its own shell against a reference, as the NPU was. The option
  set aside: v1's engine on the new fabric unchanged.

## Verification architecture

soc.md §10:
- **20.0–20.1:** a fabric shell with seven random requesters, a reference
  memory, and order, atomicity and snoop checks. Its first DUT is a serial
  reference fabric.
- **From 20.2:** in the SoC's simulation, a memory checker tests every load
  each hart retires against the global order of writes. The SoC also runs:
  - litmus tests on two harts;
  - hart-1 reset tests;
  - self-checking workload programs against independent oracles;
  - 18.7's 99 programs in lockstep with Spike, cycle for cycle as in the
    CPU shell.
- **20.3:** a DMA shell with an oracle and a cycle model.
- **Each milestone:** timing at 10 ns.

## Milestones and gates

| Milestone | Scope | Exit |
| --- | --- | --- |
| 20.0 | The fabric shell, the memory checker, the litmus harness; a serial one-bank reference fabric as the shell's first DUT | Every self-test rejected; the reference fabric passes in every mode |
| 20.1 | The banked fabric (soc.md §4); the data cache's snoop ports | Random and edge tests pass in every mode with every bin; planted bugs caught; the L1 tests pass; 10 ns out of context |
| 20.2 | The two-hart SoC: the fabric, both cores, the NPU, the devices, the runtime's dispatch and join | soc.md §10.4's regression; litmus, the memory checker and reset tests on two harts; 10 ns in context |
| 20.3 | The DMA engine and its shell; v1's driver, unchanged | soc.md §10.5; the DMA against CPU copies, at every size and alignment, in the SoC; 10 ns in context |
| 20.4 | The workload matrix: every family ported, AsterBench v12, oracles, the runner and manifest | The correctness, records and overlap gates; the scaling and v1 gates measured |
| 20.5 | Tuning from the captured data (DMA thresholds, cache geometry, partitioning, placement); ECG; the board run | Every soc.md §11 gate; the final image on the PYNQ-Z1 at 100 MHz, on two boots; the Phase 20 report and its closeout audit |

## Checklist

- [x] The owner's decisions (7 October 2026)
- [ ] soc.md approved by the owner
- [ ] 20.0 as in the table above
- [ ] 20.1 as in the table above
- [ ] 20.2 as in the table above
- [ ] 20.3 as in the table above
- [ ] 20.4 as in the table above
- [ ] 20.5 as in the table above

## Risks

- **Timing and area.** The image adds a second core, the fabric's
  arbitration, two more snoop ports per data cache, the DMA and about 66
  counters. The estimate is about 74% of the LUTs (soc.md §2): under 80%, but
  with less room for placement than the 19.5 SoC's 40%. The core's margin in
  context is thin (+0.140 ns in 18.7, +0.221 ns in 19.5's SoC). A registered
  stage in the fabric would cost a cycle on every access, and it would move
  the point where an access takes effect away from its acceptance, which
  cpu.md's ordering rests on. It is held in reserve, as an owner decision.
- **The 1.8× gates.** Everything that does not split eats into the margin:
  - the dispatch and join of each of the reduction's four iterations. One
    hart takes about 18,000 cycles an iteration (18.7's scalar reduction:
    73,811 cycles for the four), so each fixed cost of a few hundred cycles
    is a few percent of two workers' half;
  - the GEMM's barrier after the shared packing of B;
  - imbalance between the harts, and bank conflicts.

  Hart 1's start-up is kept out of the window (soc.md §9), and B's packing is
  split between the harts. If one hart packed all of B, two workers would
  reach only about 1.88–1.94×. The estimate is about 1.85–1.95× on both
  workloads, so the margin is small.
- **Memory.** Code, data and two stacks share 96 KiB, and the largest matrix
  points are tight.
- **Verification without a two-hart reference.** Spike cannot follow the
  RTL's interleaving. The cores keep Phase 18's lockstep evidence, and the
  memory checker, the litmus tests and the oracles cover what two harts add.
- **Scope.** The full matrix is hundreds of runs. The manifest and parallel
  simulation keep it tractable.

## Non-goals

More than two harts; an L2 (owner decision); write-back caches or directory
coherence; privilege modes other than machine mode; a CLINT or PLIC; changes
to the NPU, whose options are only matrix axes; floating point. Energy per
workload is not part of Phase 20's exit; the frozen target stands.

