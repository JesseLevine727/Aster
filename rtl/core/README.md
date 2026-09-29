# CPU integration

`aster_picorv32.sv` is the Aster-owned integration boundary around the pinned
PicoRV32 source in `vendor/picorv32/`. The configuration is RV32IM: multiply and
divide are enabled, compressed instructions are disabled, and PCPI carries the
co-processor instructions below. PicoRV32 is the v1 reference core; v2 replaces
it with the Aster core designed in Phase 18 (see the
[v2 plan](../../docs/phase17-plus.md#6-phase-17-sequence)).

- `aster_hart.sv`: PicoRV32 plus a non-coherent instruction/data L1 pair, used
  by `aster_minimal` and the Phase 5 `aster_multicore` top.
- `aster_atomic_hart.sv`: PicoRV32 plus a ROM instruction cache and the PCPI
  atomic and DOT8 units, used by the coherent SoC.
- `aster_pcpi_atomic.sv`: RV32A (LR/SC and AMOs) as PCPI commands executed by
  the atomic fabric.
- `aster_pcpi_dot8.sv`: Xasterdot8, a packed signed INT8 dot product
  (custom-0 encoding; see [`docs/phase8.md`](../../docs/phase8.md)).

The wrapper exposes PicoRV32's valid/ready native memory interface. This keeps
the CPU dependency replaceable while the Aster bus, caches, coherence and
peripherals evolve.

The wrapper also exposes a one-cycle retirement event and the retiring
PC/opcode from upstream RVFI, excluding trapping instructions. Build flows
define `RISCV_FORMAL` to expose those ports, but never enable the separate
`FORMAL` assumptions. Vendored source remains unmodified. `make retirement`
checks the exact observed sequence under memory stalls and across traps/reset.
