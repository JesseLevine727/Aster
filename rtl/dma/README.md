# DMA RTL

`aster_dma_engine.sv` (Phase 7) is a memory-to-memory copy engine at
`0x3000_0000`, programmed by hart 0 with a source, destination and length and
started, aborted or acknowledged through `COMMAND`; completion is polled and is
also an interrupt source. The register map is frozen in
[`docs/v1.md`](../../docs/v1.md#55-dma-0x3000_0000-abi-1).

The engine runs one job at a time as alternating read and write transactions
through the coherent device path. It moves a 32-bit word when the source,
destination and remaining length are all word-aligned, and one byte otherwise.
Source and destination must lie in shared RAM (`0x1000_0000–0x1000_8000`).

Contract: [`docs/phase7.md`](../../docs/phase7.md). Driver:
[`software/drivers/aster_dma.h`](../../software/drivers/aster_dma.h). Tests:
`make dma-engine`, `make dma-counters`, `make dma-runtime`. Arbitration with the
CPU and NPU is in [`../interconnect`](../interconnect/README.md).
