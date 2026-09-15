# Phase 10: CPU vs multicore vs ISA vs NPU

Phase 10 is complete. This package is the immutable evidence bundle for the
README roadmap exit: identical signed-INT8 dot, FIR and GEMM kernels executed
through the scalar CPU, two coherent harts, the Xasterdot8 instruction and the
4×4 INT8 NPU on one all-engine SoC image, measured by AsterBench v8 with an
independent oracle, and validated in simulation, on routed FPGA and on real
Pynq-Z1 silicon.

The contract is retained at [spec/phase10.md](spec/phase10.md). The package does
not alter the Phase 1–9 closeouts; those remain the historical baseline
referenced by the root README.

## Read-only audit

From the repository root:

```sh
python3 scripts/audit_phase10.py audit docs/results/phase10/closeout-8371c3d
```

The audit never connects to the board. It inventories and hashes every artifact,
re-validates all 288 primary and 576 repeat records with the independent
AsterBench v8 oracle, recomputes the study ratios, checks both repeat
comparisons, validates the combined DOT8+NPU HWH handoff and the routed
timing/DRC/route/utilization reports, re-checks the 12 physical boot records and
their STOPPED state, checks the full make-check log, and binds the manifest to
the committed source snapshot. `--current` additionally checks that the working
tree still matches the retained source snapshot.

## Requirement map

| README Phase 10 gate | Evidence |
| --- | --- |
| Contract, kernel semantics and fairness rules | [Phase 10 contract](spec/phase10.md) |
| Full regressions and host tests | [make-check.log](verification/make-check.log) |
| Cross-engine study and fresh repeats | [primary](simulation/primary/study.json), [repeat1](simulation/repeat1/study.json), [repeat2](simulation/repeat2/study.json), [compare](simulation/compare.log) |
| FPGA per-engine resources and routed timing | [FPGA reports](fpga/) |
| Physical Pynq-Z1 acceptance | [physical packages](physical/) |
| Clean source and immutable mapping | [source snapshot](source/source-state.json) |

## Results

The frozen study is 288 captures: three kernels, four execution paths, two byte
placements and both cache modes, each with two jobs. Two independently fresh
captures reproduce the primary study exactly. Every record was recomputed by the
independent v8 oracle.

Simulated best path (aligned, cache on):

- **dot**: scalar until `K ≈ 7`; DOT8 wins `K = 16–256` (up to 2.2×); NPU wins at
  `K = 1024` (2.8×).
- **FIR**: two coherent harts scale best (up to 1.9×); DOT8 ≈1.7×; the NPU loses
  as `K` grows because it materializes a Toeplitz input.
- **GEMM**: the NPU wins from `3×5×8` up to **13.3×** at `16×16×64`; two harts
  scale to roughly 2×.

Routed FPGA (all-engine image, 31.25 MHz): 25,121 LUT (47.2%), 19,198 FF
(18.0%), 32 BRAM (22.9%), 24 DSP (10.9%); WNS 4.177 ns / WHS 0.051 ns, zero
failing endpoints, zero routing errors, a clean methodology report, and 79
advisory DSP-pipelining DRC warnings (no DRC errors). Per-engine marginal
cost: DMA +1,488 LUT, DOT8 +1,220 LUT, NPU +4,543 LUT / +24 DSP.

Physical Pynq-Z1 (2 warm boots per capture, 31.25 MHz): GEMM 4×4×16 ratios
relative to scalar 27,821 cycles are multicore 1.628×, DOT8 1.476× and NPU
3.974× — closely matching simulation.

## Provenance and limitations

Every simulation and physical capture binds the clean committed revision
`8371c3d66bca995db95147a3eac81a072d5b6283`. The simulation is event-level RTL;
the FPGA numbers are real post-synthesis/post-route Vivado results; the physical
numbers are real 31.25 MHz silicon measurements. The physical transport is the
FPGA UART transmitter-to-receiver serial loopback read over AXI/Linux, not an
external Pmod electrical-loopback test. The board's PYNQ 3.1.1 install could not
enumerate this Zynq under the 6.6.10 Xilinx kernel, so the physical runner uses
the Linux `fpga_manager` and `/dev/mem`. Phase 10 does not claim ASIC readiness,
area or energy; those remain later phases.
