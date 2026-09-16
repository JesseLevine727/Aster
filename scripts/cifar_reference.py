#!/usr/bin/env python3
"""Independent integer reference for the tiny CIFAR-10 CNN.

Recomputes conv + requant + ReLU + maxpool + fc from the model artifact without
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
    require(model.get("schema") == "aster.cifar.model.v1", "model schema is not CIFAR")
    payload = json.dumps({k: v for k, v in model.items() if k != "hash"}, sort_keys=True).encode()
    require(hashlib.sha256(payload).hexdigest() == model["hash"], "model hash does not match its contents")
    return model


def infer(model: dict, image: list[int]) -> list[int]:
    conv, fc = model["conv"], model["fc"]
    oc, kernel = conv["out_channels"], conv["kernel"]
    h = w = model["input"]["height"]
    oh = h - kernel + 1
    # image [3,16,16]
    def pixel(c, y, x):
        return image[(c * h + y) * w + x]
    w1 = conv["weights"]
    conv_values = [[0] * (oh * oh) for _ in range(oc)]
    for oy in range(oh):
        for ox in range(oh):
            row = oy * oh + ox
            for n in range(oc):
                acc = 0
                for c in range(3):
                    for ky in range(kernel):
                        for kx in range(kernel):
                            k = c * kernel * kernel + ky * kernel + kx
                            acc += w1[n * (3 * kernel * kernel) + k] * pixel(c, oy + ky, ox + kx)
                conv_values[n][row] = max(requantize(acc, conv["requant"]["mult"],
                                                     conv["requant"]["shift"], conv["bias_q"][n]), 0)
    pooled = []
    for n in range(oc):
        for py in range(oh // 2):
            for px in range(oh // 2):
                best = -128
                for dy in range(2):
                    for dx in range(2):
                        best = max(best, conv_values[n][(py * 2 + dy) * oh + (px * 2 + dx)])
                pooled.append(best)
    w2 = fc["weights"]
    logits = []
    for out in range(fc["out_features"]):
        acc = sum(w2[out * fc["in_features"] + i] * pooled[i] for i in range(fc["in_features"]))
        logits.append(requantize(acc, fc["requant"]["mult"], fc["requant"]["shift"], fc["bias_q"][out]))
    return logits


def reference(path: Path) -> dict:
    model = load_model(path)
    test = model["test"]
    height = width = model["input"]["height"]
    in_features = 3 * height * width
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


def record_checksum(model: dict) -> int:
    """The fold the firmware performs over its computed logits and classes."""
    checksum = 0
    for logits, cls in zip(model["test"]["reference_logits"], model["test"]["reference_classes"]):
        for value in logits:
            checksum = ((checksum * 33) ^ (value & 0xFF)) & 0xFFFFFFFF
        checksum = ((checksum * 33) ^ (cls & 0xFFFFFFFF)) & 0xFFFFFFFF
    return checksum


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
            text = args.record.read_text()
            match = re.search(r"checksum=0x([0-9a-f]{8})", text)
            require(match is not None, "record has no checksum field")
            model = load_model(args.model)
            expected = record_checksum(model)
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
