# Phase 18.0 — PicoRV32 timing baseline at 10 ns (retained evidence)

The timed block is `timing_picorv32` (`verification/core/timing_picorv32.sv`):
PicoRV32 with the v1 core's parameters except IRQ and PCPI (both off), and only
its native memory port. Captured on 29 September 2026 from source revision
`0d59712` plus the (then uncommitted) Phase 18.0 files hashed below, by `make
timing-fpga-picorv32` (Vivado 2025.1) and `make timing-asic-picorv32`
(LibreLane 3.0.14, SKY130 PDK `8afc8346`). The run directories are git-ignored,
so the reports are copied here; `SHA256SUMS` covers every file in this folder.

| Target (10 ns) | Worst reg-to-reg setup slack | Implied period / Fmax | Hold | Area |
| --- | --- | --- | --- | --- |
| Vivado, `xc7z020clg400-1`, out of context, routed | +3.194 ns | 6.81 ns / 146.9 MHz | +0.093 ns | 1,478 LUTs, 1,054 FFs, 0 BRAM, 0 DSP |
| SKY130 post-route, `nom_tt_025C_1v80` | +2.804 ns | 7.20 ns / 139.0 MHz | +0.418 ns | 132,348 µm² standard cells |
| SKY130 post-route, `nom_ss_100C_1v60` | −4.186 ns | 14.19 ns / 70.5 MHz | +0.853 ns | 744 max-slew / 81 max-cap violations |
| SKY130 post-route, `max_ss_100C_1v60` | −4.325 ns | 14.32 ns / 69.8 MHz | +0.855 ns | 1,194 max-slew / 125 max-cap violations |
| SKY130 post-route, `max_ff_n40C_1v95` | +5.523 ns | 4.48 ns / 223.4 MHz | +0.265 ns | — |

SKY130 cell count: 14,673 standard cells including 4,059 tap cells
(5,079 µm²); 2,076 sequential cells; 46% utilization; route DRC 0.

## Limits of this baseline

- **Synthesis strategy.** The flow ran LibreLane's default `SYNTH_STRATEGY
  "AREA 0"`, enforced timing only at the typical corners
  (`TIMING_VIOLATION_CORNERS ['*tt*']`), and skipped post-global-route timing
  repair. The slow-corner critical path (`worst_paths.txt`) runs from the
  multiplier's operand register into the divider's operand negation, which the
  area-oriented mapping builds as a long OR-gate ripple chain; Vivado's worst
  path is the same logic on CARRY4 chains. The 70 MHz slow-corner figure
  therefore measures this flow configuration as much as PicoRV32. The
  synthesis strategy for Phase 18 is chosen, and this baseline re-run with it,
  before the Aster core's first timing report (milestone 18.1).
- **Ports are not timed.** Only register-to-register paths set the implied
  period: the Vivado run leaves ports unconstrained and SKY130 applies a
  nominal 20% I/O delay. Core-to-SRAM paths are timed from 18.1 with the SRAM
  model inside the timed top or with declared port budgets.

## Files

- `fpga/`: `summary.txt`, `timing_summary.rpt`, `utilization.rpt`, `clock.xdc`.
- `asic/`: `resolved.json` (the resolved LibreLane configuration),
  `final/metrics.json`, `55-openroad-stapostpnr/summary.rpt`,
  `55-openroad-stapostpnr/worst_paths.txt` (each corner's worst setup path,
  verbatim), and `picorv32.json`, which `scripts/timing/sky130_summary.py`
  recomputes from the retained `metrics.json` and `resolved.json` (a host test
  checks it).

The full per-corner `max.rpt` files (6–7 MB each) are not retained; their
SHA-256 under `asic/sky130/runs/p18-picorv32/55-openroad-stapostpnr/`:

```
68667285b484b901c0cf320129a0e117b8240bdd4b96c8e0f1ff5e3015320f00  nom_tt_025C_1v80/max.rpt
e7e95dd9289e5ab9d23c73ddf25fb1a9ce5a589e090f14864fbce191dcbe339a  nom_ss_100C_1v60/max.rpt
1b7a5525fe4bf1ddb9a325e809ff399bcf3864f10639ddd1ec8dcfab6d34b700  max_ss_100C_1v60/max.rpt
```

Inputs (SHA-256):

```
0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622  vendor/picorv32/picorv32.v
0c69ae8964e3a46d4b4dd8185be5efab79a692a199d4143877af9bea6415b6a2  verification/core/timing_picorv32.sv
9a9b96726549a5fc954057324fc6ec6202a4b7483a37da84e3482b0895cea6ab  asic/sky130/config.core_picorv32.json
1cbe5e28413e96850a8eff1cbe7e158b4d1cd92e57ba973c6aef550192ba8be5  asic/sky130/constraints.core.sdc
8c1bd3845de7d39a3c7f2950d07a5230b4efa65e08e7a1e5388e7571b8a37b7b  scripts/timing/vivado_ooc.tcl
```
