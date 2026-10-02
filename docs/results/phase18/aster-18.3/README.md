# Aster core, milestone 18.3 — Zicsr, traps, interrupts and counters: timing

The Aster core with Zicsr, machine-mode traps and interrupts and the counters
(`rtl/aster_core/`) at source revision `705b5f9`: the runs started after the
last edit of the RTL's logic and of the timing wrappers; after them only a
comment in `aster_core.sv`'s header was reworded. Captured on 2 October 2026. `SHA256SUMS` covers every file here
(`scripts/timing/retain.py`); the text of record is the "Milestone 18.3"
section of [`../../../phase18.md`](../../../phase18.md).

FPGA only (SKY130 was dropped on 1 October 2026): Vivado 2025.1,
`xc7z020clg400-1`, out of context, 10 ns, register to register (`make
timing-fpga-aster`), the three tops of [`../aster-18.1`](../aster-18.1/README.md):

| Folder | Top | WNS | Implied fmax | LUTs | Flip-flops | DSPs | BRAM tiles |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fpga/aster` | the core alone | +0.514 ns | 105.4 MHz | 2,695 | 1,287 | 4 | 0 |
| `fpga/aster_bram` | the core with the block RAM in the §5 form | +0.055 ns | 100.6 MHz | 2,764 | 1,389 | 4 | 32 |
| `fpga/aster_bram_reqreg` | the same with the request registered | +0.462 ns | 104.8 MHz | 2,759 | 1,519 | 4 | 32 |

From 18.3 the interrupt lines are inputs of the block-RAM tops (they were tied
low before), so the interrupt logic is timed. `named_paths.rpt` holds the
paths docs/cpu.md §4 names: from `d_rsp_valid` to the next request (+2.866 ns
in the §5 form) and the `d_rsp_error` kill (+2.451 ns), and the worst paths
from those two registers anywhere.
