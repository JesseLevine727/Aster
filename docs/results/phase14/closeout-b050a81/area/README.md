# Phase 14.6: routed area and timing

Per-configuration Vivado routed reports for the coherent all-engine SoC on the
Pynq-Z1 (xc7z020). The point is **performance per area**: what each architectural
knob costs in LUTs, registers and maximum frequency, against what it buys.

| Configuration | LUTs | LUT % | FFs | FF % | BRAM | WNS | implied Fmax |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 hart, all engines | 19 734 | 37.1% | 12 672 | 11.9% | 32 | +4.776 ns | 36.7 MHz |
| 2 harts, no L2 | 25 499 | 47.9% | 19 425 | 18.3% | 32 | +3.164 ns | 34.7 MHz |
| 2 harts, 1 KiB L2 | 29 829 | 56.1% | 29 586 | 27.8% | 32 | +3.158 ns | 34.7 MHz |

Reports are in `reports/{h1,baseline,l2}/`. The `baseline` overlay is from the
v1.0 revision; the `l2` overlay is from the v1.1 revision; the `h1` overlay is
built from the current tree. The comparison is the area/timing delta, so the
revision spread is deliberate and recorded in `area.json`.

## Deltas

| Change | LUT delta | FF delta | Timing delta | Measured benefit |
| --- | ---: | ---: | ---: | --- |
| +1 hart | +5 765 (+10.8 pp) | +6 753 (+6.4 pp) | −1.61 ns | 1.30× on `reduce_parallel` |
| +1 KiB L2 | +4 330 (+8.1 pp) | +10 161 (+9.6 pp) | −0.006 ns | up to 1.98× at `wait64` |

## Findings

- **The L2 is the better area investment.** It costs fewer LUTs than the second
  hart (+8.1 pp vs +10.8 pp) and does **not** reduce Fmax, while delivering a
  larger measured speedup (up to 1.98× vs 1.30×). The second hart's area also
  buys the higher WNS penalty, because its coherence and arbitration logic sits
  on the critical path.
- **Registers are the L2's real cost** (+9.6 pp), not LUTs — the 1 KiB array is
  distributed and its tag/state logic is register-heavy.
- **BRAM is unchanged (32 tiles)** in all three: the arrays map to distributed
  RAM at this size, so the L2 does not consume a block RAM.
- **Maximum frequency is essentially flat** at ~34.7 MHz for the two-hart
  configurations; the single-hart build reaches 36.7 MHz. None of the knobs
  moves Fmax much, so performance per area is driven by the speedup, not the
  clock.

## Limitation

The memory-latency and cache-geometry sweeps ran on the minimal SoC, which has
no FPGA overlay target, so they have no routed timing here. Only the coherent
configurations are measured.
