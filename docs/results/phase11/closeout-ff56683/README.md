# Phase 11: Quantized INT8 ML inference

Phase 11 is complete. This package is the immutable evidence bundle for the
README roadmap exit: a frozen quantized INT8 MLP runs end to end on the
coherent RV32IMA/NPU SoC, with the CPU owning control, requantization,
activation and classification and the 4×4 NPU owning the matrix multiply.

The contract is retained at [spec/phase11.md](spec/phase11.md). The package does
not alter the Phase 1–10 closeouts; those remain the historical baseline.

## Read-only audit

From the repository root:

```sh
python3 scripts/audit_phase11.py audit docs/results/phase11/closeout-ff56683
```

The audit never connects to the board. It re-runs the independent integer
reference against the model artifact, re-validates every four-path study record
with the v9 oracle, recomputes the scalar-relative ratios, checks the routed
FPGA handoff/timing/DRC/route/utilization reports, re-checks all eight physical
boot records and their STOPPED state, verifies the exported header hashes, and
binds the manifest to the committed source snapshot. `--current` additionally
checks that the working tree still matches the retained source snapshot.

## Requirement map

| README Phase 11 gate | Evidence |
| --- | --- |
| Contract, model, quantization and memory map | [Phase 11 contract](spec/phase11.md), [model](model/model.json) |
| Full regressions and host tests | [make-check.log](verification/make-check.log) |
| Four-path study and fresh repeats | [study](simulation/study.json) |
| Routed FPGA overlay | [FPGA reports](fpga/) |
| Physical Pynq-Z1 acceptance | [physical packages](physical/) |
| Clean source and immutable mapping | [source snapshot](source/source-state.json) |

## Results

The frozen model is an MLP `784 → 32 (ReLU) → 10` (artifact hash
`63c352eb…`). The independent integer reference and the exported C headers agree
with the firmware on all 32 retained images; 30/32 match the true labels, with
zero floating/integer disagreement.

Four-path per-image cycles, two fresh repeats:

| Method | Simulation | scalar/method | Physical | scalar/method |
| --- | ---: | ---: | ---: | ---: |
| scalar | 2,039,263 | 1.000 | 2,064,873 | 1.000 |
| multicore | 1,029,910 | 1.980 | 1,060,544 | 1.947 |
| dot8 | 868,357 | 2.348 | 885,527 | 2.332 |
| npu | 427,307 | 4.772 | 462,953 | 4.460 |

The NPU is the fastest path at ~4.5–4.8× the scalar baseline; DOT8 is ~2.3×;
two coherent harts are ~1.95×. All four classify identically. The workload is
memory/coherence-bound at this size: the NPU array is busy only ~1.6% of the
inference window, so the speedup comes from moving the multiply off the CPU, not
from array throughput.

Routed FPGA (all-engine image, 31.25 MHz): 25,121 LUT (47.2%), 19,198 FF
(18.0%), 32 BRAM (22.9%), 24 DSP (10.9%); WNS 4.177 ns, WHS 0.051 ns, pulse
14.750 ns, zero failing endpoints, clean methodology, zero routing errors, and
advisory DSP-pipelining DRC warnings only. Physical captures ran two warm boots
per path at 31.25 MHz, each record validated on the board.

## Provenance and limitations

Every capture binds the clean committed revision
`081df4c1deb1e918665fba442a6eb3dc1ae9264`. Training, the reference, the export
and the study are deterministic. The physical transport is the FPGA UART
transmitter-to-receiver serial loopback read over AXI/Linux, not an external
Pmod loopback. This phase does not claim ASIC readiness, area or energy; those
remain later phases.
