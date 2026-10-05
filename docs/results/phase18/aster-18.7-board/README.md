# Aster core, milestone 18.7 — the PYNQ-Z1 run at 100 MHz

The Aster core with its L1 caches on the PYNQ-Z1, at source revision
`23733e5`: the board design `rtl/soc/aster_core_pynq.sv` (the core, its
instruction and data caches, 128 KiB of block-RAM main memory, the CPU
kernels' register page, an AXI4-Lite port to the ARM side) in the Zynq block
design of `fpga/pynq_z1/build_aster_core.tcl`, clocked by the PS's FCLK0 at
100 MHz. The bitstream was built from those files (the Vivado session began
after their last edit, and they were committed unchanged) and run on
5 October 2026 by `make aster-board` (`scripts/aster_board.py`, which runs
`scripts/aster_board_remote.py` on the board). The text of record is the
"Milestone 18.7" section of [`../../../phase18.md`](../../../phase18.md).
`SHA256SUMS` covers every file here.

**Timing, in context** (`fpga/`: Vivado 2025.1, the whole device with the
PS and its interface, the v1 Linux overlay's directives, signed off by
`fpga/pynq_z1/signoff.tcl`): FCLK0 constrained at 10.000 ns; worst setup slack
**+0.140 ns**, worst hold slack +0.022 ns, no failing endpoint among 14,547;
all 9,506 routable nets routed; no DRC error. The worst path is inside the
core: M1's result through forwarding into Execute's operand select
(`core/m1_reg[result]` → `core/fsel1_reg`). 5,971 LUTs, 4,167 flip-flops,
39 block-RAM tiles (main memory 32, the page 4, the caches' arrays 2, the
console 1), 8 DSPs. The 4 MB bitstream is not kept; `fpga/bitstream.sha256`
holds its hash, which the board run checked.

**The board run** (`report.json`, `remote.log`): FCLK0 set to 1000 MHz / (5 ×
2) from the IO PLL (the registers before and after are recorded) and
**measured at 99.999 MHz** — a spinning program's cycle count over a second
of the board's wall time. 100 programs — the nine CPU kernels and 91
self-checking programs (riscv-tests rv32ui, rv32um, rv32ua and rv32mi, the
directed, trap, selfcheck and C programs) — each loaded through AXI into
zeroed memory and run: **all 100 end as in the CPU shell, cycle for cycle** —
a kernel with the shell's console byte for byte (its record and checksums)
and the shell's window, the others storing 1 to tohost at the shell's cycle
with the shell's instruction count — including `selfcheck/dot8_arith`
(3,882,380 cycles, 2,645,248 instructions) and `c/dot8_jobs` (1,743,609
cycles). Two programs that depend on the shell's memory ending where the
program does are not run on the fixed 128 KiB map
(`directed/wrongpath_fetch_fault`, `traps/fetch_traps`).

The kernels on the board (the cached core on the two-cycle block RAM; the
window as the shell measures it):

| Kernel | Window cycles | Instructions | CPI |
| --- | ---: | ---: | ---: |
| CoreMark (1 iteration) | 466,606 | 284,865 | 1.638 |
| Dhrystone | 857,421 | 492,302 | 1.742 |
| sort/search | 696,877 | 420,942 | 1.656 |
| FFT | 317,562 | 248,399 | 1.278 |
| strided | 2,722 | 1,559 | 1.746 |
| scalar Conv2D, coherent SoC | 1,481,208 | 971,011 | 1.525 |
| scalar reduction | 75,234 | 53,305 | 1.411 |
| Conv2D, minimal top | 1,111,121 | 704,346 | 1.578 |
| DOT8 Conv2D | 1,484,436 | 1,145,218 | 1.296 |

At 100 MHz CoreMark's iteration takes 4.67 ms. The §7 gate's comparison with
PicoRV32 is made in the shell without the L1 (on the one-cycle memory, as §7
requires); this run shows that the shell's cycle counts for the cached core
on the two-cycle memory are the silicon's (the gate's configuration and
PicoRV32 were not run on the board). `compare.log` is that comparison: the
board's report against the shell's runs of the same programs
(`scripts/aster_board.py --report`). `fpga/named_paths.rpt` holds the worst
path into each part of the board design's own logic, and the core's.
