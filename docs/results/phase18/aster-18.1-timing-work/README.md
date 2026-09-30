# Aster core, milestone 18.1 — timing work after the first report

The runs behind the "18.1 timing work after the first report" table in
[`../../../phase18.md`](../../../phase18.md), captured on 30 September 2026.
`SHA256SUMS` covers every file here (`scripts/timing/retain.py`). SKY130:
LibreLane 3.0.14, `hd` cells, 10 ns, post-route STA at nine corners; FPGA:
Vivado 2025.1, `xc7z020clg400-1`, out of context, 10 ns.

| Folder | Run | Source (RTL) | Flow |
| --- | --- | --- | --- |
| `asic/grt_repair` | `p18-aster-grt-repair` | `9971ea1` | baseline + design repair after global routing |
| `asic/wt` | `p18-aster-wt` | `34cf6d5` | baseline |
| `asic/wt_rc` | `p18-aster-wt-rc` | `34cf6d5` | baseline + per-corner wire RC (`LAYERS_RC`, `VIAS_R`) |
| `asic/pd` | `p18-aster-pd` | `9d29175` | baseline |
| `asic/ex` | `p18-aster-ex` | `3cf31ce` | baseline |
| `asic/wr` | `p18-aster-wr` | `209ddcb` | baseline |
| `asic/wr_nobuf1` | `p18-aster-wr-nobuf1` | `209ddcb` | baseline without `buf_1` |
| `asic/wr_chosen` | `p18-aster-wr-nobuf1-rc` | `209ddcb` | chosen: without `buf_1`, per-corner wire RC |
| `asic/wr_chosen_u38` | `p18-aster-wr-nobuf1-rc-u38` | `209ddcb` | chosen, `FP_CORE_UTIL` 38 |
| `asic/final_chosen` | `p18-aster-sc-rc` | `9ffb3ab` | chosen |
| `asic/picorv32_nobuf1` | `p18-picorv32-nobuf1` | PicoRV32 (`vendor/picorv32`) | baseline without `buf_1` |
| `asic/picorv32_chosen` | `p18-picorv32-nobuf1-rc` | PicoRV32 | chosen |
| `fpga/aster`, `fpga/aster_bram`, `fpga/aster_bram_reqreg` | Vivado | `9ffb3ab` | as in `../aster-18.1` |

"Baseline" is the PicoRV32 baseline v2 flow (`constraints.core.sdc`,
`SYNTH_STRATEGY "DELAY 1"`, post-global-route timing repair). Each run's
`resolved.json` records its full configuration. The per-corner wire-RC values
are the SKY130 table LibreLane's `config/pdk_compat.py` carries commented out.
Runs `p18-aster-wr-nobuf1*` and the PicoRV32 runs set `buf_1`'s exclusion by
override; the chosen flow is now `asic/sky130/config.core_aster.json` and
`config.core_picorv32_rc.json`. Each source revision's RTL was the one
converted when its run started; the RTL of `9971ea1`, `34cf6d5`, `9d29175`,
`3cf31ce`, `209ddcb` and `9ffb3ab` is in git.

Where a corner's report lists only port paths (every register-to-register
path comfortably met), `worst_paths.txt` says so; `summary.json` has the
worst register-to-register slack from the run's metrics either way.
