# Aster core, milestone 18.4 — timing

The Aster core at source revision `d53c46e`: RV32IMA with Zicsr and
Zifencei (18.4: the atomics and `fence.i`). The runs started after the last
edit of the RTL and the timing wrappers. Captured on 2 October 2026.
`SHA256SUMS` covers every file here (`scripts/timing/retain.py`); the text of
record is the "Milestone 18.4" section of
[`../../../phase18.md`](../../../phase18.md). The same settings and tops as
[`../aster-18.3-time`](../aster-18.3-time/README.md) (`9cbd3c1`, 18.3 as
signed off):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.763 ns | 108.3 MHz | 2,808 | 1,359 | 4 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.448 ns | 104.7 MHz | 2,866 | 1,461 | 4 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.745 ns | 108.0 MHz | 2,870 | 1,591 | 4 | 32 |

`named_paths.rpt` holds the paths docs/cpu.md §4 names: `d_rsp_valid` to the
next request +3.455 ns and the `d_rsp_error` kill +1.622 ns (§5 form).
