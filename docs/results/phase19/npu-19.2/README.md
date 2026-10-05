# The v2 NPU, milestone 19.2 — timing at 10 ns

Vivado 2025.1 out of context on the PYNQ-Z1 part (xc7z020clg400-1), 10 ns
clock, by `make timing-fpga-npu2` (scripts/timing/vivado_ooc.tcl), on the
19.2 RTL (`rtl/accelerator/aster_npu2*.sv`: 19.1's tile mapping and the
K-split mapping for N = 1). The text of record is the "Milestone 19.2"
section of [`../../../phase19.md`](../../../phase19.md); `SHA256SUMS` covers
every file here. The tops are 19.1's ([`../npu-19.1`](../npu-19.1/README.md)).

| Top | Setup slack | Hold slack | Implied | LUTs | FFs | Block RAM tiles | DSPs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `npu2`: the NPU alone | +0.581 ns | +0.096 ns | 106.2 MHz | 4,879 | 4,213 | 8 | 7 |
| `npu2_bram`: with its memory | +0.486 ns | +0.110 ns | 105.1 MHz | 4,780 | 4,307 | 40 | 7 |

Against 19.1, for the K-split mapping and the generalized loader together:
the NPU alone gained 315 LUTs (4,564 to 4,879), and the top with its memory
395 (4,385 to 4,780). Vivado's counts move by a few hundred LUTs between runs
of nearly the same RTL (an earlier 19.2 run, before a redundant multiplexer
was removed, gave 4,691 and 4,961), so these deltas are approximate.

The worst paths:
- in `npu2`, a panel's setup: the panel's group count (the smaller of the
  groups left and the groups that fit) and its columns, into the loader's
  word count of the panel's first row of B, all in the PANEL cycle (once per
  panel);
- in `npu2_bram`, the writer: the row's four partial sums selected and added
  for K-split's result (`wr_r` into `wr_q_data`).

The named paths of `npu2_bram` (`fpga/npu2_bram/named_paths.rpt`):

| Path | Slack |
| --- | ---: |
| the memory's answer to the next request | +2.176 ns |
| the memory's readiness to the next request | +1.679 ns |
| the register port | +2.474 ns |
| a memory error to the stop | +8.622 ns |
| operands through the 8×8 products | +2.517 ns |
| the accumulate into an output bank | +5.294 ns |
| a buffer read to the operands (selected per PE) | +4.131 ns |
| a loaded word into a buffer | +4.132 ns |
| an output bank to a write (with K-split's row sum) | +1.243 ns |
