# Phase 10: CPU vs multicore vs ISA vs NPU

Status: **in progress** (simulation scope).
Baseline: pushed Phase 9 closeout `2493435` /
`docs/results/phase9/closeout-2493435/`.
The [README roadmap](../README.md#phase-10--cpu-vs-multicore-vs-isa-vs-npu) defines
this phase as running identical kernels through all execution paths and
measuring cycles, latency, instructions, cache behavior, memory traffic,
accelerator utilization, FPGA resources and maximum clock.

Phase 10 adds **no new compute engine**. It reuses the four engines already
verified in Phases 4–9 and measures where each one wins or loses on the same
signed-INT8 kernels. This document is the Phase 10 contract. It preserves every
Phase 1–9 map, ABI, source and evidence artifact; no earlier closeout is
modified. Pinned PicoRV32 remains unchanged and vendor RTL is never edited.

## Scope and design principles

The experiment compares four execution paths on three kernels:

| Path | Engine | Source of record |
| --- | --- | --- |
| `scalar` | one RV32IMA hart, scalar C | Phase 9 `aster_npu_scalar_gemm` |
| `multicore` | two RV32IMA harts, coherent shared RAM | Phase 5/6 runtime + mailboxes |
| `dot8` | one hart, Xasterdot8 packed INT8 | Phase 8 `aster_pcpi_dot8` |
| `npu` | one hart driving the 4×4 INT8 array | Phase 9 `aster_npu_engine` |

Correctness is the exit criterion. Performance is a measurement, not an
assumption: a path that loses to the scalar baseline remains a valid retained
result. No result may be discarded because its ratio is below one.

The first accepted study is **simulation-only**. FPGA resource utilization,
maximum clock, post-route timing and physical warm-boot speedup require the
Pynq-Z1 board and are deferred to the board-backed portion of Phase 10; they
are not inferred from simulation.

## Arithmetic contract

All kernels use signed two's-complement INT8 inputs and 32-bit two's-complement
outputs. Every accumulation boundary is modulo 2^32. No saturation, hidden
accumulator, floating point, or compiler extension is permitted.

Inputs are read as `int8_t`. The scalar, DOT8 and NPU references must agree
bit-for-bit on every output byte. The independent Python oracle recomputes every
output with widened signed Python integers and masks the final pattern to 32
bits.

The K limit is 1024, matching the Phase 9 descriptor bound. For the contracted
maximum, `1024 × 128 × 128 < 2^31`, but the modulo-2^32 rule is still mandatory
so the RTL, C model and oracle stay unambiguous.

## Kernel contract

Let `a`, `b`/`c`, and `y`/`C` be the shared logical inputs and output. Logical
data is identical across all four paths for a given capture; only the method's
own setup, scratch and traversal differ, and all of that is inside the measured
window.

### Dot product

```text
y[0] = sum(k = 0 .. K-1)  signed(a[k]) * signed(b[k])
```

`K` is in `0..1024`. `K = 0` yields `y[0] = 0` without reading `a` or `b`.

### FIR

```text
y[r] = sum(k = 0 .. K-1)  signed(a[r+k]) * signed(c[k])     r = 0 .. TAPS-1
```

`TAPS` is fixed at 8 for the first study. The input signal `a` has
`K + TAPS - 1` valid bytes. `K = 0` yields all-zero outputs without reading `a`
or `c`.

### GEMM

```text
C[m][n] = sum(k = 0 .. K-1)  signed(A[m][k]) * signed(B[k][n])
```

`M`, `N`, `K` are in `0..1024`. `M = 0` or `N = 0` is a successful no-op.
`K = 0` writes zero to every `C` element without reading `A` or `B`.

## Layout and bounds

The shared buffers live wholly in coherent shared RAM
`0x10000000..0x10008000`. A/B/C bases and byte strides follow the Phase 9
descriptor convention:

```text
A element (i,k) = A_BASE + i*A_STRIDE + k
B element (k,j) = B_BASE + k*B_STRIDE + j
C element (i,j) = C_BASE + i*C_STRIDE + 4*j
```

The scalar and DOT8 paths consume the same byte-stride layout so no path gets a
free contiguity advantage. Each capture records actual base addresses, strides,
allocations, and placement offsets, plus 64-byte-aligned guard regions that must
retain their poison value.

The first study uses a single fixed stride policy per kernel so shapes remain
comparable, plus one deliberately unaligned placement variant (A+1, B+2, C+3)
to expose alignment sensitivity.

## Engine mappings

| Kernel | scalar | multicore (2 harts) | dot8 | npu |
| --- | --- | --- | --- | --- |
| dot | direct loop | split K into two contiguous halves; publish partial sums; hart 0 combines modulo 2^32 | packed 4-lane loop with scalar tail | `M=1, N=1, K` GEMM descriptor |
| FIR | direct loop | split the `TAPS` output rows disjointly | packed per-row dot with scalar tail | materialize `A'[r][k] = a[r+k]`, run `M=TAPS, N=1, K` |
| GEMM | direct triple loop | split `M` output rows disjointly | row×column packed dot | native `M×N×K` descriptor |

The NPU descriptor requires `A_STRIDE >= K`, so the FIR sliding window cannot be
expressed with `A_STRIDE = 1`. The NPU FIR path therefore materializes the
`TAPS × K` Toeplitz input in shared RAM and counts that materialization inside
the measured window. The materialized matrix is method-specific scratch; the
logical input signal and coefficients are byte-identical to every other path.

The multicore path splits only output or reduction ownership; disjoint writers
mean no two harts write the same byte. Partial sums and completion flags use the
existing release/acquire handoff contract. The single-hart `scalar` method is the
one-worker baseline, so the measured `scalar` vs `multicore` difference is the
parallel scaling plus dispatch/join overhead.

## Measurement contract

Each capture is one `(kernel, method, shape, placement, cache-mode)` run. All
methods in a capture use:

- identical logical A/B/C data from one seed generator;
- identical allocation sizes and guard bytes;
- identical cache-preparation policy;
- an independent full reinitialization and output poison before the measured run.

The common measurement window starts at the method command edge and ends when
the result is visible and the counters are frozen. It includes, for every path,
method dispatch, descriptor/setup writes, all data movement, compute, and
completion. It excludes UART formatting, host interaction, and global STOP. For
the NPU path it includes descriptor traffic and array memory cycles; for DOT8 it
includes packing and tail handling; for multicore it includes dispatch/join.

Each capture reports total offload cost separately from NPU active-array cycles,
so utilization and memory-only cost are visible independently. Every method
freezes all counters on the same command edge before any readout.

## AsterBench v8 record

Phase 10 adds a new strict schema/version; v2 through v7 are unchanged. One
record begins `ASTERBENCH,` and binds:

- `version=8`, `name` (`dot`, `fir`, `gemm`), `method`, `status`;
- shape (`k`, and `m`/`n`/`taps` as applicable), seed, strides and placements;
- full A/B/C allocation sizes, base addresses and guard bytes;
- the complete scalar output and the method output, so the oracle can check the
  arithmetic without trusting the firmware's own comparison;
- both 14-counter CPU banks, the 14-counter DMA bank, and the NPU
  read/write/compute/job cycles and completed tiles;
- CPU/DMA/DOT8/NPU ABIs, cache geometry, memory wait and configured clock;
- source/toolchain/build provenance recorded by the capture tool.

Fields that do not apply to a method (for example NPU counters under `scalar`)
must be exactly zero. Unknown, duplicate, noncanonical, truncated or rehashed
fields fail validation. The validator is independent of the v2–v7 validators
and is exercised by a shared valid/malformed corpus in the host tests.

## Fixed first study

The predeclared primary study is:

- dot: `K` in `{1, 4, 7, 16, 64, 256, 1024}`;
- FIR: `TAPS=8`, `K` in `{4, 16, 64, 256}`;
- GEMM: `(M,N,K)` in `{(1,1,1), (1,3,4), (3,5,8), (4,4,16), (8,8,31), (16,16,64), (32,32,32)}`;
- placements: aligned and one unaligned variant;
- cache off and on;
- all four methods;
- at least two independently fresh repeated captures per configuration.

A separate functional matrix adds zero-dimension, zero-K, partial-edge and
seeded extrema/cancellation cases. Any later shape or engine addition is a
separate study and cannot silently replace these captures.

## Verification and acceptance gates

- [x] README scope, Phase 9 baseline, kernel arithmetic and fairness contract frozen.
- [x] Shared scalar reference validated against an independent Python oracle.
- [x] Generalized DOT8 dot/FIR/GEMM kernels pass directed and randomized tests.
- [x] Two-hart path is bit-correct under the coherent ownership contract; the scalar method is the one-worker baseline.
- [x] Unified v8 firmware runs on one coherent SoC image with `HART_COUNT=2`, `ENABLE_L1=0/1`, `ENABLE_DMA=1`, `ENABLE_DOT8=1`, `ENABLE_NPU=1`.
- [x] Independent v8 validator, malformed corpus and host tests pass.
- [x] Full fixed study captures and fresh repeats are retained with complete provenance.
- [x] Cross-engine ratios and crossovers are analyzed and retained; slowdowns are kept.
- [x] All applicable Phase 1–9 regressions and `make check` remain green (full clean `make check`, 222 host tests).
- [x] Documentation updated and evidence retained under `docs/results/phase10/`.

Board-backed gates:

- [x] Per-engine LUT/FF/DSP/BRAM utilization and maximum clock from a clean Vivado run.
- [x] Post-route timing/signoff for the Phase 10 image.
- [x] Physical warm-boot captures and physical cycle measurements.

The README Phase 10 exit is satisfied at commit `8371c3d` plus the retained
closeout documentation: identical randomized dot/FIR/GEMM kernels are bit-correct
against the independent oracle, the frozen 288-capture study and two fresh
repeats reproduce exactly, per-engine FPGA resources and routed timing are
measured, and six captures execute through the all-engine image on the Pynq-Z1
at 31.25 MHz. The [self-contained closeout
bundle](results/phase10/closeout-8371c3d/README.md) retains the raw records,
routed reports and physical evidence and passes `scripts/audit_phase10.py`.

## Explicit non-goals

This phase does not add CNN/Conv2D, pooling, activation functions, quantized
model formats, an 8×8 accelerator, shared L2, interrupts, privileged software,
DDR/scatter-gather DMA, dynamic scheduling, floating point, saturation, or a
compiler fork. It does not rewrite PicoRV32, change Phase 1–9 maps/ABIs, claim
ASIC readiness, or infer area/energy from simulation. It does not perform the
Phase 11 ML-inference or Phase 12 streaming demos.

## Repository layout

| Area | Phase 10 responsibility |
| --- | --- |
| `software/benchmarks/` | Shared cross-engine kernels and the v8 workload |
| `software/tests/` | Directed/randomized cross-engine and lifecycle firmware |
| `verification/soc/` | Actual-core all-engine SoC capture harness |
| `verification/common/` | v8 record parser |
| `verification/host/` | Validator, oracle and malformed-corpus tests |
| `scripts/` | v8 capture, independent oracle, study and audit tools |
| `docs/results/phase10/` | Completed, audited Phase 10 evidence only |
