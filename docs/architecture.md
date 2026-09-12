# Aster architecture specification

Status: Phases 1/3/4 verified; Phase 2 physical closeout pending, 2026-09-12

This document is the executable contract for the first bring-up slice. It
separates decisions that are fixed for the minimal system from features that
remain deliberately open for Aster v1.

## Purpose and scope

Aster is an open, heterogeneous RISC-V SoC intended to measure compute
placement trade-offs across scalar CPUs, multiple CPUs, ISA-level operations
and dedicated accelerators. The initial implementation is a small bare-metal
computer that lets the rest of the platform be developed incrementally.

The Phase 0/1 baseline contains:

- one in-order RV32IM core supplied by the pinned PicoRV32 implementation;
- 64 KiB instruction/data ROM image space;
- 64 KiB byte-writeable RAM;
- a simulation UART;
- combinational instruction and data reads with stores committed on `clk`;
- a freestanding C firmware image that prints `Hello from Aster`;
- runtime copying initialized data, clearing BSS and reserving a 4 KiB stack.

The Phase 2 PYNQ-Z1 target adds a synchronous-read BRAM configuration of the
same SoC, a global MMCM/BUFG clock path (31.25 MHz core clock from the board's
125 MHz oscillator), a core-domain reset synchronizer and a board-facing
115200-baud UART transmitter on Pmod JA[0]. The simulator continues to use the
asynchronous memory model by default. `MEMORY_WAIT_CYCLES` specifies total
additional backing-memory wait cycles: default 0 for asynchronous reads and
1 for synchronous BRAM. Values through 1024 are supported, with at least 1
required for BRAM; UART/MMIO timing is unchanged. A held lower request is
acknowledged once after that many wait cycles, then the delay counter resets.

The planned v1 target remains two RV32IM cores, a shared L2, coherence, DMA,
custom packed INT8 instructions, an INT8 matrix accelerator, interrupts and
timers. Phase 3 implements the performance-counter MMIO block and first
AsterBench workload. Phase 4 now adds the first private I/D L1 pair; L2,
coherence, DMA and accelerator event sources remain disconnected until their
roadmap phases.

## Current block diagram

```text
                  +--------------------+
                  |   picorv32 RV32IM  |
                  |  reset PC = 0x0000 |
                  +---------+----------+
                            |
                     native valid/ready
                            v
                 +------------------------+
                 | private L1 front end   |
                 | direct-mapped I$ / D$  |
                 +-----------+------------+
                             |
                    refill / bypass traffic
                             v
                 +------------------------+
                 | address decoder        |
                 +---+-----------+--------+
                     |           |
                     v           v
                 +------+     +----------+
                 | ROM  |     | RAM/MMIO |
                 |64 KiB|     |64 KiB +  |
                 +------+     | UART/perf|
                              +----------+
```

The core still uses PicoRV32's single-outstanding-transfer valid/ready
protocol. In the Phase 4 default configuration the front end selects the I$
for instruction fetches and the D$ for RAM data requests. The decoder sees
cache-line refills and uncached traffic, while UART and performance-counter
MMIO always bypasses the caches.

## Architectural conventions

| Property | Phase 0/1 decision |
| --- | --- |
| ISA | RV32IM, little-endian; compressed instructions are disabled |
| ABI | `ilp32` bare-metal ABI |
| Privilege | Machine-mode-style bare-metal execution; no privilege transitions yet |
| Reset | Active-low synchronous reset; PC resets to `0x0000_0000` |
| Instruction width | 32-bit instructions; compressed instructions disabled in firmware |
| Data width | 32-bit datapath; byte strobes for stores |
| Alignment | Word instructions and naturally aligned half/word accesses are expected |
| Memory reads | Async or synchronous BRAM; configurable total waits, default 0 / 1 respectively |
| Memory writes | Sampled on the rising edge when `data_we` is asserted |
| Unmapped reads | Return zero |
| Unmapped/unsupported operations | PicoRV32 exposes `trap`; no Aster trap handler yet |
| Firmware image | Flat binary converted to one little-endian 32-bit hex word per line |

## Phase 4 L1 cache contract

The default `aster_minimal` configuration has one private instruction cache and
one private data cache in front of the decoder. Each cache is direct mapped
with 16 lines of 4 words (16 bytes per line, 256 bytes of data capacity). For
this geometry, address bits `[3:0]` select the byte within a line, `[7:4]`
select the line, and `[31:8]` form the tag.

- The I$ caches instruction requests in the ROM address space.
- RAM instruction fetches bypass the I$ and use RAM, including after a code
  update. D$ stores are write-through; no stale RAM instruction copy exists.
- The D$ caches data requests in the main RAM window only.
- UART, performance-counter and all other MMIO requests bypass the caches.
- Cacheable loads are read-allocate and refill one 32-bit word at a time.
- Stores are write-through and no-write-allocate; byte strobes are preserved
  at the lower level and update a resident line on a hit.
- Reset invalidates every line. There is no flush instruction, coherence
  protocol or write-back state in this phase.

The cache controller has no queue because PicoRV32 holds a single request
until completion. Its lower-level request remains asserted until the decoder
acknowledges it, so the same contract works with asynchronous simulation
memory and delayed synchronous FPGA BRAM memory. `ENABLE_L1=0` is retained as
an uncached comparison configuration. `L1_LINE_WORDS` and `L1_LINE_COUNT`
control both private caches; each capacity is `4 * words * lines` bytes,
excluding tag/valid storage. The capture tool accepts powers of two from 2
through 1024 in each dimension. The unit matrix samples every index/offset
width in that range, including 1024×1024; this does not imply every geometry
fits the PYNQ-Z1. FPGA targets retain the default 4×16 geometry and one wait
cycle; simulation Make overrides do not silently reconfigure those targets.

Reset clears a packed valid bitmap, not the data arrays. A partially filled
line cannot be used after reset. The request/response contract requires the
CPU to hold valid/address/data/strobes until ready; back-to-back accepted
transactions may have identical fields. A store miss neither allocates nor
evicts an existing conflicting line. Unaccepted lower read data is ignored.

## Memory map

All ranges are inclusive at the start and exclusive at the end. The decoder
enforces the actual ROM/RAM bounds below; individual peripherals own their
register offsets. ROM stores and unmapped stores are ignored; unmapped data
reads return zero and bypass caches. Only ROM and RAM supply instruction data:
an MMIO/unmapped instruction fetch returns zero and the core traps. RAM has a
reserved 4 KiB stack at the top; the linker rejects data/BSS overlap with it.

| Region | Start | End | Size | Access | Purpose |
| --- | ---: | ---: | ---: | --- | --- |
| Boot ROM | `0x0000_0000` | `0x0001_0000` | 64 KiB | RX | Firmware and read-only data |
| Main RAM | `0x1000_0000` | `0x1001_0000` | 64 KiB | RWX | Data, stack and uncached instruction execution |
| UART | `0x2000_0000` | `0x2000_1000` | 4 KiB | RW | Console and bring-up status |
| Timer | `0x2000_1000` | `0x2000_2000` | 4 KiB | RW | Reserved for Phase 1+ |
| Interrupt controller | `0x2000_2000` | `0x2000_3000` | 4 KiB | RW | Reserved for Phase 5+ |
| Performance counters | `0x2000_3000` | `0x2000_4000` | 4 KiB | RW | Cycles, RVFI retirement and traffic counters |
| DMA | `0x3000_0000` | `0x3000_1000` | 4 KiB | RW | Reserved for Phase 7 |
| INT8 NPU | `0x4000_0000` | `0x4000_1000` | 4 KiB | RW | Reserved for Phase 9 |

### UART registers

The simulation UART is intentionally minimal.

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x00` | `TXDATA` | W | Low byte is emitted when written |
| `0x04` | `STATUS` | R | Bit 0 indicates available TX capacity |

The register block has a one-byte ready/valid output slot. A byte-lane-0
TXDATA write stalls until capacity exists; upper-byte-only writes have no
effect. Data/valid remain stable under backpressure. Event-level simulation
drains the slot immediately; board-facing tests exercise the real 8-N-1 PHY
and its FIFO. A full FIFO never silently drops a CPU write.

### Performance-counter registers

The performance block exposes 64-bit little-endian counters as adjacent
32-bit low/high registers. Reset clears and disables counting. CONTROL commands
are recognized by their low byte only when write byte lane 0 is enabled:
`1` clears and starts, `2` freezes, `4` resumes without clearing. Other values
do nothing. Reads return bit 0 = running. The register contract is:

| Offset | Name | Meaning |
| ---: | --- | --- |
| `0x00/0x04` | `CYCLES_LO/HI` | enabled clock cycles |
| `0x08/0x0c` | `RETIRED_LO/HI` | non-trapping instructions reported by PicoRV32 RVFI |
| `0x10/0x14` | `MEM_TXN_LO/HI` | accepted native core memory transactions |
| `0x18/0x1c` | `CACHE_ACCESS_LO/HI` | accepted cacheable CPU transactions |
| `0x20/0x24` | `CACHE_MISS_LO/HI` | cache-line lookup misses |
| `0x28/0x2c` | `DMA_BYTES_LO/HI` | DMA byte events; zero until Phase 7 |
| `0x30/0x34` | `ACCEL_CYCLES_LO/HI` | accelerator-active cycles; zero until Phase 9 |
| `0x38` | `CONTROL` | clear/start = 1, freeze = 2, resume = 4; read running bit |
| `0x40/0x44` | `BACKING_LO/HI` | accepted lower-memory ROM/RAM transactions, including refills |
| `0x48` | `ABI` | counter/record ABI = 2 |
| `0x4c` | `CLOCK_HZ` | configured nominal fabric frequency |
| `0x50` | `FLAGS` | bit 0 L1 enabled, bit 1 synchronous memory |
| `0x54/0x58` | `LINE_WORDS/LINE_COUNT` | geometry of each private L1 |
| `0x5c` | `MEMORY_WAIT` | added backing-memory wait cycles (0 async, 1 sync) |

The wrapper consumes the pinned upstream core's existing `rvfi_valid` and
`rvfi_trap` outputs, enabled by `RISCV_FORMAL` in both toolchains. This does not
enable `FORMAL` assumptions and does not edit the vendored implementation.
`instr_retired = resetn && rvfi_valid && !rvfi_trap` excludes faulting instructions
and repeated halted trap records. RVFI reports completion at the next instruction
launch; this is not a fetch or the upstream early instruction-launch CSR proxy.
`make retirement` checks an exact PC/opcode sequence with skipped prefetched
instructions, long multiply/divide, stalled memory, six traps and two resets.

All eight counters use one hardware clock interval. Start/stop/resume command
edges themselves exclude events; counting occurs strictly between a start
and freeze acceptance. The runtime freezes before reading **any** counter.
All counters remain stable during validation, metadata reads and UART output,
so the snapshot is common and cannot tear. The window includes the boundary
instructions/pipeline effects and setup of the freeze marker, not just the
copy loop's disassembly. There is no claim that instruction retirement and
cache lookup events occur at the same pipeline stage.

Native transactions count CPU instruction/data/MMIO acceptances; backing
transactions count only ROM/RAM lower-bus acceptances, including cache refills
and write-through stores, not MMIO. Cache events count lookup accesses/misses
including no-write-allocate store misses. All counters wrap modulo 2^64.
DMA byte and accelerator-active inputs are hardwired zero in this SoC.

## Core interface contract

The core exposes one unified native memory interface. `mem_instr` identifies
instruction fetches, allowing a future cache front end to split instruction and
data traffic even though this single-issue core has only one outstanding
request:

```text
request: mem_valid, mem_instr, mem_addr, mem_wdata, mem_wstrb
response: mem_ready, mem_rdata
```

The core holds `mem_valid` and all request fields until `mem_ready` is high.
Reads have `mem_wstrb == 0`; stores use `mem_wstrb[n]` to control byte `n` of
the addressed 32-bit word. The decoder routes writes to RAM or UART and
ignores writes to reserved windows.

## Decisions still open

These items must be resolved before the corresponding roadmap phase, not
silently assumed by Phase 0:

- shared L2 organization and refill protocol;
- coherence protocol and atomic-memory implementation;
- system interconnect transaction format and arbitration;
- custom instruction encoding and toolchain support;
- NPU register/DMA interface, tiling format and saturation rules;
- interrupt priority and timer semantics;
- SKY130 macro strategy and SRAM availability.

## Phase 2 FPGA contract

The standalone PYNQ-Z1 implementation is programmable-logic-only. The onboard
USB-UART is attached to Zynq PS MIO, so this variant exposes PL TX on Pmod JA[0]
for a 3.3 V USB-UART adapter. The additional Linux overlay connects the Zynq
ARM host to an AXI-Lite loader/capture bridge. Firmware is loaded into a
dedicated boot-ROM programming port only while Aster is reset; CPU ROM writes
remain forbidden. UART TX is decoded by an FPGA serial receiver and drained
by Linux over AXI/SSH, with receive credits preventing loss during host pauses.
It is an internal serial loopback, not an external-pin loopback. Both variants
are constrained
for the `xc7z020clg400-1` device and must pass Vivado DRC, placement, routing,
and post-route timing before a bitstream is considered valid. See
[`fpga/pynq_z1/README.md`](../fpga/pynq_z1/README.md) for the pinout and
programming procedure.

## Phase 0 exit criteria

Phase 0 is complete when:

1. the repository layout and ownership boundaries are documented;
2. the toolchain check is reproducible with `make tools`;
3. the memory map and current-vs-planned architecture are explicit;
4. `make smoke` passes a Verilator test;
5. `make firmware` builds and disassembles the bare-metal RISC-V image; and
6. `make hello` observes `Hello from Aster` through the simulated UART.

## Phase 1/2 exit criteria

Phase 1 requires the README's compiled Hello exit plus the closeout tests in
`make phase1-matrix`: generated integer/M cases, memory permissions/bounds,
RAM execution, cold and warm C startup, stack use and expected traps across
cache and memory timing configurations. Host tests enforce the linker limits.

Phase 2 requires real Aster firmware execution and communication on PYNQ-Z1,
as stated in the README. Board-facing simulation and a timing/DRC-clean routed
bitstream are prerequisites, not substitutes for physical evidence. Board
loading and validation will use SSH/PYNQ Linux, per the user's board workflow.
The earlier bitstream report does not validate subsequent RTL revisions.

## Phase 3 exit criteria

Phase 3 is complete when (in addition to the closeout requirements below):

1. the performance-counter register map and event semantics are documented;
2. the RAM-backed AsterBench firmware builds through the bare-metal runtime;
3. the benchmark emits a complete deterministic machine-readable record; and
4. `make bench` and the full `make check` regression pass.

Measurement closeout requires strict record validation, accurate event
semantics and a common snapshot interval, counter corner-case tests,
revision/configuration provenance, reproducible comparisons and complete
board-facing output. These are verified by the Phase 3 closeout; the initial
benchmark alone did not satisfy them. See the retained Phase 3 evidence.

## Phase 4 exit criteria

Phase 4 is complete when:

1. separate L1 I/D caches are integrated behind the PicoRV32 native bus;
2. cacheable RAM/ROM traffic, write-through stores and uncached MMIO are
   explicitly defined;
3. unit verification covers refill, hits, byte writes, eviction,
   no-write-allocate stores and bypass traffic;
4. Hello and AsterBench pass with caches enabled and report non-zero cache
   access/miss counters; and
5. `make check` remains green with the Phase 4 cache regression included.

The README also requires no-cache/cache comparisons, working-set sweeps,
sequential/random access and cache-size sensitivity. Closeout additionally
requires randomized reference checks, protocol assertions, stalled/reset
transactions and supported geometries. See `docs/phase-closeout.md` for the
evidence and outstanding work; the basic unit regression is insufficient alone.
