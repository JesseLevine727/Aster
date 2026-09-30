# Accelerator RTL

Phase 9 owns the INT8 GEMM accelerator here. The implementation follows the
[Phase 9 contract](../../docs/phase9.md): processing element → parameterized
array → 4×4 tile engine → bounded shared-RAM master/control interface.

Implemented and verified modules:

- `aster_int8_pe.sv`: signed INT8 × INT8 plus 32-bit modulo accumulator;
- `aster_int8_array.sv`: `ROWS`×`COLS` PE tile accumulator with partial-edge masks
  (4×4 by default; 2×2 and 8×8 since [v1.2](../../docs/npu-geometry.md));
- `aster_npu_engine.sv`: descriptor validation, tile/K sequencing, RAM byte
  loads, exact-lane output stores, and lifecycle/cycle counters;
- `aster_npu_regs.sv`: ABI-1 descriptor registers, control/status boundary, and
  engine accounting readback;

Completed integration:

- SoC/interconnect integration, the RAM-backed C driver, AsterBench v7, and
  guarded PYNQ Linux/PCAP execution are accepted in the Phase 9 closeout.

These modules must remain independently testable without PicoRV32 or PYNQ.

The v1 engine is functionally verified but starved: each operand byte is a
separate 32-bit read, each 32-bit result is four byte stores, and operand
addresses come from combinational 32×32 multiplies, so the array computes in
under 2% of its busy cycles on the measured workloads (see the
[v2 plan](../../docs/phase17-plus.md#1-what-limits-v1)). Phase 19 redesigns the
data path.
