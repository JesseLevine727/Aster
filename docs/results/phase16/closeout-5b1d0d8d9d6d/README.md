# Phase 16 closeout (5b1d0d8d9d6d)

Self-contained evidence for the minimal SKY130/LibreLane flow, built from
`asic/sky130/runs/p16-f2`.

- Setup WNS worst corner: -1.154 ns
- Hold WNS worst corner: -0.171 ns
- Magic/KLayout DRC, LVS errors, antenna nets: 0 / 0 / 0 / 0
- Gate-level SDF simulation: PASS (`Hello from Aster`)

Validate with `python3 scripts/audit_phase16.py docs/results/phase16/closeout-5b1d0d8d9d6d --current`.
