# Phase 12.5: Machine timer

Status: **in progress**.
Baseline: pushed Phase 12 closeout `069cbc8`.
This phase is a bounded pre-freeze addition on the road to Aster v1: one
memory-mapped machine timer, added to the coherent top only. It is deliberately
small and verified in isolation before the Phase 12.6 interrupt controller
consumes its `timer_irq` output. Shared L2, interrupts on other sources and
RTL frequency optimization remain out of scope.

The README freeze point lists a timer and interrupts; this phase and Phase 12.6
close those two gaps. Legacy `aster_minimal` and Phase 5 maps stay frozen.

## Register contract

Page `0x2000_1000` (4 KiB, previously reserved and unimplemented), custom MMIO:

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x00/04` | `TIME_LO/HI` | R | free-running 64-bit up-counter in `aclk` cycles |
| `0x08/0c` | `COMPARE_LO/HI` | RW | 64-bit compare value; byte-strobed writes merge |
| `0x10` | `CONTROL` | W | bit0 enable, bit1 clear pending; byte lane 0 only |
| `0x14` | `STATUS` | R | bit0 pending, bit1 enabled |
| `0x18` | `ABI` | R | timer ABI = 1 |
| `0x1c` | `CLOCK_HZ` | R | configured nominal fabric frequency |

## Behaviour

- `TIME` increments by one every `aclk` and wraps modulo 2^64.
- Writing `COMPARE` while enabled arms the timer; when `TIME` reaches
  `COMPARE`, `STATUS.pending` is set and `timer_irq` asserts (level).
- `CONTROL` bit1 clears `pending`; `CONTROL` bit0 enables/disables. Disabling
  does not clear `pending`.
- Reset clears `TIME`, `COMPARE`, `enabled` and `pending`, and deasserts
  `timer_irq`.
- Reads are combinational and stable; a `CONTROL` write is recognized only on
  byte lane 0 of an accepted write, matching the UART/perf convention.
- `timer_irq` is exposed at the top for Phase 12.6. Until then it is observable
  but not delivered to a core.

## Verification and acceptance gates

- [x] Contract frozen.
- [x] Unit scoreboard: free-running increment, 64-bit wrap, compare match cycle,
      clear/enable/disable, byte-strobed compare merge, reset.
- [ ] Coherent-top integration with an ABI/clock/status readback and no change to
      any existing register page or ABI.
- [ ] Firmware measures a programmed interval with the timer and agrees with the
      Phase 3 cycle counter.
- [ ] Legacy `aster_minimal` and Phase 5 maps unchanged.
- [ ] Routed all-engine overlay with reset/timing/HWH signoff.
- [ ] Physical Pynq-Z1 capture with two warm boots and a stopped-state snapshot.
- [ ] Self-contained closeout bundle and read-only audit.

## Explicit non-goals

This phase does not add interrupts (Phase 12.6), a periodic auto-reload mode,
per-hart timers, RISC-V `mtime`/`mtimecmp` CSRs, a privileged timer interface,
shared L2, or any change to the legacy maps. It does not optimize the design for
frequency.
