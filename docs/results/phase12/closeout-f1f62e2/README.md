# Phase 12: Real-time heterogeneous demo

Phase 12 is complete. This package is the immutable evidence bundle for the
README roadmap exit: a streaming ECG pipeline that exercises the CPU, DMA,
Xasterdot8 and the 4×4 NPU together on the coherent RV32IMA SoC, sustained chunk
by chunk and bit-exact against an independent oracle.

The contract is retained at [spec/phase12.md](spec/phase12.md). The package does
not alter the Phase 1–11 closeouts.

## Read-only audit

From the repository root:

```sh
python3 scripts/audit_phase12.py audit docs/results/phase12/closeout-f1f62e2
```

The audit never connects to the board. It inventories and hashes every artifact,
verifies the PhysioNet sample provenance and hash, re-runs the strict v10
validator and the independent pipeline oracle on the retained study and both
physical records, checks the routed FPGA handoff/timing/DRC/route/utilization
reports, checks the full make-check log, and binds the manifest to the committed
source snapshot. `--current` additionally checks that the working tree still
matches the retained source snapshot.

## Requirement map

| README Phase 12 gate | Evidence |
| --- | --- |
| Contract, pipeline stages and sustained-throughput interpretation | [Phase 12 contract](spec/phase12.md) |
| Real PhysioNet MIT-BIH sample segment | [ecg_segment.json](input/ecg_segment.json) |
| Full regressions and host tests | [make-check.log](verification/make-check.log) |
| Streaming study and fresh repeats | [study.json](simulation/study.json) |
| Routed FPGA overlay | [FPGA reports](fpga/) |
| Physical Pynq-Z1 acceptance | [physical package](physical/) |
| Clean source and immutable mapping | [source snapshot](source/source-state.json) |

## Results

The pipeline runs 16 chunks of 64 samples. Each chunk is staged by the CPU,
moved by DMA, filtered by a 16-tap Xasterdot8 FIR on the secondary hart, reduced
to four features on the CPU, classified by a 3×4 NPU GEMM, and folded into a
checksum. The retained simulation study is three independently fresh repeats,
all identical: **1,434,468 cycles**, checksum `0x19b26ea4`, matching the
independent oracle exactly.

The routed all-engine overlay targets the Pynq-Z1 at 31.25 MHz and passes the
HWH handoff, timing, route, DRC and methodology gates. Two physical warm boots
at 31.25 MHz each emitted the exact record and stopped cleanly with a retained
64 KiB RAM image.

"Real time" here means **sustained per-chunk throughput**, not a hard deadline
guarantee: Aster still has no interrupts or timers.

## Provenance and limitations

Every capture binds the clean committed revision
`f1f62e2b327d34b2212045396585c8e86a84a6f3`. The sample source is PhysioNet
MIT-BIH record 100 (MLII); the transport is the FPGA UART TX-to-RX serial
loopback read over AXI/Linux, not an external Pmod loopback. This phase does not
claim hard real-time scheduling, interrupts or timers; those remain later work.
