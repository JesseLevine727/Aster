# Aster core, milestone 18.5 — timing

The Aster core at source revision `b087ab2`: RV32IMA with Zicsr, Zifencei
and Xasterdot8 (18.5: dot8, its four 8x8 products in DSP blocks and their sum
in the fabric, in M1). The runs started after the last edit of the RTL and the
timing wrappers. Captured on 2 October 2026. `SHA256SUMS` covers every file
here (`scripts/timing/retain.py`); the text of record is the "Milestone 18.5"
section of [`../../../phase18.md`](../../../phase18.md). The same settings and
tops as [`../aster-18.4`](../aster-18.4/README.md) (`d53c46e`):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.318 ns | 103.3 MHz | 2,830 | 1,361 | 8 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.154 ns | 101.6 MHz | 2,945 | 1,463 | 8 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.233 ns | 102.4 MHz | 2,948 | 1,593 | 8 | 32 |

`named_paths.rpt` holds the paths docs/cpu.md §4 names: `d_rsp_valid` to the
next request +3.231 ns and the `d_rsp_error` kill +2.599 ns (§5 form).
Earlier runs of the same milestone, not retained, are described in the
section: the products in LUTs (93.1, 91.7 and 90.0 MHz) and summed through
the DSP cascade (99.1, 98.4 and 99.1 MHz).
