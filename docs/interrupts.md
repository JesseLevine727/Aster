# Phase 12.6: Interrupt controller

Status: **in progress**.
Baseline: pushed Phase 12.5 closeout `9971a8c`.
This phase adds one memory-mapped interrupt controller to the coherent top and
delivers a level interrupt to each PicoRV32 hart, consuming the Phase 12.5
`timer_irq` output. It is the second bounded pre-freeze addition on the road to
Aster v1. Shared L2, interrupt prioritization, nested interrupts, a privileged
trap interface and RTL frequency optimization remain out of scope.

The README freeze point lists a timer and interrupts; Phase 12.5 and this phase
close those two gaps. Legacy `aster_minimal` and Phase 5 maps stay frozen, and
the vendor PicoRV32 core is not modified.

## Register contract

Page `0x2000_4000` (4 KiB, previously reserved and unimplemented), custom MMIO:

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x00` | `ENABLE0` | RW | hart 0 source enable mask |
| `0x04` | `ENABLE1` | RW | hart 1 source enable mask |
| `0x08` | `PENDING` | RW1C | latched source bits; write 1 clears, 0 leaves |
| `0x0c` | `ACTIVE0` | R | `PENDING & ENABLE0` |
| `0x10` | `ACTIVE1` | R | `PENDING & ENABLE1` |
| `0x14` | `RAISE` | W1S | set the software source bit |
| `0x18` | `ABI` | R | controller ABI = 1 |
| `0x1c` | `SOURCES` | R | number of source bits = 4 |

Only byte lane 0 of `ENABLE0/1`, `PENDING` and `RAISE` is meaningful. Sources:

| Bit | Source | Asserted by | Cleared by |
| ---: | --- | --- | --- |
| 0 | timer | Phase 12.5 `timer_irq` | software clears timer `STATUS.pending` |
| 1 | DMA | DMA job completion | controller `PENDING` W1C |
| 2 | NPU | NPU job done | controller `PENDING` W1C |
| 3 | software | `RAISE` write | controller `PENDING` W1C |

## Behaviour

- Each hardware source sets its `PENDING` bit on a rising edge in `aclk`; a
  one-cycle pulse is captured.
- `PENDING` is cleared by writing a 1 to a bit (W1C); writing 0 leaves it. A
  clear in the same cycle as a new edge wins.
- `RAISE` sets the software bit (bit 3); it is cleared only by the same W1C.
- `irqN` is the level `|(PENDING & ENABLEN)`, routed to hart N's PicoRV32 `irq`
  input bit 0. The controller therefore owns source-to-hart routing; a source
  should normally be enabled on one hart at a time.
- The handler reads `ACTIVE0/1` (or `PENDING`) to identify the source, services
  it, then clears the controller bit. For the timer it must also clear the
  timer's own pending, otherwise the next compare cannot produce a new edge.
- Reset clears `ENABLE0/1` and `PENDING`, and deasserts both `irqN`. The
  controller resets with `peripheral_resetn` (held while STOPPED).
- The PicoRV32 core keeps `ENABLE_IRQ=1`, `ENABLE_IRQ_QREGS=0` and
  `ENABLE_IRQ_TIMER=0`. `MASKED_IRQ` masks the upstream ebreak/buserror IRQ
  bits so `ebreak` continues to trap. `LATCHED_IRQ=0` makes the input
  level-sensitive. The IRQ vector is the upstream fixed `PROGADDR_IRQ = 0x10`.

## Implementation notes

- `ENABLE_IRQ_QREGS` must stay off. With it on, PicoRV32 decodes custom-0
  `funct7=0` as `getq`, which shadows the Phase 8 Xasterdot8 instruction (also
  custom-0 `funct7=0`) and silently corrupts every dot8 kernel. With it off the
  core aliases q0/q1 onto `gp`/`tp`; Aster firmware leaves `gp` unused and only
  uses `tp` for the startup hart id before interrupts are enabled, so the alias
  is safe.
- `LATCHED_IRQ=0` is required because the controller holds each source as a
  level. Edge-latching the line would re-assert the CPU pending bit during the
  handler and cause one spurious re-entry after the source is cleared.
- The handler saves all caller-saved registers around a C dispatch. Each of the
  sixteen stack accesses crosses the coherent fabric, so the measured
  match-to-handler latency is ~594 cycles in simulation and ~662 on the board;
  the firmware bound is 8192 cycles.
- The atomic fabric permits the controller page `0x20004` for both harts, and
  the fixed `0x10` vector is placed in `.text.init` ahead of the startup code in
  both `start.S` and `start_multicore.S`, with a weak trap default dispatch.

## Verification and acceptance gates

- [x] Contract frozen.
- [x] Unit scoreboard: edge capture, W1C, RAISE, per-hart masks, level routing,
      reset.
- [x] Coherent-top integration with an ABI/source readback and no change to any
      existing register page or ABI.
- [x] Firmware takes a timer interrupt, services it, resumes the interrupted
      loop, and a software interrupt round-trips; both agree with the cycle
      counter.
- [ ] Legacy `aster_minimal` and Phase 5 maps unchanged.
- [ ] Routed all-engine overlay with reset/timing/HWH signoff.
- [ ] Physical Pynq-Z1 capture with two warm boots and a stopped-state snapshot.
- [ ] Self-contained closeout bundle and read-only audit.

## Explicit non-goals

This phase does not add priority levels, nested or preemptive masking, a
privileged trap/CSR interface, per-source vectoring, shared L2, or any change to
the legacy maps. It does not optimize the design for frequency.
