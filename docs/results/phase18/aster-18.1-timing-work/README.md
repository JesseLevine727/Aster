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
| `asic/final_chosen` | `p18-aster-sc-rc` | `9ffb3ab` (since reverted) | chosen |
| `asic/picorv32_nobuf1` | `p18-picorv32-nobuf1` | PicoRV32 (`vendor/picorv32`) | baseline without `buf_1` |
| `asic/picorv32_chosen` | `p18-picorv32-nobuf1-rc` | PicoRV32 | chosen |
| `asic/onehot`, `asic/onehot_u38` | `p18-aster-oh`, `-oh-u38` | `bf247e8` | chosen, 40% and 38% |
| `asic/jt_u34`, `jt_u36`, `jt_u38`, `jt` | `p18-aster-jt-u34`, `-u36`, `-u38`, `p18-aster-jt` | `233aa60` | chosen, 34/36/38/40% |
| `fpga/aster`, `fpga/aster_bram`, `fpga/aster_bram_reqreg` | Vivado | `233aa60` | as in `../aster-18.1` |
| `asic/rr_u34`, `rr_u36`, `rr_u38`, `rr` | `p18-aster-rr-u34`, `-u36`, `-u38`, `-u40` | `85a2607` registered operand readiness (reverted in `b31761b`) | chosen, 34/36/38/40% |
| `fpga/rr_aster`, `fpga/rr_aster_bram`, `fpga/rr_aster_bram_reqreg` | Vivado | `85a2607` | as in `../aster-18.1` |
| `asic/keepcopy_u38` | `p18-aster-rw-u38` | `85a2607` plus a `keep`-marked low-bit copy of rs1's select, not in the history (synthesis merged the copy into the select): `keep_copy.patch` is its diff against `aa0bba3` | chosen, 38% |

"Baseline" is the PicoRV32 baseline v2 flow (`constraints.core.sdc`,
`SYNTH_STRATEGY "DELAY 1"`, post-global-route timing repair). Each run's
`resolved.json` records its full configuration. The per-corner wire-RC values
are the SKY130 table LibreLane's `config/pdk_compat.py` carries commented out.
Runs `p18-aster-wr-nobuf1*` and the PicoRV32 runs set `buf_1`'s exclusion by
override; the chosen flow is now `asic/sky130/config.core_aster.json` and
`config.core_picorv32_rc.json`. `scripts/run_asic.py` converts the RTL into
one shared file when it is invoked, and a queued run reads that file when it
starts; each run's revision above was checked against its own lint and
synthesis logs (the source locations match sv2v output of that revision), and
for `bf247e8` and `233aa60` — whose conversions differ in one line only — by
the timeline and the synthesized netlists (the `oh` runs share one netlist,
the `jt` runs another). PicoRV32 ran at 40% utilization and was not swept.
The four `rr` runs share one synthesized netlist (one conversion, then
`--skip-convert`), which has the readiness registers and not the select copy.
In the `keepcopy_u38` netlist synthesis merged the copy into the select: the
only rs1-select flops carry the copy's name (`fsel1_lo`), and the flop count,
1,776, is the `rr` netlist's. The RTL of `9971ea1`, `34cf6d5`,
`9d29175`, `3cf31ce`, `209ddcb`, `9ffb3ab`, `bf247e8`, `233aa60` and `85a2607`
is in git; `keepcopy_u38`'s is `keep_copy.patch` applied to `aa0bba3`.

Where a corner's report lists only port paths (every register-to-register
path comfortably met), `worst_paths.txt` says so; `summary.json` has the
worst register-to-register slack from the run's metrics either way.
