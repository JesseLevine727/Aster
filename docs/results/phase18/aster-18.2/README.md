# Aster core, milestone 18.2 — the M extension's timing

The runs behind the "Milestone 18.2" timing in
[`../../../phase18.md`](../../../phase18.md), captured on 1 October 2026.
`SHA256SUMS` covers every file here (`scripts/timing/retain.py`). SKY130:
LibreLane 3.0.14, `hd` cells, 10 ns, post-route STA at nine corners, in the
chosen flow as corrected (`../aster-18.1-delay-cells`); FPGA: Vivado 2025.1,
`xc7z020clg400-1`, out of context, 10 ns (`make timing-fpga-aster`).

| Folder | Run | Source (RTL) | Flow |
| --- | --- | --- | --- |
| `asic/m_u38` | `p18-aster-m-u38` | `4f42794` (the M extension; four 17×17 multiplies in M1) | chosen, 38% |
| `asic/m2_u34`, `m2_u36`, `m2_u38`, `m2_u40` | `p18-aster-m2-u34` … `-u40` | `d74408d` (36-cycle divider) | chosen with `SYNTH_MUL_BOOTH` (Booth multipliers in synthesis), 34/36/38/40% |
| `asic/m2nb_u38`, `m2nb_u40` | `p18-aster-m2nb-u38`, `-u40` | `d74408d` | chosen, without Booth (override), 38/40% |
| `asic/m3_u34`, `m3_u36`, `m3_u38`, `m3_u40` | `p18-aster-m3-u34` … `-u40` | carry-save multiplier compressing to two vectors in M1, not in the history: `asic/m3_u38/two_vector.patch` is its diff against `bdfa836` | chosen with `SYNTH_MUL_BOOTH` (no multiply operator left, so it maps none), 34/36/38/40% |
| `asic/m3ss_u38` | `p18-aster-m3ss-u38` | as `m3` | as `m3`, synthesized at `max_ss_100C_1v60` (`SYNTH_CORNER`), 38% |
| `asic/m4_u34`, `m4_u36`, `m4_u38`, `m4_u40` | `p18-aster-m4-u34` … `-u40` | `bdfa836` (carry-save multiplier, four vectors out of M1) | chosen (no Booth setting), 34/36/38/40% |
| `fpga/m_aster`, `m_aster_bram`, `m_aster_bram_reqreg` | Vivado | `4f42794` | the three tops of `../aster-18.1` |
| `fpga/m4_aster`, `m4_aster_bram`, `m4_aster_bram_reqreg` | Vivado | `bdfa836` | the same |

Each run's `resolved.json` records its full configuration. Runs of one RTL
share one synthesized netlist where their synthesis settings agree: the four
`m2` runs, the four `m3` runs and the four `m4` runs each (one conversion,
then `--skip-convert`), and the two `m2nb` runs another; `m3ss` differs in its
synthesis corner. The
`m3` netlists have the two carry-save registers of `two_vector.patch`
(`mul_sum`, `mul_carry`; 2,113 flops), the `m4` netlists the four vectors
(2,206 flops). `two_vector.patch` was reconstructed from `bdfa836` after the
runs (the variant was edited in place) and checked against those registers.
The `m2`/`m2nb`/`m3`/`m3ss`/`m4` runs other than the first of each RTL were
run in parallel, each under its own `scripts/memguard.sh` lock and a 12 GB
cap (a core run peaks near 4 GB).

`p18-aster-m-u40` was stopped before it finished (the sweep of `4f42794` was
cut short when its result was clear) and is not retained.

`buffer_census.txt` is `scripts/timing/buffer_census.py` over the sixteen
SKY130 runs: no delay cells, and the failing register-to-register paths at
`max_ss` per run (one per failing endpoint).
