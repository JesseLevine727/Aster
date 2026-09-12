# CPU integration

`aster_picorv32.sv` is the Aster-owned integration boundary around the pinned
PicoRV32 source in `vendor/picorv32/`. The initial configuration is RV32IM:
multiply and divide are enabled, compressed instructions are disabled, and
PCPI is enabled for the future packed INT8 DOT8 instruction.

The wrapper exposes PicoRV32's valid/ready native memory interface. This keeps
the CPU dependency replaceable while the Aster bus, caches, coherence and
peripherals evolve.
