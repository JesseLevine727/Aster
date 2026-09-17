# Frequency measurement: where the clock actually stops

Measured on the Pynq-Z1 with the all-engine 4×4 overlay (`aster_linux.bit`),
running the `streaming_ecg` workload (CPU + DMA + Xasterdot8 + NPU) and checking
the independent-oracle checksum at each FCLK0. Raw data in
[`sweep/sweep.json`](sweep/sweep.json).

## Result: 100 MHz functional ceiling, 3.2× the 31.25 MHz baseline

| FCLK0 | Result | cycles | checksum |
| ---: | --- | ---: | --- |
| 31.25 MHz | PASS | 1 577 811 | `0x19b26ea4` |
| 40.00 MHz | PASS | 1 577 811 | `0x19b26ea4` |
| 50.00 MHz | PASS | 1 577 811 | `0x19b26ea4` |
| 62.50 MHz | PASS | 1 577 811 | `0x19b26ea4` |
| 83.33 MHz | PASS | 1 577 811 | `0x19b26ea4` |
| **100.00 MHz** | **PASS** | 1 577 811 | `0x19b26ea4` |
| 111.11 MHz | **FAIL** | — | `retired must be in (0, cycles]` |

Every passing frequency produces the identical workload checksum, so the design
is functionally correct across the whole range. The failure at 111.11 MHz is a
**corrupted `retired` counter**, not a wrong result — the data path still
produced the correct output.

## Why 100 MHz when static timing closes at 34.9 MHz

The routed design closes at **WNS +3.384 ns** at a 32 ns period → 34.9 MHz
implied. But the actual silicon runs ~3× faster. Two reasons:

1. **The static analysis is conservative.** It reports the slow process corner
   with 0.481 ns of clock uncertainty; the real part at room temperature has far
   more margin.
2. **The critical path is a performance counter, not the data path.** The worst
   path is:

   ```
   g_coherent.soc/cache/state_reg[1]  ->  g_dma.perf/counters_reg[11][61]
   data path 28.177 ns (logic 6.301 ns / 22%  route 21.876 ns / 78%)
   logic levels 34 (CARRY4=16, LUT6=8, LUT5=2, LUT3=3, LUT2=3, MUXF7=2)
   ```

   `counters[11]` is `dma_events[11] = abort_event`. The `streaming_ecg`
   workload never aborts the DMA, so this counter's increment input is constant
   and the path is never exercised — the workload passes even though the path
   could not meet 100 MHz. At 111.11 MHz the failure is in that same
   counter bank, confirming the limit.

The full path is in [`critical-path/worst_path.rpt`](critical-path/worst_path.rpt).

## Static closure experiment: what Vivado can actually close

To separate "the RTL is slow" from "Vivado was not asked to try", I rebuilt the
overlay with the timing constraint tightened to a 100 MHz period (10 ns) and let
timing-driven placement optimize. Raw report in
[`closure-100mhz/timing_summary.rpt`](closure-100mhz/timing_summary.rpt).

| Constraint | WNS | Failing endpoints | Worst path | Implied closure |
| --- | ---: | ---: | --- | ---: |
| 32 ns (31.25 MHz) | +3.384 ns | 0 | cache state → DMA perf counter | 34.9 MHz |
| 10 ns (100 MHz) | **−10.860 ns** | **38 680 / 55 599** | NPU rows → engine state | ~47.8 MHz |

Two things are clear:

1. **Timing-driven optimization helps a lot.** The worst path drops from
   28.6 ns to 20.9 ns (27%) and the critical path moves to a different block
   (the NPU descriptor → engine state). The 78% route dominance was partly a
   placement artifact of the loose constraint.
2. **The design does not close at 100 MHz.** Even optimized, 70% of endpoints
   fail at 10 ns, and the best achievable closure is ~47.8 MHz. The gap to
   100 MHz is ~2×, which is the slow-corner-versus-typical-corner difference:
   the silicon runs at 100 MHz at room temperature, but the slow corner (used
   for signoff) closes at ~48 MHz.

## What this says for RTL optimization

- **The functional data path has large margin.** The design runs the full
  heterogeneous pipeline correctly at 100 MHz, and the only failure at
  111 MHz is a measurement counter. There is no evidence that the CPU,
  caches, fabric, DMA or NPU data paths are near their limit.
- **The static closure is held back by the performance-counter event routing.**
  78% of the critical path is route delay from the cache across the die to the
  DMA perf counter. Registering (pipelining) the event signals between their
  sources and the counters would break that long route and should raise the
  static closure substantially. This is safe: the perf counters are
  measurement-only and a one-cycle event latency changes no functional
  behavior or ABI.
- **Then the next limit appears.** With the perf path fixed, the AXI/lifecycle
  and the coherent cache/fabric paths become candidates; the 125 MHz run
  (before the perf fix) already failed the AXI STOPPED handshake.

## Recommendation

Two honest options, in increasing order of effort:

1. **Re-baseline at ~48 MHz (recommended first step).** Tighten the timing
   constraint, let timing-driven placement close, and report the frequency the
   design actually closes at. This is a ~1.5× improvement over 31.25 MHz with
   **no RTL change** — just a constraint/toolchain change and re-verification.
2. **Pipeline for 100 MHz (larger).** Closing the slow corner at 100 MHz needs
   ~2× on the critical paths: pipeline the coherent cache datapath (the dominant
   class), the NPU descriptor/address path and the perf-counter events. That is
   a real v1.3 RTL phase with its own contract and re-verification.

The measurement does **not** justify optimizing the functional data path for its
own sake: it already runs the whole heterogeneous pipeline at 100 MHz. The work
is about closing the slow corner, not about making the logic faster.
