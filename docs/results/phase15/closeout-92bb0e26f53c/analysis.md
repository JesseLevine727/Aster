# Phase 15 signoff analysis

Implementation revision: `92bb0e26f53cbde8d6e0ff1aada813259675e540`.

## Timing

- Setup WNS `max_ss_100C_1v60` (worst): **1.679 ns**
- Setup WNS `nom_ss_100C_1v60`: 2.559 ns
- Setup WNS `nom_tt_025C_1v80`: 6.303 ns
- Hold WNS worst corner: 0.270 ns
- Achieved Fmax at `nom_tt_025C_1v80`: ~73.0 MHz

## Physical

- Magic DRC errors: 0
- KLayout DRC errors: 0
- LVS errors: 0, unmatched nets: 0
- Antenna violating nets/pins: 0/0
- Route DRC errors: 0
- Design violations: 0

## Correctness

- Post-layout gate-level simulation with the `nom_tt_025C_1v80` SDF reproduces
  the Phase 1/2 oracle `Hello from Aster\n`.
- `make check` is green with 201 passing host/Verilator tests.

## Compatibility

No frozen v1.0 address, register, ABI or instruction was changed; the ROM
parameterisation keeps every existing simulation/FPGA configuration identical.
