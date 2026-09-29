# Phase 17 TODO — trustworthy baseline and performance contract

Status: **in progress**. Phase 17 starts with evidence and measurement integrity;
no v2 performance result is accepted until the items below are closed.

The v1 RTL and historical records remain the reference. New measurement semantics
must not silently reinterpret saved v10 results. Use a new record version for
corrected semantics and keep old parsers/fixtures working.

## Current milestone: P17-A — same-top, correctly attributed baseline

Progress: **P17-A1 is specified** in the
[AsterBench v11 contract](asterbench-v11.md). P17-A2 now propagates a DMA-owner
tag from the device arbiter and qualifies the DMA byte event with that tag; the
generic device-store event remains generic for coherence/reservation use. The
unit arbiter test checks CPU/DMA/NPU ownership, and the actual-core NPU runtime
compares attributed DMA bytes against accepted DMA payloads in a mixed run.

The firmware also contains a coherent scalar Conv2D path; Phase 17 exposes it as
`CONV_ENGINE=scalar_coh` and adds `make phase17-conv-matrix` to compare it with
DOT8 and NPU on the same coherent top. This remains a diagnostic v10 matrix; the
accepted baseline remains blocked on v11 implementation and evidence below.

The first local diagnostic used the same 32×32/K=5 input and the default RTL
configuration (31.25 MHz, L1 on, async/zero-wait memory, 4×16 L1, two-hart
coherent all-engine top). All three checksums were `0x07df8000`:

| Method | Cycles | Ratio vs coherent scalar |
| --- | ---: | ---: |
| Coherent scalar | 9,586,170 | 1.00× |
| DOT8 | 9,786,108 | 0.98× |
| NPU | 4,636,733 | 2.07× |

This already differs from the old 1.24× headline because that scalar number came
from `aster_minimal`. These records are v10 diagnostics in ignored `build/`; do
not treat them as audited evidence. The first capture, before P17-A2, counted
12,544 NPU output bytes as DMA. The post-A2 NPU-only rerun reports zero
`dma_bytes`; historical bundles remain unchanged. The v10 NPU
`accelerator_cycles` field still reports only the last job's 4,900 compute
cycles. P17-A3–A6 must complete before this becomes the accepted Phase 17
baseline.

- [x] **P17-A1 — Specify v11 record semantics.** Define one field per requester
  for DMA bytes and NPU bytes; define cumulative NPU busy/compute cycles and job
  count; document exactly which accepted memory events increment each field. See
  [`docs/asterbench-v11.md`](asterbench-v11.md).
- [x] **P17-A2 — Trace DMA requester identity.** Carry a DMA-owner tag through
  the coherent device arbiter; qualify DMA payload-byte events with it while
  preserving generic `device_store_commit` for NPU coherence/reservation logic.
  Old records remain unchanged; the integrated runtime regression checks that
  NPU output stores do not inflate DMA bytes.
- [ ] **P17-A3 — Add independent counter scoreboards.** Cover DMA-only, NPU-only,
  and combined jobs; stalls, partial words, zero-length jobs, aborts, resets, and
  several jobs inside one measurement window. Check exact byte and cycle totals.
- [ ] **P17-A4 — Add v11 C/Python/C++ record support and aggregation.** Sum DMA
  `bytes_done`/job cycles and NPU byte/cycle/tile snapshots over all jobs; read
  DOT8's per-hart event bank after freeze. Preserve v2–v10 validators and fixtures.
  Mutation tests must reject omitted, duplicated, misattributed, and
  non-cumulative engine fields.
- [ ] **P17-A5 — Establish same-top compute comparisons.** Run scalar, multicore,
  DOT8, and NPU implementations on the same all-engine SoC, with the same input,
  precision, memory timing, cache policy, clock, and result oracle. Keep
  `aster_minimal` measurements as a separately named baseline.
- [ ] **P17-A6 — Capture and bind raw baselines.** Retain raw records, firmware
  images, source/toolchain/configuration hashes, repeated captures, and the exact
  independent oracle outputs. Do not use ignored `build/` files as closeout
  evidence.

### P17-A acceptance

- A DMA-only run has zero NPU traffic/cycles; an NPU-only run has zero DMA bytes;
  a combined run attributes each accepted byte to exactly one requester.
- The cumulative NPU values equal the sum of all jobs, not just the last job.
- Scalar/multicore/DOT8/NPU comparisons use one declared coherent SoC
  configuration and identical logical work.
- Independent RTL scoreboards and host record validators agree on all counters;
  existing v2–v10 records still parse with their original semantics.
- Three fresh RTL captures reproduce outputs and counters; no run is discarded
  because it is slow.

## Remaining Phase 17 work

- [ ] **P17-B — Valid CPU workload timing.** Keep short CoreMark CRC tests as
  correctness checks; produce a standard CoreMark score only with a real timer,
  the required run duration, frozen compiler flags, and repeatable board timing.
  Document the Dhrystone adaptation and do not claim a standard rate for it.
- [ ] **P17-C — Reconcile Phase 16 PPA.** Recompute Fmax using the SDC associated
  with each slack report; reconcile `power.rpt` against `metrics.csv`; identify
  power activity assumptions; regenerate workload times/energy only from
  comparable source records. Keep failed timing/DRC/LVS/Tier 2 gates visible.
- [ ] **P17-D — Choose memory and area point.** Compare macro count, capacity,
  floorplan/routing margin, and workload fit for full memory, tiled smaller
  memory, and any proposed external-memory interface. Freeze one option with a
  die-area budget; do not silently shrink the map.
- [ ] **P17-E — Freeze v2 target contract.** Approve 100 MHz FPGA and SKY130
  targets/required corners, NPU utilization and speedup goals, CPU baseline,
  resource/die limits, energy method, fixed workload matrix, and source/evidence
  manifest requirements.
- [ ] **P17-F — Align live documentation.** Update the root README, architecture
  status, subsystem READMEs, and phase index. Preserve immutable bundles and label
  historical claims with their original source/configuration.

### Phase 17 exit gate

Phase 17 is complete only when P17-A through P17-F have independently auditable
evidence, the v2 numerical targets and memory/die point are frozen, and a fresh
read-only audit rejects source/configuration drift and any failed gate. No
full-chip v2 feature implementation starts before that exit review.
