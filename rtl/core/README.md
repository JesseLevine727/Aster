# CPU integration

`aster_picorv32.sv` is the Aster-owned integration boundary around the pinned
PicoRV32 source in `vendor/picorv32/`. The initial configuration is RV32IM:
multiply and divide are enabled, compressed instructions are disabled, and
PCPI is enabled for the future packed INT8 DOT8 instruction.

The wrapper exposes PicoRV32's valid/ready native memory interface. This keeps
the CPU dependency replaceable while the Aster bus, caches, coherence and
peripherals evolve.

The wrapper also exposes a one-cycle retirement event and the retiring
PC/opcode from upstream RVFI, excluding trapping instructions. Build flows
define `RISCV_FORMAL` to expose those ports, but never enable the separate
`FORMAL` assumptions. Vendored source remains unmodified. `make retirement`
checks the exact observed sequence under memory stalls and across traps/reset.
