# v1.2 analysis: parameterized NPU geometry

The accelerator is now parameterized `ROWS × COLS ∈ {2×2, 4×4, 8×8}` with the
default unchanged at 4×4. Every geometry produces the independent-oracle
checksum `0x07df8000` on the `conv2d_npu` workload, so the tiling is correct at
all three.

## Correctness

| Geometry | engine reads | engine cycles | conv cycles | checksum |
| --- | ---: | ---: | ---: | --- |
| 2×2 | 1 867 | 4 565 | 4 875 521 | `0x07df8000` |
| 4×4 | 1 123 | 2 884 | 4 636 733 | `0x07df8000` |
| 8×8 | 736 | 2 767 | 4 513 265 | `0x07df8000` |

The engine unit scoreboard passes at all three geometries across three seeds,
covering partial tiles, byte offsets, K=0, stalls, abort and the bounds/stride/
overlap error codes. The array is unchanged (already parameterized); the engine
tile loop, masks and index widths are now derived from `ROWS`/`COLS`.

## Performance

Larger tiles reduce the load traffic — the A/B reads fall 40% from 2×2 to 4×4
and another 34% from 4×4 to 8×8 — because each tile reuses its rows/columns over
a wider output block. The end-to-end convolution follows, but with sharply
diminishing returns: 5% faster from 2×2 to 4×4 and only 2.7% from 4×4 to 8×8.

## Area and frequency

See [`area/`](area/README.md). The 8×8 array costs +13 percentage points of LUTs
and a lower implied Fmax for its 2.7% throughput gain; the 4×4 is the sweet spot
and is the default. The INT8 PE multipliers are LUT-mapped (DSP count is
constant at 24), so the array scales in LUTs, not DSPs.

## Software

No software change was required. The driver and the im2col lowering pass a
descriptor (`m`, `n`, `k`, strides) and the engine tiles internally, so the
geometry is transparent to the CPU. The additive `GEOMETRY` register (`0x058`)
reports `{ROWS, COLS}` for software that wants to adapt.

## Compatibility

The default 4×4 configuration keeps the frozen v1.0/v1.1 NPU behavior; the
`FEATURES` register still reports `0x1f` at 4×4 and `0x1e` otherwise. The frozen
interface guard (`scripts/freeze_interfaces.py`) stays green.

## Physical acceptance

The 8×8 overlay runs `conv2d_npu` on the Pynq-Z1 at 31.25 MHz and reports
checksum `0x07df8000` on two warm boots (4 735 733 cycles each, the higher count
reflecting the overlay's synchronous memory), with a clean STOPPED snapshot. The
parameterized NPU is correct on hardware, not only in simulation.
