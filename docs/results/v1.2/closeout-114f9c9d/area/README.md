# v1.2 area and timing by NPU geometry

Routed Vivado reports for the coherent all-engine SoC at each NPU geometry
(xc7z020). Reports are in `reports/{2x2,4x4,8x8}/`.

| Geometry | LUTs | LUT % | BRAM | DSP | WNS | implied Fmax | engine reads | engine cycles | conv cycles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2×2 | 23 815 | 44.8% | 32 | 24 | +5.204 ns | 37.3 MHz | 1 867 | 4 565 | 4 875 521 |
| 4×4 | 25 556 | 48.0% | 32 | 24 | +3.384 ns | 34.9 MHz | 1 123 | 2 884 | 4 636 733 |
| 8×8 | 32 475 | 61.0% | 32 | 24 | +3.012 ns | 34.5 MHz | 736 | 2 767 | 4 513 265 |

## Findings

- **Larger tiles trade area and frequency for memory traffic.** Going 2×2 → 4×4
  cuts engine read beats by 40% (1 867 → 1 123) and the convolution by 5%, but
  costs +1 741 LUTs and drops implied Fmax from 37.3 to 34.9 MHz. Going 4×4 →
  8×8 cuts reads another 34% and the convolution only 2.7%, for +6 919 LUTs and
  a further 0.4 MHz.
- **Diminishing returns after 4×4.** The 8×8 array buys 2.7% more throughput for
  +13 percentage points of LUTs; the 4×4 is the sweet spot on this device.
- **DSPs do not scale.** All three geometries use 24 DSP48E1 blocks, so the INT8
  processing-element multipliers are LUT-mapped rather than mapped to DSPs —
  the 8×8's 64 PEs add no DSP pressure. The 24 DSPs come from elsewhere in the
  design.
- **The array itself is small.** Even 64 PEs add only ~16 percentage points of
  LUTs over the 4×4 baseline, because the INT8 PE is a tiny multiply-accumulate.

## Performance per area

Cycles × LUTs (lower is better) for the convolution:

| Geometry | cycles × LUTs (×10¹¹) |
| --- | ---: |
| 2×2 | 1.161 |
| 4×4 | 1.185 |
| 8×8 | 1.466 |

The 2×2 and 4×4 are effectively tied; the 8×8 is 24% worse per unit area. The
measured area/performance data therefore argues for the **4×4 default**, which
is also the frozen v1.0/v1.1 geometry.
