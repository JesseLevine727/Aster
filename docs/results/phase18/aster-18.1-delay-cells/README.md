# Aster core, milestone 18.1 — delay cells in the chosen flow

The runs behind the "chosen flow's delay cells" part of the 18.1 timing work
in [`../../../phase18.md`](../../../phase18.md), captured on 1 October 2026.
`SHA256SUMS` covers every file here (`scripts/timing/retain.py`). SKY130:
LibreLane 3.0.14, `hd` cells, 10 ns, post-route STA at nine corners.

| Folder | Run | Source (RTL) | Flow |
| --- | --- | --- | --- |
| `asic/nodly_u34`, `nodly_u36`, `nodly_u38`, `nodly` | `p18-aster-nodly-u34`, `-u36`, `-u38`, `-u40` | `7dbcb50` (the synthesized RTL of `233aa60`) | chosen, with the delay cells also excluded; 34/36/38/40% |
| `asic/picorv32_nodly` | `p18-picorv32-nodly` | PicoRV32 (`vendor/picorv32`) | the same, 40% |
| `asic/norebuf_u36`, `norebuf_u38` | `p18-aster-norebuf-u36`, `-u38` | `7dbcb50` | as `nodly`, with rebuffering off in setup repair (`PL_RESIZER_SETUP_BUFFERING` and `GRT_RESIZER_SETUP_BUFFERING` false); 36/38% |
| `asic/inv_u38` | `p18-aster-inv-u38` | `7dbcb50` plus inverted copies of the forwarding selects for Execute's stall logic, not in the history: `inv_copy.patch` is its diff against `7dbcb50` | the chosen flow before the change (delay cells allowed), 38% |

"Chosen" is the flow `asic/sky130/config.core_aster.json` and
`config.core_picorv32_rc.json` held after the 18.1 timing work (`buf_1`
excluded, LibreLane's per-corner wire-RC table); both now also exclude
`sky130_fd_sc_hd__dlygate4sd*` and `sky130_fd_sc_hd__dlymetal6s*`. Each run's
`resolved.json` records its full configuration; the `nodly` and `norebuf`
runs set the exclusions by override, with the values the configurations now
hold.

The `nodly` and `norebuf` runs share one synthesized netlist, which is
identical to that of the `233aa60` runs in `../aster-18.1-timing-work`
(`asic/jt*`): synthesis does not use the delay cells, so these runs differ
from those only in place and route. The `inv_u38` netlist has the six copy
registers (1,780 flops against 1,774). The RTL of `7dbcb50` and `233aa60` is
in git; `inv_u38`'s is `inv_copy.patch` applied to `7dbcb50`.

`buffer_census.txt` is the output of `scripts/timing/buffer_census.py` over
these runs and over earlier ones whose evidence is retained elsewhere:
`p18-aster` in `../aster-18.1` (`asic/aster`), and in
`../aster-18.1-timing-work` (folder names there) `pd`, `ex`, `wr`,
`wr_nobuf1`, `wr_chosen`, `picorv32_chosen`, `jt_u34`, `jt_u36`, `jt_u38`,
`jt` and `keepcopy_u38`. For each run it counts, from the final netlist, the
cells the repair steps inserted (by instance-name prefix and cell type), the
delay cells among them — design repair's and hold repair's apart — and the
longest serial chain of setup repair's `rebuffer` cells; and, from the slow
corner's post-route `max.rpt`, the failing register-to-register paths that
report lists (one per failing endpoint), how many pass through a delay cell,
and the most delay-cell time on one of them. It records the SHA-256 of each
netlist and report it read; the reports' hashes match the `max_rpt.sha256`
files retained with each run, and the netlists are not retained (the census
is reproducible from the runs).
