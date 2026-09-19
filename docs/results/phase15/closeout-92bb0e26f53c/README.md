# Phase 15 closeout (92bb0e26f53c)

Self-contained evidence for the minimal SKY130/LibreLane flow, built from
`asic/sky130/runs/p15-gds`.

- Setup WNS worst corner: 1.679 ns
- Hold WNS worst corner: 0.270 ns
- Magic/KLayout DRC, LVS errors, antenna nets: 0 / 0 / 0 / 0
- Gate-level SDF simulation: PASS (`Hello from Aster`)

Validate with `python3 scripts/audit_phase15.py docs/results/phase15/closeout-92bb0e26f53c --current`.
