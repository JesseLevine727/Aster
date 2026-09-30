# Phase 17 TODO — correct the v1 baseline and freeze the v2 contract

Status: **complete (29 September 2026).** Every item below is closed and each
exit-gate clause was re-checked against the evidence. The first completion
claim was premature (MNIST still emitted only v9, and P17-F was ticked before two
READMEs were reviewed); both were closed, verified with the full `make check`
(210 PASS, 270 host tests) and the retained-baseline audit, before this status
was set. Phase 18 (the Aster core) is next. Revised 29 September 2026
(see the
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
- [x] **P17-A3 — Independent counter scoreboards** (`cefc504`). DMA-only service
  is checked by `tb_aster_dma_soc`/`dma-runtime`; NPU-only Conv2D by the Phase 17
  matrix audit; the mixed DMA/NPU runtime compares DMA-request bytes with
  DMA-attributed events and sums nine NPU jobs against an independent shape
  oracle; the arbiter test holds the requester tag across a stall. Verified with
  the full `make check` (204 PASS, 257 host tests).

- [x] **P17-A4 — v11 record support and aggregation** (`f025a29`; MNIST added
  after the exit review). Verified with the full
  `make check` (207 PASS, 260 host tests).
  - [x] Coherent workloads (reduction, Conv2D engines, ECG, CIFAR) emit v11,
    summing DMA `bytes_done`/job cycles and NPU bytes/tiles/job/compute cycles
    over every job, and reading each hart's DOT8 event bank after freeze.
  - [x] The strict Python validator and C++ parser share one valid/malformed
    mutation corpus (it caught the C++ parser accepting a trailing comma).
  - [x] v2–v10 validators and fixtures still pass; the six historical v10
    capture/study drivers reject a v11 record with an explicit message.
  - [x] The same-top matrix audit checks NPU totals against an independent
    cumulative shape oracle, so a last-job-only total fails.
  - [x] The MNIST MLP also emits a v11 summary over its 32 per-image windows
    ([summed-window records](asterbench-v11.md#summed-window-records)); the
    baseline audit requires its cycles to equal the sum of the v9 windows, its
    checksum to match the model's reference outputs, and its NPU totals to
    cover both layers. Two findings: the v9 `h0_retired`/`h1_retired` fields
    have always held counter 2 (memory transactions), not retired
    instructions; and the NPU's own job time depends on the CPU's polling code
    (the same fc2 job took 5,956 then 5,756 cycles after an unrelated code-layout
    change), because every poll competes for the one serialized memory path.

Same-top diagnostic (default RTL configuration: 31.25 MHz, L1 on, asynchronous
zero-wait memory, two-hart coherent all-engine top, 32×32/K=5 input; all
checksums `0x07df8000`). These are development records in ignored `build/`, not
retained evidence (that is P17-A5/A6):

| Method | Cycles (v11 firmware) | Ratio vs coherent scalar | Cycles (v10 firmware) |
| --- | ---: | ---: | ---: |
| Coherent scalar | 9,586,170 | 1.00× | 9,586,170 |
| DOT8 | 10,016,476 | 0.96× | 9,786,108 |
| NPU | 4,644,700 | 2.06× | 4,636,733 |

The old 1.24× headline used a scalar run on `aster_minimal`. The DOT8 build
retires exactly the same 1,145,218 instructions under both firmware versions, so
its 2.4% cycle difference is a code-layout timing effect (mechanism not yet
isolated); the NPU difference is the per-job status reads now inside the window.

What the v11 records show:

- NPU Conv2D: 4 jobs, 784 tiles, 98,000 bytes read, 12,544 bytes written, and
  19,600 array-step cycles — exactly the shape oracle. The NPU is busy for
  1,345,796 cycles, but its array computes in only 0.42% of the workload's
  cycles, and with N=1 only 4 of 16 PEs do useful work (25% when active).
- CIFAR: 60 NPU jobs writing 315,680 bytes — the amount v10 misreported as
  `dma_bytes` — with the array computing in 274,800 of 28.1 M NPU-busy cycles.
- ECG: 16 DMA jobs moving exactly 1,024 bytes, DOT8 on hart 1 only, and 16 NPU
  jobs writing 192 bytes.

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
- [x] **P17-A5/A6 — One retained same-top v1 baseline.** Scalar, multicore,
  DOT8, and NPU for Conv2D, reduction, MNIST MLP, ECG, and CIFAR on the
  all-engine coherent SoC, captured under the physical synchronous one-wait
  memory model and, labelled as an idealization, the zero-wait model. One
  capture plus one determinism repeat per configuration. Raw records, firmware
  hashes, configuration, toolchain, and oracle outputs are retained under
  `docs/results/phase17/` with an audit that rejects a mismatched top, clock,
  memory mode, counter, or source hash. `aster_minimal` stays a separately
  named data point. Retained as
  [`results/phase17/baseline-56067a15815a`](results/phase17/baseline-56067a15815a/README.md): 17 captures × 2
  memory models × 2 byte-identical repeats, audited by
  `scripts/audit_phase17_baseline.py` (run by `make check`); mutation tests show
  the audit rejects a changed top, clock (including one uniformly wrong for
  every capture), memory mode, DMA attribution, last-job-only NPU total, MNIST
  summary top, cycle or transaction sum, or checksum, malformed firmware hash,
  repeat, or source hash. The recorded source hash is always checked against
  the recorded revision; `--current` additionally requires the working tree to
  equal that revision, so it fails by design once any later commit touches a
  hashed path (documentation inside `rtl/` and `scripts/` included) — `make
  check` runs the plain mode. Firmware hashes are provenance: the images are
  build products outside the bundle, bound through the source hash. Cross-check: the `sync1` MNIST
  NPU inference of the v9-only firmware (462,953 cycles per image, 4.46×; retained
  at `dfcd8fc`) equalled the Phase 11 PYNQ-Z1 board capture exactly. The current
  firmware, which adds the v11 summary, is a different binary; its NPU run is
  faster because of the CPU-polling contention recorded under A4.
- [x] **P17-B — CoreMark labelling.** Label the current one-iteration run as a
  fixed-iteration CRC correctness check wherever it appears; no standard score
  is claimed for PicoRV32. A valid CoreMark score (real timer, at least ten
  seconds) is an Aster-core deliverable in Phase 18/21. Document the Dhrystone
  adaptation. Done in `docs/workloads.md`, `docs/phase16.md`, and the retained
  baseline's README.
- [x] **P17-C — Correct Phase 15/16 reporting, without new ASIC runs.**
  - [x] Phase 16 documents state: per-corner setup/hold from the `p16-f2`
    signoff STA (hold fails at `nom_ff`, `max_tt`, `max_ff`); Fmax per corner
    from that STA (`nom_tt` ≈39.2 MHz, `max_ss` ≈20.7 MHz at the 47 ns SDC,
    including the SRAM macro's half-cycle path, which OpenSTA's
    `report_clock_min_period` figures of 40.96 and 20.77 MHz leave out — a
    correction from the Phase 18 review; see
    `docs/results/phase16/p16-f2-sta-evidence.md`);
    power with its corner (typical 62.1 mW; 70.1 mW is `max_ff`); LVS as a
    failing netlist mismatch in which `vccd1` resolves to `vssd1`; 106 routing
    DRC; 85,996 max-slew and 5,443 max-capacitance violations; Tier 2 as one
    full-size and one reduced-shape pass of eight mandatory workloads; SDF
    annotation not demonstrated; ASIC workload cycles not captured on the ASIC
    configuration. (`docs/phase16.md` Results; correction notes on the report,
    PPA, Tier 2, and cleanup pages.)
  - [x] Phase 15 documents record its 2,262 max-slew and 274 max-capacitance
    violations and name the corner of its power figure.
  - [x] `audit_phase15.py` and `audit_phase16.py` evaluate every contract gate
    from counts and evidence (shared `scripts/asic_contract.py`) instead of
    substring checks, and report electrical violations and route DRC as
    information where the historical contract did not gate them. Result:
    **Phase 16 incomplete (4/11 gates)**, and **Phase 15 incomplete (6/8)**: its
    SDF annotation left 36,809 paths unmatched and its SPEF is not hash-bound.
  - [x] Future closeouts include `asic/` in the hashed source state (the
    Phase 17 baseline audit hashes `asic/` with the rest of the source).
- [x] **P17-D — Memory and area point (owner decision).** Present options with
  numbers: full map on a larger die, smaller on-chip SRAM with tiled workloads,
  or an external-memory interface; macros versus latch/flop RAM per array. Freeze
  one option with a die-area budget. The decision brief is
  [`phase17-memory.md`](phase17-memory.md). **Decided 29 September 2026:** a 96 KiB
  host-loaded unified SRAM in independent banks; the die budget is set from the
  Phase 18 block areas plus ≈13.7 mm² of macros.
- [x] **P17-E — Freeze the v2 contract and the CPU specification.** Approve the
  100 MHz FPGA/SKY130 targets and required corners, NPU utilization and speedup
  goals including the N=1 mapping, the CPU cycle target, resource/die limits,
  energy method, workload matrix, gate-level method, and evidence manifest.
  Write `docs/cpu.md` for the Aster core (ISA, pipeline, interfaces, traps,
  verification layers, timing gates) as outlined in
  [phase17-plus.md section 6](phase17-plus.md#6-phase-17-sequence). **Approved 29
  September 2026:** the section 2 targets are frozen and [`cpu.md`](cpu.md) is
  the Phase 18 contract.
- [x] **P17-F — Live documentation aligned.** README, architecture status,
  subsystem READMEs, and the phase index describe the current state, and
  historical claims are labelled with their original configuration. Done across
  P17-H, the plan revision, and P17-C, then `fpga/pynq_z1/README.md` (current
  status section), `software/benchmarks/README.md` (record-version index),
  `rtl/accelerator/README.md`, and the verification/runtime guides; 0 broken
  links in 63 live documents. The historical Phase 9 audit, which failed in its
  plain mode since the tree moved past Phase 9, was fixed to match the others.

## Removed from the first draft

- Three fresh RTL captures of each v1 diagnostic (deterministic simulation needs
  one capture plus a determinism repeat).
- Re-deriving Phase 16 Fmax/power with new flow runs (P17-C corrects the
  documents and audits from the retained reports instead).
- A valid CoreMark score for PicoRV32 (moved to the Aster core).

## Phase 17 exit gate

Phase 17 is complete when v11 records validate in Python and C++ against a
shared mutation corpus and every coherent-SoC workload in the AsterBench catalog
(reduction, Conv2D engines, ECG, CIFAR, and MNIST) emits them — the
phase-specific v4–v8 study benches keep their historical formats, which report
one engine job per record and so never had the last-job problem; the retained v1
baseline exists and its audit rejects configuration or source drift; the
Phase 15/16 documents and audits report their true status; and the memory/area
point, v2 targets, and Aster core specification are approved. Phase 18 RTL
starts after that review.
