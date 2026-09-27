# Phase 16 signoff analysis

Implementation revision: `5b1d0d8d9d6d708517e6dfb6b21ceb5004e6c3d4`.

## Timing

- Setup WNS `max_ss_100C_1v60` (worst): **-1.154 ns**
- Setup WNS `nom_ss_100C_1v60`: 0.353 ns
- Setup WNS `nom_tt_025C_1v80`: 10.741 ns
- Hold WNS worst corner: -0.171 ns
- Achieved Fmax at `nom_tt_025C_1v80`: ~108.0 MHz

## Physical

- Magic DRC errors: 0
- KLayout DRC errors: 0
- LVS errors: 13, unmatched nets: 1
- Antenna violating nets/pins: 0/0
- Route DRC errors: 106
- Design violations: 0

## Correctness

- Tier 1: the full AsterBench v10 catalog passes at RTL on the ASIC
  configuration (`make check` is green with 201 passing host/Verilator tests).
- Tier 2: post-layout gate-level simulation with the `nom_tt_025C_1v80` SDF
  reproduces `reduce_scalar` and `reduce_parallel` with checksums matching the
  RTL and the independent oracle (see `docs/results/phase16/tier2-gate-level/`).
- Tier 3: static signoff (STA, RCX/SPEF, antenna, DRC, LVS) is recorded in the
  `physical/` directory of this bundle.

## Compatibility

No frozen v1.0 address, register, ABI or instruction was changed; the ROM/RAM
parameterisation keeps every existing simulation/FPGA configuration identical.
