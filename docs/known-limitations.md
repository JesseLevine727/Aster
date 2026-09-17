# Aster v1.0 known limitations

These are deliberate v1.0 boundaries, not defects. Each is listed with its
consequence for interpretation and its planned resolution.

## Architecture

| Limitation | Consequence | Planned |
| --- | --- | --- |
| No shared L2; coherence is MSI-like over shared RAM | L2 capacity/refill questions are unanswered; measured coherence costs are a lower bound relative to a directory protocol | v2 / Phase 14 |
| Cache hits are not serialized | Coherence and hit-latency numbers are optimistic; a directory design would add serialization | v2 |
| Single outstanding native request per hart | No memory-level parallelism; latency is exposed | out of scope for v1 |
| No interrupt priority, nesting or preemption | A single level per hart; the handler runs to completion and services all enabled sources | v2 |
| Fixed 4×4 INT8 NPU | CNN layers are tiled in software; utilization depends on tile shape | Phase 14 (2×2/8×8) |
| Polling DMA completion, no descriptor ring | One job at a time; no scatter-gather | v2 |
| No privileged trap/CSR interface, MMU or OS | Bare-metal only; no process isolation | out of scope for v1 |
| PicoRV32 `QREGS` off | Firmware must not use `gp`/`tp` for general data; `tp` holds only the startup hart id | v2 (or a non-colliding dot8 encoding) |
| No RTL frequency optimization | FPGA baseline is 31.25 MHz; frequency is not a v1 claim | Phase 14/16 |

## Verification

| Limitation | Consequence |
| --- | --- |
| Not an architectural-suite certification | The ISA regressions prove the implemented subset and the Aster software stack, not formal RV32IMA compliance |
| Physical transport is in-FPGA UART TX→RX over AXI/SSH | It is not external Pmod electrical-loopback validation |
| Standalone legacy `aster_minimal` builds retain reset/BRAM warnings | Documented in the Phase 1–4 closeout; the Linux overlay is the warning-free target |
| Simulated cache geometries do not imply FPGA fit | Only the default geometry is implemented on the PYNQ-Z1 |
| Historical Phase 1–4 bitstreams contain the diagnosed reset-polarity defect | They must not be deployed; the Phase 2 replacement is authoritative |

## Scope

- Linux, out-of-order execution, a GPU, a large NoC, RV64 and high core counts
  are deliberately **not** v1 requirements. Scope discipline is part of the
  project.
- Shared L2 is the one README freeze-point item intentionally deferred; the
  [Phase 13 contract](phase13.md) records the decision.

## Interpretation

- The v1.0 benchmark results characterize **compute placement** — scalar,
  multicore, Xasterdot8 and NPU — on one frozen all-engine image. They do not
  claim that specialization always wins; measured crossovers and slowdowns are
  retained in the phase bundles.
- "Real time" in Phase 12 means sustained per-chunk throughput, not hard
  deadlines. The Phase 12.5 timer and Phase 12.6 interrupts provide the
  mechanism for deadlines but no deadline-scheduling policy is part of v1.
