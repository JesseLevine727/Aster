#!/usr/bin/env python3
"""Phase 11 memory/model feasibility spike.

Quantifies the on-chip budget for a small INT8 MNIST model on the coherent
Aster SoC and checks each layer against the Phase 9 NPU descriptor limits.
Nothing here is a performance claim; it is a sizing tool used to pick the first
Phase 11 model.
"""
from __future__ import annotations

import argparse
import json
import math

# Memory map (bytes), from docs/architecture.md and the Phase 5/6/9 contracts.
ROM = 64 * 1024
SHARED = 32 * 1024
PRIVATE = 16 * 1024
STACK = 4 * 1024
CODE_BYTES = 12 * 1024  # measured cross-engine firmware ~8 KiB plus headroom
NPU_MAX_DIM = 1024
INT8_ABS_MAX = 128
INT32_MAX = (1 << 31) - 1


def gemm_descriptor(m, n, k, a_stride=None, b_stride=None, c_stride=None):
    a_stride = k if a_stride is None else a_stride
    b_stride = n if b_stride is None else b_stride
    c_stride = 4 * n if c_stride is None else c_stride
    return {"m": m, "n": n, "k": k, "a_stride": a_stride, "b_stride": b_stride,
            "c_stride": c_stride,
            "a_bytes": m * a_stride, "b_bytes": k * b_stride, "c_bytes": m * c_stride}


def check_layer(name, desc):
    problems = []
    for key in ("m", "n", "k"):
        if not 0 <= desc[key] <= NPU_MAX_DIM:
            problems.append(f"{name}.{key}={desc[key]} exceeds {NPU_MAX_DIM}")
    if desc["a_stride"] < desc["k"]:
        problems.append(f"{name}.a_stride < k")
    if desc["b_stride"] < desc["n"]:
        problems.append(f"{name}.b_stride < n")
    if desc["c_stride"] < 4 * desc["n"]:
        problems.append(f"{name}.c_stride < 4*n")
    acc_max = desc["k"] * INT8_ABS_MAX * INT8_ABS_MAX
    if acc_max > INT32_MAX:
        problems.append(f"{name} accumulation {acc_max} overflows int32")
    tiles = math.ceil(desc["m"] / 4) * math.ceil(desc["n"] / 4)
    return {"problems": problems, "tiles": tiles, "acc_max": acc_max}


def mlp(in_features, hidden, out_features, images, image_bytes, batch=1):
    # Layer 1: C[hidden][batch] = W1[hidden][in] * X[in][batch]
    l1 = gemm_descriptor(hidden, batch, in_features)
    # Layer 2: C[out][batch] = W2[out][hidden] * H[hidden][batch]
    l2 = gemm_descriptor(out_features, batch, hidden)
    weights = hidden * in_features + out_features * hidden
    biases = (hidden + out_features) * 4
    activations = in_features * batch + hidden * batch + out_features * batch
    # Weights/biases are copied from ROM into shared RAM for NPU access.
    shared = weights + biases + activations
    rom = CODE_BYTES + weights + biases + images * image_bytes
    return {"kind": "mlp", "layers": {"fc1": l1, "fc2": l2},
            "weights": weights, "biases": biases, "activations": activations,
            "shared_bytes": shared, "rom_bytes": rom,
            "image_bytes": image_bytes, "images": images, "batch": batch}


def cnn(images, image_bytes):
    # conv1: 1->8 channels, 3x3, 28x28 valid -> 26x26 ; im2col K=9, M=676, N=8
    # conv2: 8->16 channels, 3x3, 26x26 valid -> 24x24 ; im2col K=72, M=576, N=16
    c1 = gemm_descriptor(676, 8, 9)
    c2 = gemm_descriptor(576, 16, 72)
    weights = 8 * 9 + 16 * 8 * 9
    biases = (8 + 16) * 4
    im2col1 = 676 * 9
    im2col2 = 576 * 72
    activations = 8 * 26 * 26 + 16 * 24 * 24 + 16 * 24 * 24 + 10
    scratch = max(im2col1, im2col2)
    shared = weights + biases + activations + scratch
    rom = CODE_BYTES + weights + biases + images * image_bytes
    return {"kind": "cnn", "layers": {"conv1": c1, "conv2": c2},
            "weights": weights, "biases": biases, "activations": activations,
            "im2col_scratch": scratch, "shared_bytes": shared, "rom_bytes": rom,
            "image_bytes": image_bytes, "images": images, "batch": 1}


CANDIDATES = {
    "mlp_784_32_10": lambda: mlp(784, 32, 10, images=20, image_bytes=784),
    "mlp_784_16_10": lambda: mlp(784, 16, 10, images=20, image_bytes=784),
    "mlp_196_32_10": lambda: mlp(196, 32, 10, images=40, image_bytes=196),
    "cnn_8_16": lambda: cnn(images=20, image_bytes=784),
}


def report():
    out = {"rom": ROM, "shared": SHARED, "private": PRIVATE, "stack": STACK,
           "code_bytes": CODE_BYTES, "candidates": {}}
    for name, factory in CANDIDATES.items():
        model = factory()
        layers = {layer: check_layer(layer, desc) for layer, desc in model["layers"].items()}
        problems = [p for layer in layers.values() for p in layer["problems"]]
        total_tiles = sum(layer["tiles"] for layer in layers.values())
        fits_shared = model["shared_bytes"] <= SHARED
        fits_rom = model["rom_bytes"] <= ROM
        # largest test-set size that still fits ROM
        per_image = model["image_bytes"]
        fixed_rom = model["rom_bytes"] - model["images"] * per_image
        max_images = (ROM - fixed_rom) // per_image
        out["candidates"][name] = {
            "weights": model["weights"], "biases": model["biases"],
            "activations": model.get("activations"),
            "im2col_scratch": model.get("im2col_scratch", 0),
            "shared_bytes": model["shared_bytes"], "shared_free": SHARED - model["shared_bytes"],
            "rom_bytes": model["rom_bytes"], "rom_free": ROM - model["rom_bytes"],
            "max_test_images": int(max_images),
            "npu_tiles": total_tiles,
            "fits_shared_ram": fits_shared, "fits_rom": fits_rom,
            "npu_ok": not problems, "problems": problems,
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = report()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return
    print(f"ROM {ROM} B, shared RAM {SHARED} B, private {PRIVATE} B x2, stack {STACK} B")
    print(f"firmware code budget {CODE_BYTES} B\n")
    for name, c in data["candidates"].items():
        verdict = "FIT" if (c["fits_shared_ram"] and c["fits_rom"] and c["npu_ok"]) else "NO"
        print(f"{name:16} {verdict}  weights={c['weights']:6} B  shared={c['shared_bytes']:6} B "
              f"(free {c['shared_free']:6})  rom={c['rom_bytes']:6} B  max_images={c['max_test_images']:3}  "
              f"tiles={c['npu_tiles']:4}")
        if c["problems"]:
            print("    problems:", "; ".join(c["problems"]))


if __name__ == "__main__":
    main()
