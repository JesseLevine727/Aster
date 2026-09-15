# Phase 11: Quantized INT8 ML inference

Status: **draft / in progress**.
Baseline: pushed Phase 10 closeout `fea14f3`
(`docs/results/phase10/closeout-8371c3d/`).
The [README roadmap](../README.md#phase-11--quantized-ml-inference) defines this
phase as deploying a small INT8 model beginning with MNIST, mapping supported
operations to the NPU while the CPU handles control and unsupported operations,
and comparing inference across the scalar CPU, two cores, the custom instruction
and the NPU including offload overhead.

Phase 11 adds **no new compute engine**. It reuses the Phase 9 4×4 INT8 NPU and
the Phase 10 measurement infrastructure. This document is the Phase 11 contract.
It preserves every Phase 1–10 map, ABI, source and evidence artifact.

## Scope and design principles

The first accepted target is a quantized fully-connected network on MNIST,
executed end to end on the coherent RV32IMA/NPU SoC. The CPU owns control,
requantization, activation and classification; the NPU owns the integer matrix
multiply. Correctness is the exit criterion: every retained test image must
classify identically to an independent integer reference.

The [feasibility spike](../scripts/phase11_feasibility.py) fixes the sizing. The
binding constraint is the **32 KiB shared RAM** because the Phase 9 NPU can only
read A, B and C from `0x10000000..0x10008000`; the 64 KiB ROM holds the
firmware, weights and the retained test images.

| Candidate | Shared RAM (free) | ROM | Max test images | NPU tiles |
| --- | ---: | ---: | ---: | ---: |
| **MLP 784→32→10** | 26,402 B (6,366) | 53,544 B | 35 | 11 |
| MLP 784→16→10 | 13,618 B (19,150) | 40,776 B | 51 | 7 |
| MLP 196→32→10 (14×14) | 6,998 B | 26,888 B | 237 | 11 |
| CNN 8→16 | 66,642 B | 29,288 B | 66 | 914 |

The primary model is **784→32→10** (ReLU hidden, 10-way output); the fallback is
**784→16→10** if headroom is needed. The CNN does not fit without K-chunked
accumulation and is an explicit non-goal of the first exit.

### Frozen first configuration

- Network: `784 → 32 (ReLU) → 10`, weights INT8, biases INT32, activations INT8.
- Retained test subset: **32 MNIST test images**, a fixed seeded selection
  balanced across the ten classes, stored quantized in ROM with labels.
- Batch: 1. Each layer is one NPU descriptor (`M=32,N=1,K=784` then
  `M=10,N=1,K=32`); 11 output tiles total.
- Shared RAM: weights, biases, activations and requantization scratch under a
  fixed linker map; weights are staged from ROM at init.
- Reference: `scripts/phase11_train.py` + `scripts/phase11_reference.py` must
  agree bit-for-bit with the firmware on all 32 images.

## Model and quantization contract

The network is a two-layer MLP over a 28×28 grayscale input flattened to 784
values. Weights and activations are signed INT8; biases are INT32. Every
quantization constant is frozen and versioned.

- **Activations**: per-tensor symmetric, zero-point 0. Input byte `v` maps to
  `q = clamp(round(v / s_in), -128, 127)`.
- **Weights**: per-tensor symmetric, zero-point 0.
- **Accumulation**: the NPU computes each dot product as an exact signed INT32
  sum accumulated modulo 2^32. For `K ≤ 1024` the true sum cannot overflow
  (`1024 × 128 × 128 < 2^31`), so the modulo rule is never exercised.
- **Requantization** is performed on the CPU with a frozen integer formula:

  ```text
  y = clamp( round_half_away( (acc + bias) * mult / 2^shift ), -128, 127 )
  ```

  where `mult` and `shift` are derived from `s_in · s_w / s_out`. The C runtime
  and the independent Python reference must implement the identical integer
  operations (64-bit intermediate product, defined rounding, saturating clamp).
  The NPU itself never saturates.
- **Activation**: ReLU on the hidden layer (`max(y, 0)`), argmax over the 10
  output logits. Both are CPU operations.

No floating point executes on the RISC-V; floats appear only in training and in
the offline reference.

## Operator mapping

| Operation | Engine |
| --- | --- |
| Layer-1 and layer-2 matmul | NPU (one descriptor per layer) |
| Bias add, requantize, clamp | CPU |
| ReLU | CPU |
| Argmax / classification | CPU |
| Input image staging | CPU (ROM → shared RAM) |
| Weight staging at init | CPU (ROM → shared RAM) |

Each fully-connected layer is a GEMM `C[out][batch] = W[out][in] · X[in][batch]`
with `M=out`, `N=batch`, `K=in`, `A_STRIDE=in`, `B_STRIDE=batch`,
`C_STRIDE=4·batch`. The first exit uses `batch=1`; a later study may batch while
`K·batch` fits shared RAM.

## Memory and layout contract

A fixed linker map places the firmware and immutable data in ROM and the
NPU-visible weights, activations and scratch in shared RAM. The map is frozen
per model so addresses and cache behaviour are reproducible.

- ROM: `.text`, `.rodata`, weights, biases and the retained quantized test
  images (with labels).
- Shared RAM: a `struct` of weights copied at init, activation buffers, the
  requantization scratch and the frozen output logits.
- Private RAM: the two 4 KiB stacks only.

The model's weights are copied from ROM into shared RAM once at startup; that
copy is setup, outside the measured inference window. The retained test images
stay in ROM and are staged per inference.

## Data pipeline

Training and export run off-board; the board runs only frozen integers.

1. A reproducible training script (`scripts/phase11_train.py`) trains the MLP on
   MNIST using the GPU, applies the frozen quantization scheme, and emits a
   deterministic artifact: INT8 weights, INT32 biases, scales, the quantized
   test subset and labels, plus a content hash and the toolchain/seed.
2. An independent reference (`scripts/phase11_reference.py`) recomputes every
   retained inference with the same integer formula and records the expected
   logits and class for each image.
3. `scripts/phase11_export.py` converts the artifact into the ROM data blob and
   the C headers used by the firmware, and rejects any mismatch with the
   reference.

The retained test subset is a fixed, seeded selection of MNIST test images whose
count is bounded by the ROM budget (32 for the primary model). The subset and
its labels are committed; no test data is invented on the board.

## Runtime and control contract

The firmware is a bare-metal layer scheduler: stage the image, program the NPU
descriptor, poll to completion, requantize and activate on the CPU, then argmax.
Descriptor publication uses the existing release/acquire contract. There are no
interrupts or timers; completion is polled. Global warm STOP drains an admitted
job as in Phase 9.

Each inference emits an AsterBench v9 record binding the model version, image
index and label, the full INT8 input, the layer descriptors, the complete INT32
accumulators or requantized activations, the final logits and class, the
expected class, and the same CPU/DMA/DOT8/NPU counters as v8. Unknown,
duplicate, noncanonical or rehashed fields fail validation.

## Measurement contract

The primary comparison runs the same retained images through four paths:

- `scalar`: the existing scalar reference (no NPU).
- `multicore`: two coherent harts split output rows.
- `dot8`: the Xasterdot8 packed instruction.
- `npu`: the Phase 9 accelerator.

The common window starts at the inference command edge and ends when the class
is visible. It includes image staging, descriptor setup, matmul, requantization,
activation and classification, and excludes UART formatting and global STOP.
Per-layer cycles, total offload cost, NPU active-array cycles and memory-only
cycles are reported separately. Slowdowns below one are retained. End-to-end
accuracy on the retained subset is reported alongside cycles, not instead of it.

## Verification and acceptance gates

- [ ] Feasibility spike committed; model and memory map frozen.
- [ ] Training/export is reproducible and hash-bound; the independent integer reference matches the exported artifact.
- [ ] Firmware inference is bit-exact against the reference for every retained image on the all-engine SoC.
- [ ] AsterBench v9 record and independent validator with malformed-corpus host tests.
- [ ] Four-path measurement study with fresh repeats; accuracy and cycles both retained.
- [ ] Applicable Phase 1–10 regressions and `make check` remain green.
- [ ] 31.25 MHz routed overlay and physical Pynq-Z1 acceptance at the Phase 9/10 rigor.
- [ ] Self-contained closeout bundle and read-only audit.

## Explicit non-goals

This phase does not add CNN/Conv2D, pooling, CIFAR-10, training on the board,
floating point on the RISC-V, an 8×8 accelerator, shared L2, interrupts,
privileged software, DDR/scatter-gather DMA, dynamic scheduling, or model
compression. It does not change Phase 1–10 maps/ABIs or claim ASIC readiness.

## Repository layout

| Area | Phase 11 responsibility |
| --- | --- |
| `scripts/phase11_*.py` | Feasibility, training, reference, export, capture and audit |
| `software/benchmarks/` | Quantized MNIST inference workload |
| `software/tests/` | Directed inference and requantization firmware |
| `verification/unit/` | Requantization/argmax scoreboards |
| `verification/soc/` | Actual-core inference capture harness |
| `verification/host/` | v9 validator and malformed corpus |
| `docs/results/phase11/` | Completed, audited evidence only |
