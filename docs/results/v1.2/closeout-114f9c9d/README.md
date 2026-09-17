# v1.2 closeout: parameterized NPU geometry

Frozen implementation: `114f9c9d2456cc5559dc7b3c3da5b19471adc76f`
Baseline: Aster v1.1 at `4731be2`

## What was added

The INT8 NPU tile engine is parameterized `ROWS × COLS ∈ {2×2, 4×4, 8×8}`. The
default stays 4×4, so the frozen v1.0/v1.1 NPU behavior and the interface guard
are unchanged. A read-only `GEOMETRY` register (`0x058`) reports `{ROWS, COLS}`.
No software change was needed: the driver and im2col pass a descriptor and the
engine tiles internally.

## Headline results

| Geometry | engine reads | conv cycles | checksum | LUTs | implied Fmax |
| --- | ---: | ---: | --- | ---: | ---: |
| 2×2 | 1 867 | 4 875 521 | `0x07df8000` | 44.8% | 37.3 MHz |
| 4×4 | 1 123 | 4 636 733 | `0x07df8000` | 48.0% | 34.9 MHz |
| 8×8 | 736 | 4 513 265 | `0x07df8000` | 61.0% | 34.5 MHz |

- **Correct at every geometry** — the same independent-oracle checksum.
- **Diminishing returns after 4×4**: the 8×8 buys 2.7% throughput for +13
  percentage points of LUTs and a lower Fmax.
- **The INT8 PE multipliers are LUT-mapped** (DSP count is constant at 24), so
  the array scales in LUTs, not DSPs.
- Performance per area (cycles × LUTs) is effectively tied between 2×2 and 4×4;
  the 8×8 is 24% worse. The **4×4 default is the measured sweet spot**.

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Contract | `spec/npu-geometry.md` |
| 2 | Geometry study | `studies/geometry/study.json` |
| 3 | Routed area/timing | `area/area.json`, `area/README.md`, `area/reports/` |
| 4 | Analysis | `analysis.md` |
| 5 | Clean check | `verification/make-check.log` |
| 6 | Frozen source | `source/source-state.json` |
| 7 | Physical acceptance | `physical/` (8×8 overlay, oracle checksum on two boots) |

## Audit

```
python3 scripts/audit_v12.py docs/results/v1.2/closeout-114f9c9d --current
```
