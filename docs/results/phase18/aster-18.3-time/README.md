# Aster core, milestone 18.3 — timing with the time counter

The Aster core at source revision `9cbd3c1`: 18.3 as signed off, with
`time`/`timeh` reading the core's own free-running 64-bit counter (the
owner's decision). The runs started after the last edit of the RTL and the
timing wrappers. Captured on 2 October 2026. `SHA256SUMS` covers every file
here (`scripts/timing/retain.py`); the text of record is the "Milestone 18.3"
section of [`../../../phase18.md`](../../../phase18.md). The same settings and
tops as [`../aster-18.3`](../aster-18.3/README.md) (`705b5f9`, before the
counter):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.716 ns | 107.7 MHz | 2,736 | 1,353 | 4 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.177 ns | 101.8 MHz | 2,880 | 1,455 | 4 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.439 ns | 104.6 MHz | 2,875 | 1,585 | 4 | 32 |

`named_paths.rpt` holds the paths docs/cpu.md §4 names: `d_rsp_valid` to the
next request +2.881 ns and the `d_rsp_error` kill +2.227 ns (§5 form).
