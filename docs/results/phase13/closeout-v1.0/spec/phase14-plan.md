# Phase 14 design-space plan (seed)

This is the seed plan for the Phase 14 design-space exploration. It maps the
frozen v1.0 build parameters and the README research questions onto concrete,
comparable experiments. It is not a contract; it is refined into a Phase 14
contract when the freeze closes.

## Principle

Phase 14 varies **parameters**, not features. The v1.0 interfaces are frozen, so
every sweep keeps the same software, ABIs and measurement window and changes
only architecture. Results are only comparable if the workload definition,
counter window and provenance are identical — the AsterBench v10 machinery
already enforces that.

## Knobs

| Knob | Values | Notes |
| --- | --- | --- |
| `HART_COUNT` | 1, 2 | multicore scaling and coherence cost |
| `L1_LINE_WORDS` × `L1_LINE_COUNT` | e.g. 4×8, 4×16, 4×32, 8×16 | L1 capacity and line size |
| `MEMORY_WAIT_CYCLES` | 0, 1, 4, 16, 64 | modeled lower-memory latency / bandwidth |
| `SYNC_MEMORY` | 0, 1 | asynchronous vs synchronous backing |
| engine | scalar, multicore, DOT8, NPU | compute placement |
| NPU geometry | 2×2, 4×4, 8×8 | requires parameterizing `aster_int8_array` |
| problem size | per workload | crossover discovery |

Shared L2 (`ENABLE_L2`) is a v2 knob and is not part of the Phase 14 sweep until
the L2 subsystem exists.

## Research questions to experiments

| README question | Experiment | Primary metric |
| --- | --- | --- |
| Performance scaling 1→2 cores | `parallel`, `multicore_gemm`, `reduce_parallel` at 1 vs 2 harts | speedup, per-hart cycles |
| When memory bandwidth bottlenecks | working-set and stride sweeps at increasing `MEMORY_WAIT_CYCLES` | cycles, backing transactions, miss rate |
| L1/L2 size sensitivity | cache geometry sweep on `strided`, `sort_search`, `fft` | hit rate, cycles (L2 part deferred) |
| Cost of coherence and false sharing | coherent litmus, ping-pong, false-sharing kernels | interventions, invalidations, writebacks |
| DMA vs CPU copy crossover | Phase 7 size/alignment sweep, cache on/off | cycles/byte, alignment sensitivity |
| ISA vs accelerator justification | dot/FIR/GEMM through scalar, DOT8, NPU | cycles, retired, accelerator cycles |
| NPU offload overhead vs problem size | NPU tiling sweep on `conv2d_npu`, `cifar_cnn` | setup bytes, utilization, cycles |
| Accelerator dimensions | 2×2/4×4/8×8 on GEMM | utilization, LUT/DSP, cycles |
| Performance per area / energy | Phase 16 PPA on the frozen design | mm², energy/op, GOPS/W |
| FPGA vs SKY130 | Phase 15/16 comparison | Fmax, area, energy |

## Method

1. Fix the software and the measurement window. Use the AsterBench v10 records
   and the coherent ABI 4 common freeze.
2. Vary one knob at a time; retain every configuration with provenance.
3. Report crossovers and slowdowns, not just speedups. The project's value is in
   *where each approach wins and where its overhead makes it lose*.
4. Keep each sweep a reproducible study with fresh repeats, as in Phases 10–12.

## Prerequisites before the sweep

- Parameterize the NPU array geometry (currently fixed 4×4) so 2×2/8×8 can be
  built and synthesized.
- Add a study driver that compiles one configuration, runs the workload set, and
  emits a machine-readable comparison table.
- Decide how many configurations fit on the PYNQ-Z1 and which are
  simulation-only.

## Minimal configuration for Phase 15 (SKY130)

Take the smallest meaningful system through the open ASIC flow first:

```text
HART_COUNT=1  ENABLE_L1=0  ENABLE_DMA=0  ENABLE_DOT8=0  ENABLE_NPU=0
```

This exercises the CPU, ROM/RAM, UART and clock/reset on SKY130 before the full
all-engine v1.0 design is attempted. SRAM macro strategy is an open decision
recorded in `docs/architecture.md`.
