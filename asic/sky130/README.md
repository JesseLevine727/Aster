# SKY130

LibreLane flows for the SKY130 phases, driven by `scripts/run_asic.py`
(see [`docs/toolchain.md`](../../docs/toolchain.md) and
[`docs/memory.md`](../../docs/memory.md)).

| Phase | Files | Status |
| --- | --- | --- |
| [15](../../docs/phase15.md): minimal SoC | `aster_asic.sv`, `config.json`, `constraints.sdc`, `macro_placement.cfg`, `rom/`, `tb_aster_asic_gl.v` | GDS at 20 ns; see Phase 15 and the Phase 17 corrections |
| [16](../../docs/phase16.md): full v1.3 SoC, 16 KiB ROM + 16 KiB RAM cut | `aster_v1_asic.sv`, `config.v1.json`, `constraints.v1.sdc` (signoff, `CLOCK_PERIOD` = 47 ns), `constraints_pnr.v1.sdc` (20 ns place-and-route), `macro_placement.v1.cfg`, `tb_aster_v1_gl.v`, `sram_clean.v` | GDS produced; signoff **incomplete** |

`config.v1clean.json` (hold margins 0.6 ns) and `config.v1aplus.json` (lower
density, more routing iterations) are Phase 16 cleanup experiments; both
regressed and are kept only as a record. `sram/` holds the Yosys blackbox for the
OpenRAM `sky130_sram_2kbyte_1rw1r_32x512_8` macro, and `sram_clean.v` is a
functional macro model for gate-level simulation.

```sh
python3 scripts/run_asic.py --design minimal          # Phase 15
python3 scripts/run_asic.py --design v1 --run-tag NAME \
    --skip-step Magic.WriteLEF --skip-step Odb.CheckDesignAntennaProperties
```

Run directories under `runs/` are ignored by git. The two runs behind the
committed closeouts are kept: `runs/p15-gds` (Phase 15) and `runs/p16-f2`
(Phase 16). SKY130 work for v2 is Phase 22 of the
[v2 plan](../../docs/phase17-plus.md).
