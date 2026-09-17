# Phase 12.5 closeout: machine timer

Source revision: `c3ec874d00155bd297a56dc55290fb7795651b49`
Overlay: all-engine coherent RV32IMA (harts=2, L1, DMA, Xasterdot8, 4x4 NPU)
Clock: 31.25 MHz

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Frozen contract and firmware | `spec/timer.md`, `input/timer_interval.c`, `input/timer_interval.hex` |
| 2 | Unit and firmware scoreboards | `verification/make-check.log` |
| 3 | Simulation study and fresh repeats | `simulation/study.json` |
| 4 | Routed FPGA overlay | `fpga/` |
| 5 | Physical Pynq-Z1 acceptance | `physical/` |
| 6 | Clean source and immutable mapping | `source/source-state.json` |

## Results

- Unit scoreboard: free-run, exact compare match, clear/enable/disable,
  byte-strobed compare merge, 64-bit wrap and reset.
- Firmware interval test: `TIME == COMPARE` match with a bounded poll latency of
  75 cycles in simulation, agreeing with the coherent ABI 4 cycle counter over
  the same window.
- Routed overlay: WNS +5.200 ns, TNS 0, WHS +0.026 ns, THS 0, zero routing
  errors and a passing five-scenario reset signoff.
- Physical: two warm boots on the Pynq-Z1 at 31.25 MHz, each reporting
  `TIMER PASS` with an 84-cycle match latency and a clean STOPPED snapshot.

## Audit

```
python3 scripts/audit_timer.py docs/results/phase12.5/closeout-c3ec874 --current
```
