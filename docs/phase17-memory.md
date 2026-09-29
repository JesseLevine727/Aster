# P17-D — memory capacity and die-area decision brief

Status: **decided on 29 September 2026 — Option B**: a 96 KiB host-loaded
unified SRAM in independent banks. Phase 17 had to freeze one memory/area point
before Phase 18 RTL ([plan](phase17-plus.md#memory-capacity-and-area-are-one-decision)).
This brief records the facts that constrained the choice.

## Facts

### SKY130 SRAM macros available in the PDK

The installed PDK (`8afc8346…`) ships three OpenRAM macros:

| Macro | Capacity | Area | Area per KiB | Liberty corners |
| --- | ---: | ---: | ---: | --- |
| `sky130_sram_2kbyte_1rw1r_32x512_8` (used in Phases 15–16) | 2 KiB | 0.285 mm² | 0.142 mm² | TT 1.8 V 25 °C only |
| `sky130_sram_1kbyte_1rw1r_32x256_8` | 1 KiB | 0.191 mm² | 0.191 mm² | TT only |
| `sram_1rw1r_32_256_8_sky130` | 1 KiB | 0.168 mm² | 0.168 mm² | SS/TT/FF at 1.8 V 25 °C, plus TT voltage/temperature points |

All three models are analytical rather than silicon-characterized: they launch
read data from the **falling** clock edge (about 0.4–0.65 ns after it) and give
minimum periods of 0.13–1.96 ns, which are not credible. None is characterized
at the standard-cell slow signoff corner (`ss`, 1.60 V, 100 °C). Any SKY130 timing
claim through an SRAM therefore needs a registered macro interface that fits in
half a cycle (≈4.5 ns at 100 MHz) plus an explicit derating or independent
characterization, and must say which it used.

### What Phase 16 learned

- 16 two-kilobyte macros (16 KiB ROM + 16 KiB RAM) occupied 4.55 mm² and routed
  on a 20 mm² die with 16.8% standard-cell utilization.
- The full 64 KiB ROM + 64 KiB RAM map (64 macros, ≈18.2 mm² of macro area) was
  attempted on 35 mm² and larger dies. Detailed placement failed until the
  macros were packed into a solid 8×8 block; that run then failed global
  routing on congestion after about 21 hours, and later attempts were
  abandoned for time. It was never shown
  to be impossible, but it was never completed either.
- The NPU and DMA can reach only the 32 KiB shared-RAM window
  (`0x1000_0000–0x1000_8000`), so the accelerator working set is bounded by that
  window in v1.

### Workload footprints (v1 firmware)

Bytes from `riscv32-unknown-elf-size` on the current builds; ROM holds code and
read-only data, RAM holds `.bss` (stacks live in the per-hart private windows).

| Workload | ROM (code + rodata) | RAM (`.bss`) | Notes |
| --- | ---: | ---: | --- |
| Reduction | 3.1–3.3 KB | 4.1 KB | |
| Conv2D engines | 5.6–6.0 KB | 23.8 KB | im2col matrix in shared RAM |
| Streaming ECG | 9.0 KB | 0.4 KB | |
| CIFAR-10 CNN | 29.3 KB | 32.5 KB | 20 test images, weights in ROM |
| MNIST MLP | 56.7 KB (5.4 KB code + 51.4 KB weights/images) | 26.4 KB | 32 test images; fc1 weights are also copied to RAM |
| CPU set (`aster_minimal`) | 2.2–15.0 KB | 1.0–18.4 KB | CoreMark 15.0 KB ROM, Dhrystone 18.4 KB RAM |

Only MNIST needs more than 32 KiB of ROM, and only because its 32 test images
and weights are baked into ROM.

## Options

| Option | On-chip memory | Macro area (2 KiB macros) | Consequence |
| --- | --- | ---: | --- |
| A. Full v1 map | 64 KiB ROM + 64 KiB RAM | ≈18.2 mm² | No software change; die well above 20 mm²; Phase 16 never completed routing it; highest leakage. |
| B. Sized unified memory (recommended) | 96 KiB host-loaded unified SRAM (code + data), banked; NPU operand buffers separate (Phase 19) | ≈13.7 mm² | Every current workload fits without dropping any. Instruction and data sides use separate banks so the CPU can fetch and load in one cycle. |
| B′. Tight unified memory | 64 KiB unified SRAM | ≈9.1 mm² | Fits all but MNIST and CIFAR as built; those need host-streamed test images or fewer retained images. |
| C. External memory | Small on-chip SRAM + an off-chip memory interface | smallest | Adds a controller and pads; every benchmark result then includes off-chip latency/bandwidth; the FPGA must model the same latency. |

Separately from the main memory, caches and small, fast arrays (L1 tags/data,
NPU operand buffers, register files) should be compared as macro versus
latch/flip-flop RAM in Phase 18 on measured area and timing.

## Recommendation

**Option B:** a 96 KiB host-loaded unified SRAM built from 2 KiB macros in
independent banks, with a registered macro interface budgeted to half a cycle,
and the die budget set from Phase 18's block-level area measurements plus the
macro area. It keeps every workload in the catalog unchanged, avoids the
unrouted 64-macro configuration, and matches how the Phase 16 ASIC already
booted (host-loaded SRAM instead of a mask ROM). The NPU operand buffers are
decided separately in Phase 19.

**Decision (owner, 29 September 2026): Option B.** The die budget is set from
Phase 18's measured block areas plus ≈13.7 mm² of macros, and is recorded when
the Phase 18 feasibility report closes.
