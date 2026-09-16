#!/usr/bin/env python3
"""Independent integer reference for the tiny CIFAR-10 CNN (v2).

Recomputes conv1 + pool + conv2 + pool + fc from the model artifact without
importing the training code, and verifies every retained logit and class.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def round_shift(product: int, shift: int) -> int:
    if shift == 0:
        return product
    half = 1 << (shift - 1)
    if product >= 0:
        return (product + half) >> shift
    return -((-product + half) >> shift)


def requantize(accumulator: int, mult: int, shift: int, bias_q: int) -> int:
    return max(-128, min(127, round_shift(accumulator * mult, shift) + bias_q))


def load_model(path: Path) -> dict:
    model = json.loads(path.read_text())
    require(model.get("schema") == "aster.cifar.model.v2", "model schema is not CIFAR v2")
    payload = json.dumps({k: v for k, v in model.items() if k != "hash"}, sort_keys=True).encode()
    require(hashlib.sha256(payload).hexdigest() == model["hash"], "model hash does not match its contents")
    return model


def _im2col(image: list[list[list[int]]], channels: int, height: int, width: int) -> list[list[int]]:
    out_h, out_w = height - 2, width - 2
    rows = [[0] * (channels * 9) for _ in range(out_h * out_w)]
    for oy in range(out_h):
        for ox in range(out_w):
            row = oy * out_w + ox
            for c in range(channels):
                for ky in range(3):
                    for kx in range(3):
                        rows[row][c * 9 + ky * 3 + kx] = image[c][oy + ky][ox + kx]
    return rows


def _conv(planes: list[list[list[int]]], layer: dict) -> list[list[list[int]]]:
    channels = layer["in_channels"]
    height = len(planes[0])
    width = len(planes[0][0])
    out_h, out_w = height - 2, width - 2
    cols = _im2col(planes, channels, height, width)
    weights = layer["weights"]
    out_channels = layer["out_channels"]
    k_dim = channels * 9
    result = [[[0] * out_w for _ in range(out_h)] for _ in range(out_channels)]
    for n in range(out_channels):
        row = weights[n * k_dim:(n + 1) * k_dim]
        for index in range(out_h * out_w):
            acc = sum(a * b for a, b in zip(row, cols[index]))
            value = max(requantize(acc, layer["requant"]["mult"], layer["requant"]["shift"],
                                   layer["bias_q"][n]), 0)
            result[n][index // out_w][index % out_w] = value
    return result


def _maxpool(planes: list[list[list[int]]]) -> list[list[list[int]]]:
    channels, height, width = len(planes), len(planes[0]), len(planes[0][0])
    out_h, out_w = height // 2, width // 2
    result = [[[0] * out_w for _ in range(out_h)] for _ in range(channels)]
    for c in range(channels):
        for y in range(out_h):
            for x in range(out_w):
                result[c][y][x] = max(planes[c][2 * y][2 * x], planes[c][2 * y][2 * x + 1],
                                      planes[c][2 * y + 1][2 * x], planes[c][2 * y + 1][2 * x + 1])
    return result


def infer(model: dict, image: list[int]) -> list[int]:
    planes = [[[0] * 16 for _ in range(16)] for _ in range(3)]
    for c in range(3):
        for y in range(16):
            for x in range(16):
                planes[c][y][x] = image[(c * 16 + y) * 16 + x]
    pooled1 = _maxpool(_conv(planes, model["conv1"]))
    pooled2 = _maxpool(_conv(pooled1, model["conv2"]))
    features = [pooled2[c][y][x] for c in range(len(pooled2)) for y in range(2) for x in range(2)]
    fc = model["fc"]
    weights = fc["weights"]
    logits = []
    for out in range(fc["out_features"]):
        row = weights[out * fc["in_features"]:(out + 1) * fc["in_features"]]
        acc = sum(a * b for a, b in zip(row, features))
        logits.append(requantize(acc, fc["requant"]["mult"], fc["requant"]["shift"], fc["bias_q"][out]))
    return logits


def record_checksum(model: dict) -> int:
    checksum = 0
    for logits, cls in zip(model["test"]["reference_logits"], model["test"]["reference_classes"]):
        for value in logits:
            checksum = ((checksum * 33) ^ (value & 0xFF)) & 0xFFFFFFFF
        checksum = ((checksum * 33) ^ (cls & 0xFFFFFFFF)) & 0xFFFFFFFF
    return checksum


def reference(path: Path) -> dict:
    model = load_model(path)
    test = model["test"]
    in_features = 3 * 16 * 16
    require(len(test["images"]) == len(test["labels"]) * in_features, "image buffer disagrees with labels")
    logits, classes = [], []
    for index in range(len(test["labels"])):
        image = test["images"][index * in_features:(index + 1) * in_features]
        values = infer(model, image)
        logits.append(values)
        classes.append(max(range(len(values)), key=values.__getitem__))
    require(logits == test["reference_logits"], "independent logits differ from the artifact")
    require(classes == test["reference_classes"], "independent classes differ from the artifact")
    correct = sum(1 for c, label in zip(classes, test["labels"]) if c == label)
    return {"images": len(classes), "correct": correct, "accuracy": correct / len(classes),
            "model_hash": model["hash"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record", type=Path, help="v10 record to cross-check against the artifact")
    args = parser.parse_args()
    try:
        result = reference(args.model)
        if args.record:
            import re
            match = re.search(r"checksum=0x([0-9a-f]{8})", args.record.read_text())
            require(match is not None, "record has no checksum field")
            expected = record_checksum(load_model(args.model))
            require(int(match.group(1), 16) == expected,
                    f"record checksum {match.group(1)} disagrees with the artifact {expected:#010x}")
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"PASS: independent CIFAR reference matches {result['images']} images "
          f"({result['correct']} correct, {result['accuracy']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
