# Phase 10 FPGA resource and clock results

Target: Pynq-Z1 `xc7z020clg400-1`, Vivado 2025.1, 31.25 MHz FCLK0.
Image: two coherent harts, coherent L1, DMA, Xasterdot8 and NPU
(`linux-h2-coherent-c1-dma-dot8-npu`).

## Full image (routed)

| Resource | Used | Available | Percent |
| --- | ---: | ---: | ---: |
| Slice LUTs | 25,121 | 53,200 | 47.22% |
| Slice Registers | 19,198 | 106,400 | 18.04% |
| Block RAM Tiles | 32 | 140 | 22.86% |
| DSPs | 24 | 220 | 10.91% |

Timing (post-route, 32 ns / 31.25 MHz):

| Metric | Value |
| --- | ---: |
| WNS (setup) | 4.177 ns |
| TNS | 0.000 ns |
| WHS (hold) | 0.051 ns |
| THS | 0.000 ns |
| WPWS (pulse width) | 14.750 ns |
| Failing endpoints | 0 |

The setup slack implies a timing-implied maximum of about
`1 / (32 - 4.177) ns ≈ 35.9 MHz` for this placed-and-routed image. This is an
implied bound from one implementation, not a swept maximum-frequency result.

DRC `Checks found: 0`, methodology `Checks found: 0`, routing errors `0`.
The generated vendor reset netlist passes all five assert/release scenarios and
the HWH handoff passes with `dot8=true`, `npu=true`, `bridge_version=0x00090001`.

Bitstream/HWH hashes are in `bitstream.sha256`.

## Per-engine marginal cost (post-synthesis)

Each row adds one engine to the previous; values are post-synthesis logic cells
from `utilization_synth_*.rpt`.

| Configuration | LUT | FF | BRAM | DSP |
| --- | ---: | ---: | ---: | ---: |
| base: 2 harts + coherent L1 | 18,340 | 15,248 | 32 | 0 |
| + DMA | 19,828 | 16,562 | 32 | 0 |
| + DMA + DOT8 | 21,048 | 17,345 | 32 | 0 |
| + DMA + NPU | 24,371 | 18,498 | 32 | 24 |

Marginal engine cost:

| Engine | LUT | FF | BRAM | DSP |
| --- | ---: | ---: | ---: | ---: |
| DMA | +1,488 | +1,314 | +0 | +0 |
| Xasterdot8 | +1,220 | +783 | +0 | +0 |
| NPU (4×4 INT8) | +4,543 | +1,936 | +0 | +24 |

The NPU is the largest engine and the only one that uses DSPs (24 of 220 for the
sixteen PEs and the surrounding arithmetic); it uses no block RAM because the
array and accumulators are register-based. The base image's 32 BRAM tiles are
the 64 KiB ROM and 64 KiB RAM plus the coherent L1s.

## Reproduce

```sh
make fpga-linux-xe                 # full routed image and bitstream
# per-engine synthesis-only utilization
vivado -mode batch -source fpga/pynq_z1/build_linux.tcl \
  -tclargs "$PWD" build/fpga/pynq_z1/util-base 2 1 1 0 0 0 0
# ... repeat with 2 1 1 1 0 0 0, 2 1 1 1 1 0 0, 2 1 1 1 0 1 0
```
