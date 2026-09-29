# Aster v1.0 known limitations

These are deliberate v1.0 boundaries, not defects. Each is listed with its
consequence for interpretation and its planned resolution. The "Planned" column
was updated on 2026-09-29 for v1.1–v1.3 and the
[v2 plan](phase17-plus.md).

## Architecture

| Limitation | Consequence | Planned |
| --- | --- | --- |
| No shared L2 in v1.0; coherence is MSI-like over shared RAM | L2 capacity/refill questions are unanswered in v1.0 | **resolved in v1.1** ([contract](l2.md)): a memory-side read-allocate/write-through L2, default off; see the [analysis](results/v1.1/closeout-bb881fc/analysis.md) |
| All memory traffic, including cache hits, is serialized | One transaction is in flight across the SoC: both harts, DMA and the NPU share one fabric → arbiter → cache-controller path, and a D-cache load hit costs about seven cycles | v2: per-core L1 with single-cycle hits (Phase 18) and parallel hits (Phase 20) |
| Single outstanding native request per hart | No memory-level parallelism; latency is exposed | out of scope for v1; v2 Aster core and memory system (Phase 18) |
| No interrupt priority, nesting or preemption | A single level per hart; the handler runs to completion and services all enabled sources | v2 |
| Fixed 4×4 INT8 NPU | CNN layers are tiled in software; utilization depends on tile shape | geometry **resolved in v1.2** ([2×2/4×4/8×8](npu-geometry.md), 4×4 default); the array is starved by its byte-wide data path — v2 Phase 19 |
| Polling DMA completion, no descriptor ring | One job at a time; no scatter-gather | v2 |
| No privileged trap/CSR interface, MMU or OS | Bare-metal only; no process isolation | out of scope for v1; the v2 Aster core adds machine-mode CSRs and standard traps (no MMU/OS) |
| PicoRV32 `QREGS` off | Firmware must not use `gp`/`tp` for general data; `tp` holds only the startup hart id | v2: the Aster core implements Xasterdot8 natively, removing the collision |
| No RTL frequency optimization | FPGA baseline was 31.25 MHz; frequency is not a v1.0 claim | v1.3 closed the all-engine FPGA image at 50 MHz; SKY130 (Phase 16) did not close; v2 targets 100 MHz |

## Verification

| Limitation | Consequence |
| --- | --- |
| Not an architectural-suite certification | The ISA regressions prove the implemented subset and the Aster software stack, not formal RV32IMA compliance (v2 runs `riscv-arch-test` on the Aster core) |
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
