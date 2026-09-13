# AsterBench v5 DMA experiment contract

Status: design for the next implementation milestone; no benchmark or board
results claimed here. The [Phase 7 contract](phase7.md) and README remain the
acceptance authority. Preserve all v2/v3/v4 record and capture interfaces.

## Paired experiment

One RV32IMA image contains both an optimized CPU copy kernel and the real DMA
driver. The two methods use the **same physical source/destination buffers**,
size, alignment, input seed and cache preparation within a job. Hart 1 stays
reset for this latency experiment; DMA polling is primary CPU work. The
functional runtime separately tests peer work/publication and selective reset.

CPU baseline: a non-volatile, non-inlined 32-bit word-copy kernel, unrolled for
16-byte chunks, with byte alignment prefix/tail and a byte path when source
and destination cannot become simultaneously word-aligned. No unaligned word
accesses, undefined strict-aliasing trick, library substitution or volatile
byte-only baseline. Retain and inspect actual compiler flags/disassembly.
This is a documented freestanding kernel, not a claim to be the fastest libc
implementation for every alignment.

DMA baseline: `aster_dma_copy`, including its ABI/owner/status reads, descriptor
programming, submission fences, bounded polling, completion and ordering.
There is no CPU copy fallback. Reject failed or incomplete jobs, not slow jobs.

Before *each* method, acknowledge the idle DMA and deterministically rewrite
the complete source and destination buffers (including guards), outside the
measurement window. Report this as `prepared_reinitialize`, **not cold cache**.
Record actual buffer addresses, offsets, allocation sizes and cache geometry.
Keep the source base at `0x10001000` and destination base at `0x10005080` for
the entire size sweep, using a benchmark-only linker layout. The fixed
`0x4080` separation is eight lines modulo the default sixteen 16-byte cache
lines; increasing size must not accidentally change the relative cache index
by moving the second allocation. Each declared allocation is rounded to 64
bytes and includes at least 64-byte prefix/tail guards. Unused address gaps
are not payload. Other cache geometries still have their own recorded index
effects; fixed placement is not a claim to remove all cache behavior.

The common window starts before method dispatch/copy/descriptor setup and
freezes after completion and a memory/I/O fence. It excludes initialization,
verification, output printing and global stop/flush. The command edges themselves
are excluded by the existing common-window ABI. All 28 CPU counters and 14
DMA counters freeze together. Actual hardware JOB_CYCLES/BYTES_DONE/STATUS are
retained as raw diagnostics; after a CPU method, those per-job registers can
still describe a previous DMA job because ACK only clears flags. They are not
substitutes for the measured window.

Default **four jobs**, alternating method order: CPU/DMA in jobs 1 and 3,
DMA/CPU in jobs 2 and 4. Both methods of one job use the same seed. Each capture
includes two warm boots, and the fixed study includes an independently rebuilt
repeat. Keep every per-boot/job/method result rather than averaging away order
effects or negative speedups.

## Fixed study and correctness

Sizes: `0,1,2,3,4,7,8,15,16,31,32,63,64,127,128,255,256,511,512,1024,2048,4096,8192`.
Alignments (source/destination byte offsets after a 64-byte guard): `0/0`,
`1/1`, `1/2`. Cross cache off/on, two present harts but one active worker,
4-word/16-line geometry and physical synchronous RAM with one wait cycle at
31.25 MHz. The separate functional unit/runtime matrices cover all 16 alignment
pairs, permissions, overlap, aborts and lifecycle, not just these performance
representatives. Additional geometries/timings are sensitivity tests, not
substitutes for a complete fixed study.

Each method verifies every source byte, destination byte and guard. Its raw
serial record contains the **complete destination allocation**, not merely a
checksum or selected elements. The host independently reconstructs the expected
bytes from seed, length and offsets. Actual stopped RAM retains both buffers
and all per-job counter/metadata snapshots; the simulation keeps a full-RAM
oracle from architectural CPU stores and actual DMA payload acceptance.

During a CPU measurement, DMA events must be zero and the CPU's destination
stores must implement the copy. During a DMA measurement, the CPU must not
store payload destination bytes; actual DMA stores must form the exact ordered
byte range, preserve guards and match source data. Snoop/dirty-drain traffic
is explicitly distinct from payload. Check all event wiring against raw
request/acceptance/retirement/cache observations, then compare RAM, serial and
counter captures. A host-created output buffer is not evidence of RISC-V DMA.

## Versioning, provenance and interpretation

Use a strict `ASTERBENCH,version=5,name=dma_memcpy` serial format with exact
fields, canonical unsigned numbers/hex, method/order/window/preparation labels,
job and seed identity, clock/geometry/addresses, raw driver/engine status,
all 42 counters and full output hex. Reject unknown/duplicate/missing fields,
truncation, reordered or repeated jobs, impossible event counts, wrong buffers,
wrong output and legacy-version substitution. Freeze the precise field schema
in the parser/firmware together and mutation-test both record and capture gates.

Captures retain clean Git revision/source inventory, exact build invocation,
compiler/Verilator identity, ELF/symbol/load-image audit, ROM, raw UART, full
RAM, per-hart/DMA event observations, boot/stop history and file hashes. The
physical capture additionally binds the actual bitstream/HWH/reset-netlist/
routed reports, loaded-image/clock/ABI preflight, real serial channel and final
STOPPED state. Keep simulation references and physical observations distinct.

Primary comparison is CPU common-window cycles / DMA common-window cycles,
paired by seed, job, order, boot and configuration. Values below one are DMA
slowdowns. Report first observed win, subsequent reversals, order spread and
cache/alignment dependence. Convert cycles to latency with the verified clock;
nonzero-size bandwidth is payload bytes / that end-to-end latency. Zero-byte
bandwidth is undefined. Do not claim a universal crossover, CPU availability
while polling or a future accelerator's performance from this experiment.
