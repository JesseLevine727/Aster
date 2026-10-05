# The v2 NPU, milestone 19.3 — timing at 10 ns

Vivado 2025.1 out of context on the PYNQ-Z1 part (xc7z020clg400-1), 10 ns
clock, by `make timing-fpga-npu2` (scripts/timing/vivado_ooc.tcl), on the
19.3 RTL (`rtl/accelerator/aster_npu2*.sv`: the tile and K-split mappings and
A in two levels). The text of record is the "Milestone 19.3" section of
[`../../../phase19.md`](../../../phase19.md); `SHA256SUMS` covers every file
here. The tops are 19.1's ([`../npu-19.1`](../npu-19.1/README.md)).

| Top | Setup slack | Hold slack | Implied | LUTs | FFs | Block RAM tiles | DSPs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `npu2`: the NPU alone | +0.591 ns | +0.110 ns | 106.3 MHz | 5,438 | 4,712 | 8 | 11 |
| `npu2_bram`: with its memory | +0.710 ns | +0.082 ns | 107.6 MHz | 5,639 | 4,826 | 40 | 11 |

The four more DSPs are A's two-level extent: three registered 12 × 32-bit
products in CHECK, in place of 19.2's one. The runs that led here (not
retained):
- the first 19.3 run missed 10 ns: −1.552 ns alone, −0.793 ns with the
  memory. CHECK formed min(M − 1, A_M0 − 1), a 32-bit compare and select, in
  the cycle that multiplied it;
- with that bound registered: +0.185 ns with the memory, the K divider's
  input subtracting K − 1 in front of its 33-bit compare-and-subtract;
- with M − 1 and K − 1 latched at START: +0.417 / +0.601 ns, A's row stepper
  (its wrap compare into the next segment) the worst with the memory;
- with the wrap test registered beside the row index and a chained adder
  dropped from the loader's next address: +0.378 / +0.364 ns, B's extent
  product still subtracting K − 1 inline;
- with B's and C's extent products taking the latched M − 1 and K − 1: this
  run.

CHECK keeps its 16 cycles throughout.

The worst paths:
- in `npu2`, a tile's MAC count at its start (the columns left into rows ×
  columns × K, into its LUT RAM);
- in `npu2_bram`, a panel's setup (its group count and columns into the
  loader's word count of B's first row, once per panel).

The named paths of `npu2_bram` (`fpga/npu2_bram/named_paths.rpt`):

| Path | Slack |
| --- | ---: |
| the memory's answer to the next request | +3.471 ns |
| the memory's readiness to the next request | +4.066 ns |
| the register port | +2.487 ns |
| a memory error to the stop | +8.357 ns |
| operands through the 8×8 products | +2.581 ns |
| the accumulate into an output bank | +5.120 ns |
| a buffer read to the operands | +4.666 ns |
| a loaded word into a buffer | +4.421 ns |
| an output bank to a write | +1.931 ns |
