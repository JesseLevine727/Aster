# Aster v1.0 freeze closeout

Frozen revision: `b050a81e6586f586a8a14e9bb642710cc2d19ebc`
Annotated tag: `v1.0`
Target: all-engine coherent RV32IMA (harts=2, L1, DMA, Xasterdot8, 4x4 NPU)
Clock: 31.25 MHz

## What v1.0 is

Aster v1.0 is 2× PicoRV32 RV32IMA with private L1 I/D caches, MSI-like
coherence over shared RAM, a DMA engine, the Xasterdot8 packed-INT8 instruction,
a 4×4 INT8 NPU, UART, performance counters, a machine timer and a per-hart
interrupt controller. The authoritative contract for every address, register,
ABI and instruction encoding is `spec/v1.md`.

Shared L2 is deliberately **not** in v1.0; it is a v2 / Phase 14 design-space
axis, recorded in `spec/phase13.md` and `spec/known-limitations.md`.

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Frozen specification | `spec/v1.md`, `spec/phase13.md`, `spec/known-limitations.md`, `spec/phase14-plan.md`, `spec/architecture.md` |
| 2 | Interface freeze guard | `interfaces/freeze-interfaces.log` |
| 3 | Frozen clean check | `verification/make-check.log` |
| 4 | Frozen source and tag | `source/source-state.json` |

## Results

- `scripts/freeze_interfaces.py` confirms 23 frozen address decodes, ABI
  constants and the Xasterdot8 encoding against the RTL, including the atomic
  fabric's permitted-page set.
- The frozen `make check` passes with 200 PASS records, no failures and 253
  host tests.
- The annotated `v1.0` tag points at the frozen revision, and
  `source/source-state.json` records the implementation file hashes and the
  spec hashes.

## Audit

```
python3 scripts/audit_v1.py docs/results/phase13/closeout-v1.0 --current
```

## Next

- Phase 14 sweeps the frozen knobs (`HART_COUNT`, cache geometry,
  `MEMORY_WAIT_CYCLES`, NPU geometry, engine choice) per
  `spec/phase14-plan.md`.
- Phase 15 takes a minimal configuration through SKY130.
