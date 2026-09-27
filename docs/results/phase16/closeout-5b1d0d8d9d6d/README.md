# Phase 16 closeout (5b1d0d8d9d6d)

Self-contained evidence for the Phase 16 full v1.3 SKY130/LibreLane flow, built
from `asic/sky130/runs/p16-f2`.

- Setup WNS worst corner (`max_ss`): -1.154 ns
- Setup WNS `nom_ss` / `nom_tt`: 0.353 / 10.741 ns
- Hold WNS worst corner (`max_ff`): -0.171 ns
- Magic / KLayout DRC: 0 / 0
- Antenna violating nets: 0
- Route (TritonRoute) DRC: 106
- LVS errors: 13
- Power (TT): 70.1 mW
- Tier 2 gate-level: `reduce_scalar` and `reduce_parallel` PASS

Validate with `python3 scripts/audit_phase16.py docs/results/phase16/closeout-5b1d0d8d9d6d --current`.
