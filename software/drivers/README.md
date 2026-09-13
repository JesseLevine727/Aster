# Bare-metal drivers

The Phase 7 [`aster_dma.h`](aster_dma.h) / [`aster_dma.c`](aster_dma.c) driver
controls the actual coherent hardware engine at `0x30000000`. Build it with
RV32IMA/ilp32, the RAM-backed multicore runtime and `-Isoftware/drivers`.
The [DMA ABI/ownership contract](../../docs/phase7.md) specifies its registers
and shared-RAM-only range. The minimal and noncoherent legacy SoCs do not
gain DMA merely by linking this driver.

`aster_dma_copy(destination, source, bytes, maximum_polls)` submits a
descriptor and polls for completion. `aster_dma_submit` returns `PENDING` or
an immediate completion/error; use `poll`/`wait` for asynchronous submission.
No path calls CPU `memcpy`. Source and destination may have any byte alignment;
nonzero overlapping ranges are rejected. Length zero completes without touching
either pointer. The hardware validates the entire descriptor before payload I/O.

Only hart 0 may submit, acknowledge or abort. The caller serializes control
operations and owns both buffers until completion. Use explicit release/acquire
publication before another hart consumes the result. The driver uses compiler
barriers and `fence iorw,iorw` around descriptor submission and completed data;
hardware snoops dirty CPU lines, so there is no manual cache-flush requirement.

Timeouts are **poll counts**, not wall-clock deadlines. A timeout does not
cancel a transfer or release either buffer. `aster_dma_abort_and_wait` requests
a cooperative drain: an already offered write may still finish, and
`aster_dma_bytes_done` then reports the completed prefix. If abort also times
out, the requester may still be busy; do not reuse buffers or pretend that
resetting a software flag repairs a stalled memory responder. An abort that
arrives after successful completion leaves that success intact.

`aster_dma_acknowledge` clears terminal/error/rejection flags while idle.
A new accepted START also clears them; descriptors and last-job byte/cycle
accounting remain available after ACK. `aster_dma_job_cycles` uses high/low/
high reads to avoid torn 64-bit carry. Neither job cycles nor polling imply
CPU offload or define the end-to-end AsterBench measurement window.

`make dma-runtime` compiles and runs the driver on the actual Verilated cores;
its integration acceptance is tracked in [Phase 7](../../docs/phase7.md).
UART, timer and interrupt driver expansion remain separate work.

The Phase 9 [`aster_npu.h`](aster_npu.h) / [`aster_npu.c`](aster_npu.c) driver
controls the ABI-1 INT8 GEMM engine at `0x40000000`. It validates RV32 shared
RAM ranges, byte strides, non-overlap and hart-0 ownership before publishing a
descriptor with `fence iorw,iorw`. `aster_npu_poll`/`wait`/`abort_and_wait` use
poll-count semantics and never release buffers after a timeout. The scalar
`aster_npu_scalar_gemm` routine is the software/reference boundary and writes
unaligned C payloads by bytes, matching the hardware layout.
