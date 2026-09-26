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

- Post-layout gate-level simulation with the `nom_tt_025C_1v80` SDF reproduces
  the Phase 1/2 oracle `Hello from Aster\n`.
- `make check` is green with 201 passing host/Verilator tests.

## Compatibility

No frozen v1.0 address, register, ABI or instruction was changed; the ROM
parameterisation keeps every existing simulation/FPGA configuration identical.
