# AsterBench v5 DMA experiment contract

Status: the complete clean simulation and physical PYNQ studies pass all
144 captures / 288 warm boots / 1,152 paired jobs each, with exact physical
reference counters and independent UART/RAM/Git audits. Final immutable
packaging and full phase closeout are still pending.
The [Phase 7 contract](phase7.md) and README remain the
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
includes two warm boots. The fixed study independently rebuilds the 1 KiB case
for each of its six cache/alignment series, selected before running the study.
That is 138 main captures plus six fresh repeats: **144 captures, 288 boots,
1,152 paired jobs, 2,304 method records**. Keep every per-boot/job/method result
rather than averaging away order
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

## Development commands and evidence gates

`make dma-bench SYNC_MEMORY=1` builds an RV32IMA ELF/complete ROM and runs
the actual-core scoreboard for the default 64-byte aligned case, four balanced
jobs and two warm boots. Select `DMA_BYTES`, `DMA_ALIGNMENT` (`aligned`,
`same_offset`, `different_offset`), `DMA_JOBS`, `DMA_SEED`, `DMA_BOOTS`,
`DMA_UART_SEED` and the usual hart/cache/memory geometry knobs. RAM snapshots
use exclusive creation: use a fresh `BUILD_DIR` or `DMA_RAM_PREFIX` for a rerun.

`make linux-dma-bench` runs the same audited ELF through the AXI bridge and
actual TX/RX serial circuitry. Because UART can lag multiple measurement
windows, this scoreboard queues independent observations at each FREEZE, then
matches the arriving record to that window; it never substitutes the latest
live counters for an earlier measurement. `linux-dma-bench-cases` checks both
cache modes at zero/small/large sizes and all alignment policies;
`linux-dma-bench-baud` checks both cache modes at the physical 115,200 baud.
These are additional integration regressions, not physical capture packages.
With caches enabled, actual-core runs retain the instruction cache's minimum
two words/two lines; the standalone coherent-device cache's 1x1 and 1x1024
boundary tests do not make those instruction-cache configurations supported.

`python3 scripts/dma_results.py capture --output /new/path/b64.json` makes a
fresh isolated build and saves raw log, actual ELF/ROM/map/disassembly and both
64 KiB stopped-RAM snapshots beside a versioned envelope. The capture defaults
to clean source; `--allow-dirty` is explicitly development-only. Source and
toolchain identities must remain stable during execution. Failed runs retain
partial log/RAM and cannot overwrite an earlier attempt. The capture checker
audits all 108 metadata/counter words per method, complete final source and
destination allocations, actual CPU-kernel retirement, exclusive CPU/DMA
payload ownership and all 42 observed counters. Full-RAM architectural-store
equality and identical pre-method buffer preparation are additionally checked
on every run by the actual-core scoreboard.

`python3 scripts/dma_results.py audit /path/b64.json` reads artifact bytes and
the complete committed source inventory. It does not run executables or build
commands named in evidence. Its output preserves every paired ratio, latency
and bandwidth (undefined for zero bytes).

`python3 scripts/dma_study.py plan` prints the fixed plan;
`capture --output /new/study/directory` executes it against one clean revision,
sharing only the freshly built configuration-specific simulation models across
main cases. Each selected repeat gets a separate new build root. `audit
/path/study.json` rejects missing/extra/reordered cases, altered artifacts,
mixed source/toolchains and non-independent repeats, and recomputes every
summary. It reports any-pair and all-pairs first wins separately, subsequent
reversals, and a sustained win only through the **largest tested** size. It
does not interpolate a universal crossover or suppress negative ratios.

## Measured physical crossover

The fixed study uses clean `888c24b` firmware/hardware and committed `694ae0e`
physical collectors at **31.25 MHz**. All six freshly rebuilt 1 KiB repeats
produce identical physical records. CPU/DMA ratios below one mean DMA is
slower; polling still occupies the primary CPU, so these are latency results,
not a CPU-availability or overlap speedup claim.

| Alignment / caches | First all-pairs DMA win | Later sampled reversal | All sampled sizes win from, through 8 KiB | CPU cycles / DMA cycles at 8 KiB |
|---|---:|---:|---:|---:|
| Aligned / off | 63 B | 64 B | 127 B | 1.645768× |
| Aligned / on | 255 B | 256 B | 511 B | 1.103809× |
| Same byte offset / off | 31 B | None | 31 B | 1.653920× |
| Same byte offset / on | 128 B | None | 128 B | 1.109817× |
| Different byte offsets / off | 8 B | None | 8 B | 3.967321× |
| Different byte offsets / on | 63 B | None | 63 B | 1.622970× |

First-any-pair and first-all-pairs wins coincide in this study. These are
sampled boundaries, not claims about untested intermediate lengths. CPU word
copying and its 16-byte unroll change the relative cost at exact boundaries;
the 63/64 and 255/256 reversals are retained, not smoothed away. Cache-enabled
CPU copying makes DMA's relative advantage smaller. Setup, polling, coherent
forwarding/dirty maintenance and completion are all included as specified
above; the raw 64-byte aligned cache-off pilot, for example, records CPU
1,204 cycles versus DMA 1,443 cycles (0.834373×), a genuine DMA slowdown.
