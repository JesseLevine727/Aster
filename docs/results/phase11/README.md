# Phase 11: Quantized INT8 ML inference — results

Status: **complete**. The contract is [`docs/phase11.md`](../../phase11.md).

This directory retains the frozen model, the four-path inference study, the
routed FPGA reports and the physical Pynq-Z1 captures.

## Model

Frozen MLP `784 → 32 (ReLU) → 10`, per-tensor symmetric INT8, 32-image balanced
MNIST test subset. Artifact hash
`63c352eb252cf17a27067632187c41b4359a8d7d238a948a24eca3cf180356c5`; training is
deterministic. The independent integer reference and the exported C headers
agree bit-for-bit, and the firmware reproduces the reference on all 32 images
(30/32 against the true labels — the two misses are genuine model errors with
zero quantization disagreement).

## Four-path study

Same 32 images through scalar, multicore, dot8 and npu, two fresh repeats.
Per-image cycles (mean over the 32 images):

| Method | Simulation cycles | scalar/method | Physical cycles | scalar/method |
| --- | ---: | ---: | ---: | ---: |
| scalar | 2,039,263 | 1.000 | 2,064,873 | 1.000 |
| multicore | 1,029,910 | 1.980 | 1,060,544 | 1.947 |
| dot8 | 868,357 | 2.348 | 885,527 | 2.332 |
| npu | 427,307 | 4.772 | 462,953 | 4.460 |

All four paths classify identically (30/32). The NPU is ~4.5–4.8× the scalar
baseline; DOT8 is ~2.3×; two coherent harts are ~1.95×. The physical ratios
track simulation closely.

## FPGA

All-engine overlay (`linux-phase11`): 25,121 LUT (47.2%), 19,198 FF (18.0%),
32 BRAM (22.9%), 24 DSP (10.9%); WNS 4.177 ns, WHS 0.051 ns, pulse 14.750 ns,
zero failing endpoints; DRC advisory DSP warnings only, clean methodology, zero
routing errors. Hashes in [fpga/bitstream.sha256](fpga/bitstream.sha256).

## Physical

Four captures (scalar, multicore, dot8, npu) at 31.25 MHz, two warm boots each,
each record validated on the board by the independent v9 oracle. See
[physical/README.md](physical/README.md).

## Reproduce

```sh
make phase11-model
make phase11-infer-validate PHASE11_METHOD=npu
python3 scripts/phase11_study.py capture --output build/phase11/study --repeats 2
python3 scripts/phase11_study.py audit build/phase11/study/study.json
make fpga-linux-xe            # all-engine routed overlay
```
