# v1.2: Parameterized NPU geometry

Status: **scoped** (not started).
Baseline: Aster v1.1 at `4731be2`.
Origin: the Phase 14.5 accelerator-dimensions stage, deferred from Phase 14.

This document scopes the last unmeasured README research question — *"How do
accelerator dimensions affect utilization, area and performance?"* — as a
bounded v1.2 sub-phase. It is a **feature phase**, not a parameter sweep: the
frozen v1.0/v1.1 NPU is fixed 4×4 in RTL *and* software, so exposing 2×2 and
8×8 requires both to change and to be re-verified.

## Why it is not a parameter sweep

`aster_int8_array` is already parameterized (`ROWS`/`COLS`), but the rest of the
NPU is not:

| Location | Hardcoded 4×4 |
| --- | --- |
| `aster_npu_engine` | `load_index < 4` (A and B), `output_index < 16`, `output_index >> 2`, `& 3`, the `+3/+2/+1` row/col masks, `for i < 4` loops, `output_index[3:0]`, the GEMM stride `* 4` |
| engine widths | `load_index`/`output_index [4:0]`, `a_values`/`b_values [0:3]`, `array_a`/`array_b [0:3]`, masks `[3:0]`, `array_results [0:15]` |
| `aster_npu_regs` | `FEATURES = 0x0000_001f` (the 4×4 geometry code) |
| software | `aster_npu.c` and `software/benchmarks/xe_kernels.c` (im2col GEMM) assume a 4×4 tile |

The engine changes are mechanical once `ROWS`/`COLS` and the derived widths are
threaded through, but they touch the tile iteration, load/output indexing and
mask generation, so the whole accelerator must be re-verified.

## Scope

- Parameterize `ROWS`, `COLS ∈ {2, 4, 8}` in `aster_npu_engine` and thread them
  through `aster_npu_regs`, `aster_coherent_soc`, `aster_pynq_linux` and
  `aster_linux_ip`.
- Default stays `ROWS = COLS = 4`, so the frozen v1.0/v1.1 NPU is unchanged and
  `scripts/freeze_interfaces.py` stays green.
- Generalize the software driver and the im2col GEMM to the runtime geometry.
- Report the geometry so software can adapt.

## Register contract

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x054` | `FEATURES` | R | unchanged bits; bit 0 set only when `ROWS == COLS == 4` |
| `0x058` | `GEOMETRY` | R | `{8'b0, ROWS[7:0], COLS[7:0]}` |

`0x058` is a new read-only register; it is the only register-map change and is
documented as a v1.2 addition. Every other NPU register keeps its v1.0 meaning.

## Verification and acceptance gates

- [ ] Contract frozen.
- [ ] Default 4×4 configuration bit-identical to v1.1 (the freeze guard green).
- [ ] Unit scoreboard for 2×2, 4×4 and 8×8 tile/edge/accumulator behavior.
- [ ] Engine runtime, driver and im2col software generalized and re-verified at
      all three geometries against the independent oracle.
- [ ] The Phase 9/10/11 NPU studies re-run at 4×4 to prove no regression.
- [ ] A geometry study measuring utilization, cycles and area at 2×2/4×4/8×8.
- [ ] Routed overlays and, if it fits, a physical capture per geometry.
- [ ] Self-contained closeout bundle and read-only audit.

## Open questions to resolve in the contract

- Whether 8×8 fits the xc7z020 (the 4×4 array is 16 PEs; 8×8 is 64) — a
  resource check before committing to the axis.
- Whether the im2col lowering or a direct-convolution lowering is the right
  comparison at 2×2 (the Phase 14 result showed im2col overhead dominates).
- Whether to keep `GEOMETRY` in the NPU page or report it through the bridge.

## Explicit non-goals

No change to the default 4×4 behavior, no new datatype or sparsity support, no
tiling/descriptor-ring redesign, and no change to the frozen v1.0/v1.1
interfaces beyond the additive `GEOMETRY` register.

## Recommendation

Do this as a bounded sub-phase with the full discipline (contract → RTL →
software → re-verification → study → overlay → physical → closeout), exactly as
v1.1. It is the last README research question without a measured answer; the
other nine are answered by Phases 10–14 and v1.1.
