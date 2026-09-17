# Phase 13: Freeze Aster v1

Status: **in progress**.
Baseline: pushed Phase 12.6 closeout `ca43eca`.

Phase 13 stops feature development and stabilizes the architecture, software,
documentation and tests into a tagged **Aster v1.0**. It adds no architectural
features. Its job is to turn the accumulated phase contracts into one
authoritative, frozen specification, prove the frozen revision reproduces from a
clean checkout, and hand a stable baseline to the design-space and ASIC phases.

## Scope decision: shared L2 is not in v1.0

The README "likely freeze point" lists a shared L2. Phase 6 deliberately
deferred it ("There is no directory or shared L2"), and the design philosophy
forbids bundling a major unverified subsystem into a stabilization step. v1.0
therefore freezes the verified **MSI-like coherence over shared RAM** baseline
and records shared L2 as a deliberate v2 / Phase 14 design-space axis rather
than an unmet requirement.

Resolved v1.0 scope:

| Subsystem | v1.0 |
| --- | --- |
| 2× PicoRV32 RV32IMA, private L1 I/D | in |
| MSI-like two-core coherence over shared RAM | in |
| DMA engine (coherent, polled completion) | in |
| Xasterdot8 packed-INT8 instruction | in |
| 4×4 INT8 NPU | in |
| UART, performance counters, machine timer, interrupt controller | in |
| Shared L2 cache | **out (v2 / Phase 14)** |
| Interrupt priority, nesting, preemption | **out** |
| Privileged/CSR trap interface, MMU, OS | **out** |
| RTL frequency optimization | **out (Phase 14/16)** |
| SKY130 / OpenROAD physical design | **out (Phase 15/16)** |

## Deliverables

1. `docs/phase13.md` — this freeze contract.
2. `docs/v1.md` — the single authoritative frozen v1 specification: memory map,
   peripheral ABIs, ISA, core/fabric/coherence contract, interrupt model,
   clock/reset/lifecycle, build parameters and version identity.
3. A reconciled, explicitly frozen peripheral map covering both the legacy
   `aster_minimal` map and the coherent-top map, including the coherent
   performance-counter offsets that differ from the legacy header.
4. A frozen version/ABI table (bridge version, per-peripheral ABI constants).
5. `scripts/freeze_interfaces.py` — extracts decode ranges and ABI constants
   from the RTL and diffs them against `docs/v1.md`, so post-freeze drift fails.
6. A clean-checkout `make check` from the frozen revision with the retained log.
7. Consolidated documentation: README current status, `architecture.md`
   decisions, and a `docs/known-limitations.md`.
8. An annotated `v1.0` tag and a freeze manifest hashing RTL, software, docs and
   FPGA artifacts.
9. `scripts/audit_v1.py` — the read-only freeze audit and a self-contained
   closeout bundle.
10. `docs/phase14-plan.md` — the design-space knob inventory mapped to the
    README research questions, plus the minimal configuration for Phase 15.

## Verification and acceptance gates

- [ ] Contract frozen.
- [ ] `docs/v1.md` covers every implemented interface and matches the RTL.
- [ ] `scripts/freeze_interfaces.py` passes against the frozen revision.
- [ ] Clean-checkout `make check` passes from the tagged revision.
- [ ] Documentation states the v1 scope, resolved decisions and known
      limitations with no "future work" ambiguity.
- [ ] Annotated `v1.0` tag and freeze manifest retained.
- [ ] Self-contained closeout bundle and read-only audit.

## Explicit non-goals

This phase does not add shared L2, interrupt priority or nesting, a privileged
trap interface, new instructions, new peripherals, RTL frequency work, or any
change to the frozen v1 register maps and ABIs. Feature work resumes only after
the freeze, on a v2 branch or the Phase 14 parameter sweeps.
