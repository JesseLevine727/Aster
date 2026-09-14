# Accelerator RTL

Phase 9 owns the INT8 GEMM accelerator here. The implementation follows the
[Phase 9 contract](../../docs/phase9.md): processing element → parameterized
array → 4×4 tile engine → bounded shared-RAM master/control interface.

Implemented and verified modules:

- `aster_int8_pe.sv`: signed INT8 × INT8 plus 32-bit modulo accumulator;
- `aster_int8_array.sv`: sixteen PE tile accumulator with partial-edge masks;
- `aster_npu_engine.sv`: descriptor validation, tile/K sequencing, RAM byte
  loads, exact-lane output stores, and lifecycle/cycle counters;
- `aster_npu_regs.sv`: ABI-1 descriptor registers, control/status boundary, and
  engine accounting readback;

Completed integration:

- SoC/interconnect integration, the RAM-backed C driver, AsterBench v7, and
  guarded PYNQ Linux/PCAP execution are accepted in the Phase 9 closeout.

These modules must remain independently testable without PicoRV32 or PYNQ.
The optional 8×8 configuration is deferred until the 4×4 exit gate passes.
