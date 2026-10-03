# Aster core, milestone 18.6 — timing

The Aster core at source revision `2a3b3cf`: RV32IMA with Zicsr, Zifencei and
Xasterdot8, its CSR instructions now waiting for M1 and M2 (18.6), and its L1
instruction and data caches (4 KiB each, direct-mapped, 16-byte lines, arrays
in block RAM). The runs started after the last edit of the RTL and the timing
wrappers. Captured on 3 October 2026. `SHA256SUMS` covers every file here
(`scripts/timing/retain.py`); the text of record is the "Milestone 18.6"
section of [`../../../phase18.md`](../../../phase18.md). The first three tops
are as in [`../aster-act4`](../aster-act4/README.md) (`c24b32b`); the fourth
is new (`make timing-fpga-aster-l1`):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.472 ns | 105.0 MHz | 2,821 | 1,361 | 8 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.106 ns | 101.1 MHz | 2,936 | 1,463 | 8 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.363 ns | 103.8 MHz | 2,933 | 1,593 | 8 | 32 |
| `fpga/aster_l1` | the core, its caches, and a two-cycle block RAM behind them | +0.104 ns | 101.1 MHz | 4,189 | 2,417 | 8 | 34 |

`named_paths.rpt` in the second and third holds the paths docs/cpu.md §4
names: `d_rsp_valid` to the next request +2.416 ns and the `d_rsp_error` kill
+2.139 ns (§5 form; +4.481 and +3.770 ns with the request registered). In `fpga/aster_l1` the core's port paths end inside the
caches; it names the paths across their boundaries: the core into the data
cache +2.175 ns and out of it +0.973 ns (the data cache's error decode into
the core's kill), the core into the instruction cache +3.608 ns and out of it
+3.027 ns, the data cache to the block RAM +2.832 ns and back +2.661 ns.

The worst paths: a forwarded operand into M1's CSR-write flag (core alone); a
forwarded W result into the block RAM's write enable (§5 form, 18.2's kind);
W's result into M1's CSR-write flag (request registered); and, with the
caches, inside the core and touching no cache signal — Decode's instruction
through its own decode (illegal, operand use, the hazard check) into the
enable of Decode's registers. One run per top: Vivado's placement
varies by a few hundred picoseconds from one netlist to the next (the §5 form
has given 101.6, 105.3 and now 101.1 MHz over 18.5, ACT4 and 18.6).
