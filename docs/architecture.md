# Aster architecture specification

Status: Phase 2 baseline, 2026-09-11

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
- a RISC-V assembly firmware image that prints `Hello from Aster`.

The Phase 2 PYNQ-Z1 target adds a synchronous-read BRAM configuration of the
same SoC, a global MMCM/BUFG clock path (31.25 MHz core clock from the board's
125 MHz oscillator), a core-domain reset synchronizer and a board-facing
115200-baud UART transmitter on Pmod JA[0]. The simulator continues to use the
asynchronous memory model for fast bring-up tests; the native bus contract is
unchanged apart from the explicit one-cycle memory response in BRAM mode.

The planned v1 target remains two RV32IM cores, private L1 caches, a shared L2,
coherence, DMA, custom packed INT8 instructions, an INT8 matrix accelerator,
interrupts, timers and performance counters. None of those are implied to be
implemented by this baseline.

## Current block diagram

```text
                  +--------------------+
                  |   picorv32 RV32IM  |
                  |  reset PC = 0x0000 |
                  +-----+---------+----+
                        |         |
                  instr|         |data
                        v         v
                 +----------+  +------------------+
                 | ROM      |  | address decoder  |
                 | 64 KiB   |  +--+-------+-------+
                 +----------+     |       |
                                  v       v
                              +------+ +------+
                              | RAM  | | UART |
                              |64 KiB | | MMIO |
                              +------+ +------+
```

The current bus is intentionally a direct core-to-peripheral connection. It
uses PicoRV32's single-outstanding-transfer valid/ready protocol; the Phase 1
decoder acknowledges every transfer without wait states. The future
interconnect and caches will replace this wiring while preserving the address
and transaction semantics where possible.

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

## Memory map

All ranges are inclusive at the start and exclusive at the end. The decoder
uses the high-level windows below; individual peripherals own their register
offsets.

| Region | Start | End | Size | Access | Purpose |
| --- | ---: | ---: | ---: | --- | --- |
| Boot ROM | `0x0000_0000` | `0x0001_0000` | 64 KiB | RX | Firmware and read-only data |
| Main RAM | `0x1000_0000` | `0x1001_0000` | 64 KiB | RWX | Scratch/data/stack in later firmware |
| UART | `0x2000_0000` | `0x2000_1000` | 4 KiB | RW | Console and bring-up status |
| Timer | `0x2000_1000` | `0x2000_2000` | 4 KiB | RW | Reserved for Phase 1+ |
| Interrupt controller | `0x2000_2000` | `0x2000_3000` | 4 KiB | RW | Reserved for Phase 5+ |
| Performance counters | `0x2000_3000` | `0x2000_4000` | 4 KiB | RW | Reserved for Phase 3+ |
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

- exact L1/L2 organization and refill protocol;
- coherence protocol and atomic-memory implementation;
- system interconnect transaction format and arbitration;
- custom instruction encoding and toolchain support;
- NPU register/DMA interface, tiling format and saturation rules;
- interrupt priority and timer semantics;
- FPGA clock/reset and UART pin implementation;
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

Phase 1 is complete when the PicoRV32 RV32IM image passes the directed test
and C runtime simulation. Phase 2 is complete when the board-facing UART
simulation passes, the Vivado flow generates a bitstream with no DRC errors or
unrouted nets and timing reports no failing endpoints. Physical board
observation remains a hardware-validation step and is recorded separately.
