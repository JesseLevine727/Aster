# Aster architecture specification

Status: Phase 1 closeout; Phase 2–4 implementations under verification, 2026-09-12

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
asynchronous memory model for fast bring-up tests; the native bus contract is
unchanged apart from the explicit one-cycle memory response in BRAM mode.

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
| Memory reads | Combinational in simulation; one-cycle synchronous response in FPGA BRAM mode |
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
memory and one-cycle synchronous FPGA BRAM memory. `ENABLE_L1=0` is retained as
an uncached comparison configuration.

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
| Performance counters | `0x2000_3000` | `0x2000_4000` | 4 KiB | RW | Cycles, instruction proxy and traffic counters |
| DMA | `0x3000_0000` | `0x3000_1000` | 4 KiB | RW | Reserved for Phase 7 |
| INT8 NPU | `0x4000_0000` | `0x4000_1000` | 4 KiB | RW | Reserved for Phase 9 |

### UART registers

The simulation UART is intentionally minimal.

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x00` | `TXDATA` | W | Low byte is emitted when written |
| `0x04` | `STATUS` | R | Bit 0 is always `1` (`TX ready`) |

There is no baud-rate generator in simulation. The UART testbench observes a
one-cycle `tx_valid` pulse and prints `tx_data`.

### Performance-counter registers

The performance block exposes 64-bit little-endian counters as adjacent
32-bit low/high registers. A write of `1` to `CONTROL` offset `0x38` clears
all counters; byte lane 0 must be enabled. The register contract is:

| Offset | Name | Meaning |
| ---: | --- | --- |
| `0x00/0x04` | `CYCLES_LO/HI` | enabled clock cycles |
| `0x08/0x0c` | `RETIRED_LO/HI` | accepted instruction-fetch transactions (current PicoRV32 proxy) |
| `0x10/0x14` | `MEM_TXN_LO/HI` | accepted native core memory transactions |
| `0x18/0x1c` | `CACHE_ACCESS_LO/HI` | accepted cacheable CPU transactions |
| `0x20/0x24` | `CACHE_MISS_LO/HI` | cache-line lookup misses |
| `0x28/0x2c` | `DMA_BYTES_LO/HI` | DMA byte events; zero until Phase 7 |
| `0x30/0x34` | `ACCEL_CYCLES_LO/HI` | accelerator-active cycles; zero until Phase 9 |
| `0x38` | `CONTROL` | write bit 0 to clear |

The current core has no architectural retire output, so `RETIRED` is defined
as an instruction fetch accepted by the PicoRV32 native bus. This is an
explicit Phase 3 proxy, not a claim of precise retirement accounting. The
runtime reads each 64-bit value high/low/high to avoid a torn sample. The
snapshot itself is taken through MMIO, so its readout overhead is included in
the reported cycle and transaction counts. Cache access and miss events are
generated by the Phase 4 L1 pair; DMA and accelerator counters remain zero
until those subsystems are connected.

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

The PYNQ-Z1 implementation is intentionally programmable-logic-only. The
onboard USB-UART is attached to Zynq PS MIO, so this baseline exposes PL TX on
Pmod JA[0] and requires a 3.3 V USB-UART adapter. The target is constrained
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

The remaining closeout requires strict record validation, accurate event
semantics and a common snapshot interval, counter corner-case tests,
revision/configuration provenance, reproducible comparisons and complete
board-facing output. The initial benchmark alone does not satisfy these.

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
