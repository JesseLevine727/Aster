# Aster core, milestone 18.1 — inverted select copies for the stall logic

The runs behind the "inverted select copies" part of the 18.1 timing work in
[`../../../phase18.md`](../../../phase18.md), captured on 1 October 2026: the
Aster core at `941bff4`, whose Execute stall logic reads registered inverted
copies of the forwarding selects. `SHA256SUMS` covers every file here
(`scripts/timing/retain.py`).

| Folder | Run | Flow |
| --- | --- | --- |
| `asic/invc_u34`, `invc_u36`, `invc_u38`, `invc` | `p18-aster-invc-u34`, `-u36`, `-u38`, `-u40` | SKY130, LibreLane 3.0.14, `hd` cells, 10 ns: the chosen flow as corrected (`asic/sky130/config.core_aster.json` at `8fe82f2`, delay cells excluded); 34/36/38/40% core utilization (38% configured, the others by override) |
| `fpga/aster`, `fpga/aster_bram`, `fpga/aster_bram_reqreg` | Vivado 2025.1, `xc7z020clg400-1`, out of context, 10 ns (`make timing-fpga-aster`) | the three tops of `../aster-18.1` |

The runs were made from a working tree whose `rtl/aster_core/aster_core.sv`
is byte-identical to `941bff4`'s (committed after the runs); the four SKY130
runs share one synthesized netlist (one conversion, then `--skip-convert`),
with the six copy registers (1,780 flops), and it is identical to that of
`p18-aster-inv-u38` in `../aster-18.1-delay-cells` (the same RTL in the flow
before the correction). Each run's `resolved.json` records its full
configuration.

`buffer_census.txt` is `scripts/timing/buffer_census.py` over the four SKY130
runs (repair-step buffers, delay cells, the longest serial rebuffer chain,
and the failing register-to-register paths at `max_ss` — none).
