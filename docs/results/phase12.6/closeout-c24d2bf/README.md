# Phase 12.6 closeout: interrupt controller

Source revision: `c24d2bfec2c5ec878ffd528601851b927fc38636`
Overlay: all-engine coherent RV32IMA (harts=2, L1, DMA, Xasterdot8, 4x4 NPU)
Clock: 31.25 MHz

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Frozen contract and firmware | `spec/interrupts.md`, `input/timer_interrupt.c`, `input/timer_interrupt.hex` |
| 2 | Unit and firmware scoreboards | `verification/make-check.log` |
| 3 | Simulation study and fresh repeats | `simulation/study.json` |
| 4 | Routed FPGA overlay | `fpga/` |
| 5 | Physical Pynq-Z1 acceptance | `physical/` |
| 6 | Clean source and immutable mapping | `source/source-state.json` |

## Results

- Unit scoreboard: edge capture, W1C, RAISE, per-hart masks, level routing and
  reset.
- Firmware: a software interrupt and a machine-timer interrupt are taken on
  hart 0, serviced, cleared and returned from while the interrupted loop
  resumes; the timer deadline agrees with the cycle counter. Match-to-handler
  latency is 594 cycles in simulation.
- Routed overlay: WNS +3.164 ns, TNS 0, WHS +0.020 ns, THS 0, zero routing
  errors and a passing five-scenario reset signoff.
- Physical: two warm boots on the Pynq-Z1 at 31.25 MHz, each reporting
  `TIMER IRQ PASS` with a 662-cycle delivery latency and a clean STOPPED
  snapshot.

## Audit

```
python3 scripts/audit_interrupts.py docs/results/phase12.6/closeout-c24d2bf --current
```
