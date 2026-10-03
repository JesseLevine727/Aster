# Aster core after the ACT4 adoption — timing

The Aster core at source revision `c24b32b`: 18.5 (RV32IMA, Zicsr, Zifencei,
Xasterdot8) with `time`/`timeh` reading the platform's `mtime` through a new
64-bit input, registered in the core (owner decision on adopting ACT4,
2 October 2026). The runs started after the last edit of the RTL and the
timing wrappers (whose tops gained the `mtime` input). Captured on 2 October
2026. `SHA256SUMS` covers every file here (`scripts/timing/retain.py`); the
text of record is the "ACT4 adopted" section of
[`../../../phase18.md`](../../../phase18.md). The same settings and tops as
[`../aster-18.5`](../aster-18.5/README.md) (`b087ab2`):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.489 ns | 105.1 MHz | 2,848 | 1,361 | 8 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.502 ns | 105.3 MHz | 2,921 | 1,463 | 8 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.474 ns | 105.0 MHz | 2,921 | 1,593 | 8 | 32 |

`named_paths.rpt` holds the paths docs/cpu.md §4 names: `d_rsp_valid` to the
next request +3.856 ns and the `d_rsp_error` kill +3.197 ns (§5 form).
