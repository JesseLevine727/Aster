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

Do a bounded **v1.3 frequency phase**: pipeline the perf-counter event paths
(and any other measurement-only long routes), re-close static timing, and
re-measure. The target is a static closure that matches the ~100 MHz the
silicon already delivers, which would let the reported baseline rise from
31.25 MHz to ~100 MHz (3.2×) with no functional change. RTL optimization of the
actual data path is not indicated by this measurement.
