# Interconnect RTL

The v1 interconnect admits one memory transaction at a time for the whole SoC;
see the [v2 plan](../../docs/phase17-plus.md#1-what-limits-v1) for why v2
replaces it.

- `aster_atomic_fabric.sv` (Phase 6): the serialized, permission-checked CPU
  transaction engine for both harts. It checks each request against the
  memory/MMIO permit list and executes RV32A atomics as one locked
  read-modify-write.
- `aster_device_arbiter.sv` (Phase 9): round-robin arbitration between the CPU
  fabric, DMA and NPU in front of the coherent cache. A device keeps its turn
  until its accepted transaction completes. Since Phase 17-A2 it also marks
  DMA-owned transactions (`m_dma`) so DMA byte counters exclude NPU traffic.
- `aster_dma_arbiter.sv` (Phase 7): CPU/DMA arbitration used when the NPU is
  not elaborated.
- `aster_arbiter2.sv` (Phase 5): the two-hart arbiter of the legacy
  non-coherent `aster_shared_fabric`.

Tests: `make atomic-fabric`, `make device-arbiter`, `make arbiter`.
