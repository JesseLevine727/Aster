# Phase 16 `p16-f2` signoff STA — retained evidence

The Phase 16 signoff run lives in the git-ignored `asic/sky130/runs/p16-f2/`.
This note copies the figures the live documents cite, with the SHA-256 of each
source report, so they survive a clean checkout. Recorded on 29 September 2026
during the Phase 18 review; SDC period 47 ns (signoff), PnR SDC 20 ns.

## Setup-limited frequency per corner

OpenSTA's `report_clock_min_period` (in each corner's `clock.rpt`) gives
40.96 MHz at `nom_tt`, 22.12 MHz at `nom_ss` and 20.77 MHz at `max_ss`. It
leaves out the design's critical path at `nom_tt` and `nom_ss`, which starts at
an SRAM macro and is launched on the **falling** clock edge (the OpenRAM model
drives read data from the falling edge) and captured on the rising edge: a
half-cycle path. For a half-cycle path with slack *s* at period *T* = 47 ns, the
delay budget is *T*/2 − *s*, so the shortest period it allows is
2 × (23.5 ns − *s*).

| Corner | Worst path in `max.rpt` | Slack | Period it allows | `report_clock_min_period` | Setup-limited Fmax |
| --- | --- | ---: | ---: | ---: | ---: |
| `nom_tt_025C_1v80` | `soc.ram.bank.g_macro[7].macro` (fall) → flop (rise) | +10.741 ns | 25.52 ns | 24.42 ns | **≈39.2 MHz** |
| `nom_ss_100C_1v60` | same macro half-cycle path | +0.353 ns | 46.29 ns | 45.21 ns | **≈21.6 MHz** |
| `max_ss_100C_1v60` | flop → flop (full cycle), −1.154 ns; the macro half-cycle path is −0.632 ns | −0.632 ns (macro) | 48.26 ns (the flop path needs 48.15 ns) | 48.15 ns | **≈20.7 MHz** |

## Hold and electrical violations (`summary.rpt`)

Worst hold slack: `nom_ff` −0.026 ns, `max_tt` −0.133 ns, `max_ff` −0.171 ns
(all other corners met). At `max_ss`: 67 setup-violating endpoints
(TNS −37.07 ns), 85,996 max-slew and 5,443 max-capacitance violations.

## Power per corner (vectorless, 47 ns clock; `power.rpt`, total in W)

| Corner | Internal | Switching | Leakage | Total |
| --- | ---: | ---: | ---: | ---: |
| `nom_tt_025C_1v80` | 0.04698 | 0.01483 | 0.00029 | **0.06210** |
| `nom_ss_100C_1v60` | 0.04096 | 0.01160 | 0.00147 | 0.05403 |
| `max_ss_100C_1v60` | 0.04096 | 0.01221 | 0.00147 | 0.05464 |
| `max_ff_n40C_1v95` | 0.05142 | 0.01839 | 0.00030 | **0.07010** (the flow's `power__total`) |

## Source reports (paths under `asic/sky130/runs/p16-f2/`)

```
13e071cf7f38f3b24790616b91efed6f64a8930a0b4a290e8c09de90507a6a9d  final/metrics.json
0afea6a780ee796081e6c8c4998dca1a3f146db5c763f6864dbe67c21d5e0d72  57-openroad-stapostpnr/summary.rpt
d05561c21ecbd4e20db30a2e79aaaf2f916f308fc5165fc7df98c02330c0328f  57-openroad-stapostpnr/nom_tt_025C_1v80/clock.rpt
2dd6830283f0254411bdac640749c054c5b1a4f949fe312a1251b34786edc262  57-openroad-stapostpnr/nom_tt_025C_1v80/max.rpt
85650d98af93d2d9ee2b6e1a918c077e10a9d8b914562465476e7e2864b48698  57-openroad-stapostpnr/nom_tt_025C_1v80/power.rpt
afb2d93073c9ccd3cbb28a924d24b99ee55a9e7d37459caffb2636c8e2b36e4a  57-openroad-stapostpnr/nom_ss_100C_1v60/clock.rpt
8e09a6c1bbe09e462436145fdcb4b33a136cc62a03149a60e39e26fe878a8b25  57-openroad-stapostpnr/nom_ss_100C_1v60/max.rpt
4ed8f4fc36706fbdf8398db52a9e868e30fc11a3613182bbbe012cf663aef51f  57-openroad-stapostpnr/max_ss_100C_1v60/clock.rpt
e6edd3e8f4c0e497cc4a2afcb6947a0a5f17d71c08f04899037b764df5a2ce52  57-openroad-stapostpnr/max_ss_100C_1v60/max.rpt
dfa8a27de42636a3712aa8befc8eaec3d60d9629cdd47663a83ca8c76b5bebc2  57-openroad-stapostpnr/max_ff_n40C_1v95/power.rpt
```

The same half-cycle macro path is why [`phase17-memory.md`](../../phase17-memory.md)
budgets an SRAM read at half a cycle (≈4.5 ns at 100 MHz).
