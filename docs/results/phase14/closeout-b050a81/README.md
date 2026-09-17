# Phase 14 closeout: design-space exploration

Frozen implementation: `b050a81e6586f586a8a14e9bb642710cc2d19ebc` (tag `v1.0`)
Target: the frozen v1.0 SoC; parameters swept, no features added

## What was measured

| Study | Knob | Workloads | Answer |
| --- | --- | --- | --- |
| `memory-latency` | `MEMORY_WAIT_CYCLES` 0/1/4/16/64 | strided, sort_search, fft, conv2d | memory-bound crossover |
| `cache-geometry` | cache off, 4×8, 4×16, 4×32, 8×16 | strided, sort_search, fft, conv2d | L1 size effect |
| `core-scaling` | `HART_COUNT` 1/2, 1/2 workers | reduce | two-core scaling |
| `compute-placement` | scalar / Xasterdot8 / NPU | conv2d | specialization crossover |

## Headline results

- Memory latency dominates the least compute-dense workload: `strided` degrades
  **9.34×** from `wait0` to `wait64` while `conv2d` degrades only **1.62×**.
- The L1 is a **net slowdown** on every workload and geometry in this minimal
  SoC; the uncached build is fastest for all four.
- Two workers give **1.30×** on the memory-bound reduction; a second hart with
  one worker changes nothing.
- At this problem size the Xasterdot8 convolution is **0.59×** the scalar
  baseline (im2col overhead dominates) and the NPU is only **1.24×**.

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Contract | `spec/phase14.md` |
| 2–5 | Four validated studies | `studies/*/study.json` |
| 6 | Analysis | `analysis.md` |
| 7 | Frozen clean check | `verification/make-check.log` |
| 8 | Frozen source | `source/source-state.json` |

## Deferred within Phase 14

- **Accelerator dimensions (14.5).** The NPU engine hardcodes 4×4 in ~10
  places; exposing 2×2/8×8 is a bounded but non-trivial RTL change that needs
  its own re-verification. Not measured in this pass.
- **Per-configuration routed timing (14.6).** The frozen v1.0 overlay timing is
  retained in the Phase 13 bundle; per-swept-config overlays are deferred.

## Audit

```
python3 scripts/audit_phase14.py docs/results/phase14/closeout-b050a81 --current
```
