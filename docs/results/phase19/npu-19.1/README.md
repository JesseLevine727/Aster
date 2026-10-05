# The v2 NPU, milestone 19.1 — timing at 10 ns

Vivado 2025.1 out of context on the PYNQ-Z1 part (xc7z020clg400-1), 10 ns
clock, by `make timing-fpga-npu2` (scripts/timing/vivado_ooc.tcl), on the
19.1 RTL (`rtl/accelerator/aster_npu2*.sv`). The text of record is the
"Milestone 19.1" section of [`../../../phase19.md`](../../../phase19.md);
`SHA256SUMS` covers every file here.

| Top | Setup slack | Hold slack | Implied | LUTs | FFs | Block RAM tiles | DSPs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `npu2`: the NPU alone | +0.793 ns | +0.093 ns | 108.6 MHz | 4,564 | 4,087 | 8 | 7 |
| `npu2_bram`: with its memory | +0.622 ns | +0.094 ns | 106.6 MHz | 4,385 | 4,188 | 40 | 7 |

- `npu2` times the NPU's register-to-register paths, its ports unconstrained;
  its memory port's write data keeps the data path (buffers, array, output
  banks) from being trimmed. Its 8 block-RAM tiles are the operand buffers:
  four 4 KiB A banks and the 16 KiB B panel.
- `npu2_bram` (`verification/npu/timing_npu2_bram.sv`) puts the NPU in front of
  a two-cycle 96 KiB block RAM, with readiness from a register (back-pressure)
  and the register port's inputs registered. So the paths from the memory's
  answer and readiness to the next request, and the register port, are timed
  register to register; its 40 tiles include the 96 KiB memory.
- The worst paths: in `npu2`, the tile's MAC count at a tile's start
  (columns left → this tile's columns → the product rows × columns × K into
  its LUT RAM); in `npu2_bram`, the descriptor check's registered product
  (M − 1) × A_STRIDE, from the latched M into the DSP cascade.

The named paths of `npu2_bram` (`fpga/npu2_bram/named_paths.rpt`):

| Path | Slack |
| --- | ---: |
| the memory's answer to the next request | +3.455 ns |
| the memory's readiness to the next request | +4.342 ns |
| the register port | +2.802 ns |
| a memory error to the stop | +8.237 ns |
| operands through the 8×8 products | +2.644 ns |
| the accumulate into an output bank | +5.189 ns |
| a buffer read to the operands | +5.873 ns |
| a loaded word into a buffer | +3.787 ns |
| an output bank to a write | +5.839 ns |
