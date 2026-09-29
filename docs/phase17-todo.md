# Phase 17 TODO — correct the v1 baseline and freeze the v2 contract

Status: **in progress**. Revised 29 September 2026 (see the
[plan revision](phase17-plus.md#7-plan-revision--29-september-2026)). Phase 17
is deliberately lean: it corrects what v1 reports and fixes the v2 contract; it
does not polish a design that v2 replaces. No v2 performance RTL starts until the
exit review below.

The v1 RTL and historical records remain the reference. New measurement semantics
must not silently reinterpret saved v10 results. Use a new record version for
corrected semantics and keep old parsers/fixtures working. Immutable closeout
bundles (`docs/results/*/closeout-*`) are never edited; corrections go in live
documents that link to them.

## Done

- [x] **P17-A1 — Specify v11 record semantics** (`9e81ebe`). One field per
  requester for DMA and NPU bytes; cumulative NPU busy/compute cycles and job
  counts; exact increment rules. See [`asterbench-v11.md`](asterbench-v11.md).
- [x] **P17-A2 — Trace DMA requester identity** (`9e81ebe`). The device arbiter
  carries a DMA-owner tag and the DMA byte event is qualified by it, while the
  generic `device_store_commit` stays available for NPU coherence/reservation
  logic. Known remaining gap: device events 5–9 still include NPU traffic, and
  with `ENABLE_L2=1` the DMA byte event reads zero; v11 therefore aggregates
  engine totals from per-job status registers instead of these events.
- [x] **P17-A3 — Independent counter scoreboards** (`b40066b`). DMA-only service
  is checked by `tb_aster_dma_soc`/`dma-runtime`; NPU-only Conv2D by the Phase 17
  matrix audit; the mixed DMA/NPU runtime compares DMA-request bytes with
  DMA-attributed events and sums nine NPU jobs against an independent shape
  oracle; the arbiter test holds the requester tag across a stall. Verified with
  the full `make check` (204 PASS, 257 host tests).

Same-top diagnostic (default RTL configuration: 31.25 MHz, L1 on, asynchronous
zero-wait memory, two-hart coherent all-engine top, 32×32/K=5 input; all
checksums `0x07df8000`). These are v10 diagnostics in ignored `build/`, not
retained evidence:

| Method | Cycles | Ratio vs coherent scalar |
| --- | ---: | ---: |
| Coherent scalar | 9,586,170 | 1.00× |
| DOT8 | 9,786,108 | 0.98× |
| NPU | 4,636,733 | 2.07× |

The old 1.24× headline used a scalar run on `aster_minimal`. The v10 NPU
`accelerator_cycles` field reports only the last of four jobs (4,900 cycles).

## Open, in order

- [x] **P17-H — Housekeeping.** Verified with the full `make check` (207 PASS,
  257 host tests).
  - [x] Workload simulators are built in configuration-tagged directories, so a
    changed memory/cache/L2/NPU configuration never reuses a stale binary.
  - [x] `make check` covers `device-arbiter`, `dma-counters`, and `l2-unit`.
  - [x] Stale phase statuses, broken links, subsystem READMEs, the ASIC README,
    `docs/toolchain.md`, and `docs/memory.md` examples are corrected (the old
    `memguard.sh -- run_asic.py` example deadlocked on the nested lock); FPGA
    results are not called "silicon"; `docs/l2.md` matches the RTL.
  - [x] Stray tracked files are removed.
- [ ] **P17-A4 — v11 record support and aggregation.**
  - [ ] Coherent workloads (reduction, Conv2D engines, ECG, CIFAR) emit v11,
    summing DMA `bytes_done`/job cycles and NPU bytes/tiles/job/compute cycles
    over every job, and reading each hart's DOT8 event bank after freeze.
  - [ ] Strict Python validator and C++ parser share one valid/malformed
    mutation corpus that rejects omitted, duplicated, unknown, misattributed,
    and non-cumulative engine fields.
  - [ ] v2–v10 validators and fixtures still pass; capture/study scripts that
    only understand v10 either accept v11 or fail with an explicit v10-only
    message.
  - [ ] The same-top matrix audit and workload oracles run on v11 records;
    `make check` passes.
- [ ] **P17-A5/A6 — One retained same-top v1 baseline.** Scalar, multicore,
  DOT8, and NPU for Conv2D, reduction, MNIST MLP, ECG, and CIFAR on the
  all-engine coherent SoC, captured under the physical synchronous one-wait
  memory model and, labelled as an idealization, the zero-wait model. One
  capture plus one determinism repeat per configuration. Raw records, firmware
  hashes, configuration, toolchain, and oracle outputs are retained under
  `docs/results/phase17/` with an audit that rejects a mismatched top, clock,
  memory mode, counter, or source hash. `aster_minimal` stays a separately
  named data point.
- [ ] **P17-B — CoreMark labelling.** Label the current one-iteration run as a
  fixed-iteration CRC correctness check wherever it appears; no standard score
  is claimed for PicoRV32. A valid CoreMark score (real timer, at least ten
  seconds) is an Aster-core deliverable in Phase 18/21. Document the Dhrystone
  adaptation.
- [ ] **P17-C — Correct Phase 15/16 reporting, without new ASIC runs.**
  - [ ] Phase 16 documents state: per-corner setup/hold from the `p16-f2`
    signoff STA (hold fails at `nom_ff`, `max_tt`, `max_ff`); Fmax per corner
    from that STA (`nom_tt` 40.96 MHz, `max_ss` 20.77 MHz at the 47 ns SDC);
    power with its corner (typical 62.1 mW; 70.1 mW is `max_ff`); LVS as a
    failing netlist mismatch in which `vccd1` resolves to `vssd1`; 106 routing
    DRC; 85,996 max-slew and 5,443 max-capacitance violations; Tier 2 as one
    full-size and one reduced-shape pass of eight mandatory workloads; SDF
    annotation not demonstrated; ASIC workload cycles not captured on the ASIC
    configuration.
  - [ ] Phase 15 documents record its 2,262 max-slew and 274 max-capacitance
    violations and name the corner of its power figure.
  - [ ] `audit_phase15.py` and `audit_phase16.py` enforce their contracts:
    every corner's setup/hold, route DRC, LVS error count, electrical
    violations, and required gate-level runs, with checks that can fail. The
    expected outcome is that Phase 16 reports **incomplete**.
  - [ ] Future closeouts include `asic/` in the hashed source state.
- [ ] **P17-D — Memory and area point (owner decision).** Present options with
  numbers: full map on a larger die, smaller on-chip SRAM with tiled workloads,
  or an external-memory interface; macros versus latch/flop RAM per array. Freeze
  one option with a die-area budget.
- [ ] **P17-E — Freeze the v2 contract and the CPU specification.** Approve the
  100 MHz FPGA/SKY130 targets and required corners, NPU utilization and speedup
  goals including the N=1 mapping, the CPU cycle target, resource/die limits,
  energy method, workload matrix, gate-level method, and evidence manifest.
  Write `docs/cpu.md` for the Aster core (ISA, pipeline, interfaces, traps,
  verification layers, timing gates) as outlined in
  [phase17-plus.md section 6](phase17-plus.md#6-phase-17-sequence).
- [ ] **P17-F — Live documentation aligned.** README, architecture status,
  subsystem READMEs, and the phase index describe the current state, and
  historical claims are labelled with their original configuration.

## Removed from the first draft

- Three fresh RTL captures of each v1 diagnostic (deterministic simulation needs
  one capture plus a determinism repeat).
- Re-deriving Phase 16 Fmax/power with new flow runs (P17-C corrects the
  documents and audits from the retained reports instead).
- A valid CoreMark score for PicoRV32 (moved to the Aster core).

## Phase 17 exit gate

Phase 17 is complete when v11 records validate in Python and C++ against a
shared mutation corpus and every coherent workload emits them; the retained v1
baseline exists and its audit rejects configuration or source drift; the
Phase 15/16 documents and audits report their true status; and the memory/area
point, v2 targets, and Aster core specification are approved. Phase 18 RTL
starts after that review.
