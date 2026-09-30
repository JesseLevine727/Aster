# Pre-18.1 SKY130 flow and L1-array probes (retained runs)

The runs behind the synthesis-strategy choice and the L1 array probes in
[`docs/phase18.md`](../../../phase18.md) ("Pre-18.1 measurements"), copied from
the git-ignored `asic/sky130/runs/` by `scripts/timing/retain.py`; the chosen
PicoRV32 run itself is in [`../picorv32-baseline-v2`](../picorv32-baseline-v2/README.md).
All used post-global-route timing repair and signed off against
`asic/sky130/constraints.core.sdc` at 10 ns (LibreLane's default constraint
set); the `*-pnr-margin-*` runs placed and routed against
`constraints.core.pnr_margin.sdc` instead. Each `asic/NAME/resolved.json`
records every setting.

| Folder | Design | Settings that differ |
| --- | --- | --- |
| `asic/picorv32-area-0` | PicoRV32 | `SYNTH_STRATEGY "AREA 0"` |
| `asic/picorv32-area-3` | PicoRV32 | `SYNTH_STRATEGY "AREA 3"` |
| `asic/picorv32-delay-2` | PicoRV32 | `SYNTH_STRATEGY "DELAY 2"` |
| `asic/picorv32-delay-1-grt-design-repair` | PicoRV32 | `DELAY 1` plus post-global-route design repair |
| `asic/sram-2k-mux` | 2 KiB flip-flop array, synthesized mux read | `DELAY 1`, post-global-route design repair, 50% utilization |
| `asic/sram-512b-andor` | 512 B flip-flop array, one-hot + AND-OR read | `DELAY 1`, 30% utilization |
| `asic/picorv32-delay-1-pnr-margin-3ns-setup` | PicoRV32 | `DELAY 1`; place-and-route reads `constraints.core.pnr_margin.sdc` (3 ns extra *setup* uncertainty), signoff reads `constraints.core.sdc` |
| `asic/picorv32-delay-1-pnr-margin-3ns` | PicoRV32 | **superseded:** an earlier margin SDC that also applied the 3 ns to hold (needless hold buffers, +20.7% area) |
| `asic/sram-512b-andor-pnr-margin-3ns` | 512 B flip-flop array, one-hot + AND-OR read | **superseded:** the same hold-margin bug; 25% utilization, `GRT_ADJUSTMENT` 0.15 |

`summary.json` in each folder is `scripts/timing/sky130_summary.py` over the
retained `final/metrics.json` and `resolved.json`; `56-openroad-stapostpnr/worst_paths.txt`
(or the step number of that run) holds each corner's worst register-to-register
path, and `max_rpt.sha256` the hashes of the full reports. These are signoff
figures; the resizer's own estimates before extraction were optimistic (see
docs/phase18.md).

Inputs (SHA-256) of the corrected runs:

```
0836050971b3c6cdd28ac3b1e5719a67fb645161912bef1e472e63995ceb0622  vendor/picorv32/picorv32.v
0c69ae8964e3a46d4b4dd8185be5efab79a692a199d4143877af9bea6415b6a2  verification/core/timing_picorv32.sv
bc9f54efe2e755c97b1e273e7ecb11fff1f7402de1e71d98e6c7b25507f43b2b  verification/core/timing_sram_array.sv
f67aa79b133a5c1fd113340a76141063553c0b5c3ed91b0e888e03f851e51236  asic/sky130/constraints.core.sdc
25f643944e5b21e9b9e2802599338199c6481fcf808e5302ca6c4b1788239bce  asic/sky130/constraints.core.pnr_margin.sdc
```
