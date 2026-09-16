# Phase 12: Real-time heterogeneous demo

Status: **complete**.
Baseline: pushed Phase 11 closeout `2a4b649`.
The [README roadmap](../README.md#phase-12--real-time-heterogeneous-demo) defines
this phase as using a streaming dataset to exercise the CPU, DMA, DSP/custom
instructions and NPU together, sustaining the stream while reporting
utilization/performance counters.

Phase 12 adds **no new compute engine**. It composes the Phase 7 DMA engine, the
Phase 8 Xasterdot8 instruction and the Phase 9 NPU into one streaming pipeline
on the coherent RV32IMA SoC. This document is the Phase 12 contract.

## Scope and design principles

The workload is a finite streaming ECG pipeline. A deterministic chunk schedule
drives all engines in sequence per chunk, and the whole pipeline must be
bit-exact against an independent Python oracle. "Real time" here means
**sustained per-chunk throughput** — the pipeline finishes each chunk and
advances without stalls — not a hard deadline guarantee, because Aster still has
no interrupts or timers.

The sample source is a real PhysioNet MIT-BIH Arrhythmia Database record 100
(MLII) segment: samples 0–1023, ADC zero 1024, scale 4, baked into ROM by
`scripts/gen_ecg_data.py` and retained at
[docs/results/phase12/ecg_segment.json](results/phase12/ecg_segment.json).

## Pipeline contract

Per chunk of `ECG_CHUNK = 64` samples, `ECG_CHUNKS = 16` chunks total:

1. **CPU** stages the next 64 samples from ROM into shared RAM.
2. **DMA** copies the window to a working buffer (the Phase 7 coherent engine).
3. **Xasterdot8** (secondary hart) runs a 16-tap FIR over the window.
4. **CPU** extracts four features (peak, mean absolute value, zero crossings,
   mean) and quantizes them to INT8.
5. **NPU** (primary hart) classifies the features with a 3×4 INT8 GEMM.
6. The class is folded into a running checksum.

The primary hart orchestrates DMA and the NPU while the secondary hart runs the
FIR; they synchronize with the established release/acquire mailbox handoff. The
measured window starts at the first chunk and ends after the last chunk's class
is visible, so it includes staging, DMA, FIR, feature extraction, NPU inference
and coordination.

## Measurement contract

The workload emits an AsterBench v10 record (`name=streaming_ecg`,
`category=system`) binding the chunk size, chunk count, filter length, sample
provenance, the hardware counters and the `dma_bytes` and `accelerator_cycles`
observations. The record must satisfy the strict
[`scripts/asterbench_v10.py`](../scripts/asterbench_v10.py) validator, and the
checksum must equal the independent pipeline oracle in
[`scripts/workload_reference.py`](../scripts/workload_reference.py).

## Verification and acceptance gates

- [x] Contract, pipeline stages and the sustained-throughput interpretation frozen.
- [x] Real PhysioNet MIT-BIH sample segment baked and hash-bound.
- [x] Pipeline is bit-exact against the independent oracle on the all-engine SoC.
- [x] Deterministic fresh repeats reproduce the record exactly.
- [x] Routed all-engine overlay with reset/timing/HWH signoff.
- [x] Physical Pynq-Z1 capture with two warm boots and a stopped-state snapshot.
- [x] Self-contained closeout bundle and read-only audit.

The README Phase 12 exit is satisfied at commit `847740b` plus the retained
closeout documentation: the streaming ECG pipeline is bit-exact against the
independent oracle, three fresh simulation repeats and two physical warm boots
reproduce the record, and the [self-contained closeout
bundle](results/phase12/closeout-847740b/README.md) passes
`scripts/audit_phase12.py`.

## Explicit non-goals

This phase does not add interrupts, timers, a hard real-time scheduler,
preemptive multitasking, CNN/streaming models, shared L2, DDR/scatter-gather
DMA, or a second stream. It does not change Phase 1–11 maps or ABIs.

## Repository layout

| Area | Phase 12 responsibility |
| --- | --- |
| `scripts/gen_ecg_data.py` | Bake the PhysioNet segment |
| `software/benchmarks/workload_ecg.c` | The heterogeneous pipeline |
| `verification/soc/tb_aster_workload_coherent.cpp` | Actual-core capture harness |
| `scripts/workload_reference.py` | Independent pipeline oracle |
| `scripts/phase12_study.py` | Repeat study and audit |
| `scripts/run_phase12_mem.py` | Guarded physical capture |
| `docs/results/phase12/` | Completed, audited evidence |
