# Phase 14: Design-space exploration

Status: **in progress**.
Baseline: frozen Aster v1.0 at tag `v1.0` (`b050a81`).

Phase 14 varies the frozen v1.0 parameters rather than adding features. The
goal is to discover **crossovers and bottlenecks** and answer the README
research questions with comparable, provenance-checked measurements. It does
not change any frozen address, register, ABI or instruction encoding, and it
does not build shared L2 (that is v1.1).

## Scope decision

The frozen design already exposes most knobs. Two things are deliberately out
of scope:

- **Shared L2** — a subsystem, not a parameter. Deferred to a bounded v1.1
  phase; only then does L2 size become a sweep axis.
- **RTL frequency optimization** — changes the frozen RTL. Phase 14 *measures*
  routed Fmax per configuration; it does not pipeline or retime the design.
  Real frequency/energy work is Phase 16.

The one bounded RTL change in this phase is exposing the NPU array geometry
(2×2 / 4×4 / 8×8). The MAC tile is already parameterized (`ROWS`/`COLS`); the
engine and register boundary hardcode 4×4. The default stays 4×4, so the v1.0
interface freeze and `scripts/freeze_interfaces.py` are preserved.

## Knobs under test

| Knob | Values | Frozen default |
| --- | --- | --- |
| `MEMORY_WAIT_CYCLES` | 0, 1, 4, 16, 64 | 0 |
| `L1_LINE_WORDS` | 4, 8 | 4 |
| `L1_LINE_COUNT` | 8, 16, 32 | 16 |
| `ENABLE_L1` | 0, 1 | 1 |
| `HART_COUNT` | 1, 2 | 2 |
| engine | scalar, multicore, Xasterdot8, NPU | n/a |
| NPU geometry | 2×2, 4×4, 8×8 | 4×4 |
| problem size | per workload | n/a |

## Campaign

The campaign is staged so each study is independently reproducible and
auditable. Each stage retains every configuration's AsterBench v10 record with
source, toolchain and configuration provenance.

- **14.1 Memory latency.** `MEMORY_WAIT_CYCLES` sweep on `strided`,
  `sort_search`, `fft` and `conv2d`. Answers "when does memory bandwidth become
  the bottleneck".
- **14.2 Cache geometry.** `L1_LINE_WORDS`/`L1_LINE_COUNT` and cache on/off on
  the same workloads. Answers "how much do L1 cache sizes affect real
  workloads".
- **14.3 Core scaling.** `HART_COUNT` 1 vs 2 on `reduce` (1 and 2 workers) and
  `parallel`. Answers "how well does performance scale from one to two cores"
  and the coherence cost.
- **14.4 Compute placement.** scalar vs multicore vs Xasterdot8 vs NPU on
  convolution/GEMM across problem sizes. Answers "when is a custom instruction
  enough and when is a separate accelerator justified" and the NPU offload
  crossover.
- **14.5 Accelerator dimensions.** 2×2 / 4×4 / 8×8 NPU on GEMM/convolution.
  Answers "how do accelerator dimensions affect utilization, area and
  performance".
- **14.6 Routed timing.** Per-configuration Vivado routed timing and
  utilization for the configurations that fit the PYNQ-Z1.

## Deliverables

1. `docs/phase14.md` — this contract.
2. A design-space study driver that builds one configuration, runs the workload
   set, validates each record against its independent oracle, and emits a
   machine-readable comparison table with provenance.
3. The staged studies with fresh repeats.
4. A crossover/bottleneck analysis mapping results to the README research
   questions, including measured slowdowns.
5. Per-configuration routed timing and utilization.
6. A self-contained closeout bundle and `scripts/audit_phase14.py`.

## Verification and acceptance gates

- [x] Contract frozen.
- [x] `make check` still passes; the frozen v1.0 RTL is unchanged and the freeze
      guard is green (the NPU geometry parameterization is deferred to 14.5).
- [x] Each sweep is reproducible from a clean tree and validates every record
      against its independent oracle.
- [x] Every README research question that does not require shared L2 or ASIC
      PPA has a measured answer or an explicit "not measured" note.
- [x] Analysis reports crossovers and slowdowns, not only speedups.
- [ ] Routed timing/utilization retained for the swept configurations
      (deferred to 14.6; the frozen v1.0 overlay timing is retained in the
      Phase 13 bundle).
- [x] Self-contained closeout bundle and read-only audit.

## Stage status

- **14.1 memory latency** — complete (`studies/memory-latency`).
- **14.2 cache geometry** — complete (`studies/cache-geometry`).
- **14.3 core scaling** — complete (`studies/core-scaling`).
- **14.4 compute placement** — complete at one problem size
  (`studies/compute-placement`); a size sweep is a follow-up.
- **14.5 accelerator dimensions** — deferred: the NPU engine hardcodes 4×4 in
  ~10 places, so exposing 2×2/8×8 is a bounded but non-trivial RTL change that
  needs its own re-verification.
- **14.6 routed timing** — deferred.

## Explicit non-goals

This phase does not add shared L2, new instructions, new peripherals, interrupt
priority/nesting, or any change to the frozen v1.0 interfaces. It does not
optimize the RTL for frequency. It does not run the ASIC flow.
