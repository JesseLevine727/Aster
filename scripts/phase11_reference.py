#!/usr/bin/env python3
"""Independent integer reference for the Phase 11 quantized MLP.

Reimplements the frozen integer inference from the model artifact without
importing the training code. Verifies the artifact hash, recomputes every
retained logit and class, and fails on any disagreement with the artifact.
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


def requantize(acc: int, mult: int, shift: int, bias_q: int) -> int:
    return max(-128, min(127, round_shift(acc * mult, shift) + bias_q))


def infer(model: dict, image: list[int]) -> list[int]:
    hidden = []
    fc1 = model["layers"][0]
    w1 = fc1["weights"]
    for out in range(fc1["out"]):
        row = w1[out * fc1["in"]:(out + 1) * fc1["in"]]
        acc = sum(a * b for a, b in zip(row, image))
        value = requantize(acc, fc1["requant"]["mult"], fc1["requant"]["shift"], fc1["bias_q"][out])
        hidden.append(max(value, 0))
    fc2 = model["layers"][1]
    w2 = fc2["weights"]
    logits = []
    for out in range(fc2["out"]):
        row = w2[out * fc2["in"]:(out + 1) * fc2["in"]]
        acc = sum(a * b for a, b in zip(row, hidden))
        logits.append(requantize(acc, fc2["requant"]["mult"], fc2["requant"]["shift"], fc2["bias_q"][out]))
    return logits


def reference(path: Path) -> dict:
    model = json.loads(path.read_text())
    require(model.get("schema") == "aster.phase11.model.v1", "model schema is not Phase 11")
    payload = json.dumps({k: v for k, v in model.items() if k != "hash"}, sort_keys=True).encode()
    require(hashlib.sha256(payload).hexdigest() == model["hash"], "model hash does not match its contents")

    test = model["test"]
    images = test["images"]
    in_features = model["layers"][0]["in"]
    require(len(images) == len(test["labels"]) * in_features, "image buffer size disagrees with labels")
    logits = []
    classes = []
    for index in range(len(test["labels"])):
        image = images[index * in_features:(index + 1) * in_features]
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
    args = parser.parse_args()
    try:
        result = reference(args.model)
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"PASS: independent integer reference matches {result['images']} images "
          f"({result['correct']} correct, {result['accuracy']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
