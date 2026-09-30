# PicoRV32 timing baseline v2 — corrected constraints, chosen flow settings

Supersedes [`../picorv32-baseline`](../picorv32-baseline/README.md), whose SKY130
constraints lacked clock uncertainty, timing derate, a maximum-transition
limit, and input drive and output load (see its README). Captured on 29–30
September 2026 on source revision `40e028d` plus the uncommitted pre-18.1
files hashed below. `SHA256SUMS` covers every file here
(`scripts/timing/retain.py`).

## Settings

- **SKY130:** LibreLane 3.0.14, SKY130 `hd` cells, 10 ns, constraints
  `asic/sky130/constraints.core.sdc` (LibreLane's default constraint set:
  0.25 ns clock uncertainty, 5% early/late derate, 0.75 ns maximum transition,
  `inv_2` input drive, 33.4 fF output load, propagated clocks after CTS).
  Synthesis `SYNTH_STRATEGY "DELAY 1"`, chosen by measurement (below), and
  post-global-route timing repair (`RUN_POST_GRT_RESIZER_TIMING`, on in every
  run compared; LibreLane 3.0.14 has no repair after detailed routing). Run
  `p18-sdc-picorv32-delay-1`.
- **FPGA:** Vivado 2025.1, `xc7z020clg400-1`, out of context, 10 ns. Two tops:
  `core` (PicoRV32 alone; register-to-register paths — the 18.0 capture,
  reused, since its inputs are unchanged) and `core_bram`
  (PicoRV32 with the shell's 96 KiB memory as block RAM inside the block,
  addressed from the look-ahead port, so core-to-memory paths are timed).

## Results (worst register-to-register setup slack at 10 ns)

| Target | Slack | Implied period / Fmax | Hold | Area |
| --- | ---: | ---: | ---: | --- |
| FPGA, `core` | +3.194 ns | 6.81 ns / 146.9 MHz | +0.093 ns | 1,478 LUTs, 1,054 FFs |
| FPGA, `core_bram` | +1.613 ns | 8.39 ns / 119.2 MHz | +0.108 ns | 1,673 LUTs, 1,082 FFs, 32 BRAM36 |
| SKY130 `nom_tt_025C_1v80` | +3.974 ns | 6.03 ns / 166.0 MHz | +0.306 ns | 159,211 µm² (17,748 cells, 2,076 flops) |
| SKY130 `max_tt_025C_1v80` | +3.762 ns | 6.24 ns / 160.3 MHz | +0.308 ns | |
| SKY130 `nom_ss_100C_1v60` | −1.495 ns | 11.50 ns / 87.0 MHz | +0.827 ns | |
| SKY130 `max_ss_100C_1v60` (slow signoff) | −1.970 ns | 11.97 ns / 83.5 MHz | +0.833 ns | |
| SKY130 `max_ff_n40C_1v95` | +5.928 ns | 4.07 ns / 245.6 MHz | +0.101 ns | |

Route DRC 0. The FPGA `core_bram` critical path is the look-ahead address fanning
out to 32 block RAMs (7.1 of its 7.7 ns is routing).

**Open physical issue:** maximum-transition violations against the 0.75 ns
limit remain at every corner (1,199 at `nom_tt`, 4,754 and 67 capacitance at
`max_ss`); post-global-route design repair (`RUN_POST_GRT_DESIGN_REPAIR`) cut
them to 745 at `nom_tt` but only to 4,491 at `max_ss`, and cost 0.7 ns of
slow-corner slack, so it is off.

**Flow correlation:** the resizer's estimate after global routing showed the
slow corner met (+0.04 ns) where signoff extraction finds −1.97 ns; its wire
RC estimate (tech LEF; `LAYERS_RC` unset) is optimistic. These slacks are the
signoff figures; correlating the flow is the next step (docs/phase18.md). Clean
electrical signoff is a Phase 20 gate; these figures are block-feasibility
evidence.

## How the synthesis strategy was chosen

PicoRV32, same constraints, post-global-route timing repair on in every run:

| `SYNTH_STRATEGY` | `max_ss` slack | Fmax | `nom_tt` slack | Std-cell area |
| --- | ---: | ---: | ---: | ---: |
| AREA 0 (LibreLane default) | −4.970 ns | 66.8 MHz | +2.302 ns | 156,799 µm² |
| AREA 3 | −2.437 ns | 80.4 MHz | +3.618 ns | 175,337 µm² |
| **DELAY 1** | **−1.970 ns** | **83.5 MHz** | **+3.974 ns** | **159,211 µm²** |
| DELAY 2 | −2.111 ns | 82.6 MHz | +3.908 ns | 159,862 µm² |
| DELAY 1 + post-global-route design repair | −2.654 ns | 79.0 MHz | +3.494 ns | 159,345 µm² |

## Inputs (SHA-256 at capture)

```
0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622  vendor/picorv32/picorv32.v
0c69ae8964e3a46d4b4dd8185be5efab79a692a199d4143877af9bea6415b6a2  verification/core/timing_picorv32.sv
3922ff0d910c9478986e57b077dbb8eca44bb839f478100a08cdf1827d9041f7  verification/core/timing_picorv32_bram.sv
f67aa79b133a5c1fd113340a76141063553c0b5c3ed91b0e888e03f851e51236  asic/sky130/constraints.core.sdc
8c1bd3845de7d39a3c7f2950d07a5230b4efa65e08e7a1e5388e7571b8a37b7b  scripts/timing/vivado_ooc.tcl
```

The run passed `SYNTH_STRATEGY` and `RUN_POST_GRT_RESIZER_TIMING` as overrides;
`asic/sky130/config.core_picorv32.json` now sets both (`asic/core/resolved.json`
records the values used).
