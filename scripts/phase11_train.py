#!/usr/bin/env python3
"""Phase 11 training and quantization.

Trains the frozen MLP 784-32-10 on MNIST (GPU when available), applies the
frozen per-tensor symmetric INT8 quantization, selects the seeded balanced test
subset and emits a deterministic model artifact. The artifact is the single
source of truth consumed by phase11_reference.py and phase11_export.py.

This script is the only place that uses floating point; the board runs the
integer formula exported here.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import torch
import torch.nn as nn

SCHEMA = "aster.phase11.model.v1"
SUBSET = 32
SEED = 0x13570000


def read_idx(path: Path) -> np.ndarray:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        magic, count = struct.unpack(">II", stream.read(8))
        if magic == 0x00000803:  # images
            rows, cols = struct.unpack(">II", stream.read(8))
            return np.frombuffer(stream.read(), dtype=np.uint8).reshape(count, rows, cols)
        if magic == 0x00000801:  # labels
            return np.frombuffer(stream.read(), dtype=np.uint8).reshape(count)
        raise ValueError(f"unknown IDX magic {magic:#x} in {path}")


def load_mnist(root: Path):
    train_images = read_idx(root / "train-images-idx3-ubyte")
    train_labels = read_idx(root / "train-labels-idx1-ubyte")
    test_images = read_idx(root / "t10k-images-idx3-ubyte")
    test_labels = read_idx(root / "t10k-labels-idx1-ubyte")
    return train_images, train_labels, test_images, test_labels


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MLP(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.fc1 = nn.Linear(784, 32)
        self.fc2 = nn.Linear(32, 10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(torch.relu(self.fc1(x)))


def quantize_symmetric(values: np.ndarray) -> tuple[np.ndarray, float]:
    scale = float(np.max(np.abs(values))) / 127.0
    if scale == 0.0:
        scale = 1.0
    quantized = np.clip(np.round(values / scale), -128, 127).astype(np.int8)
    return quantized, scale


def fixed_point(ratio: float, bits: int = 31) -> tuple[int, int]:
    """Represent ratio as mult / 2**shift with mult normalized to [2**30, 2**31)."""
    if ratio <= 0:
        raise ValueError("requantization ratio must be positive")
    shift = 0
    while ratio < (1 << 30):
        ratio *= 2
        shift += 1
    mult = int(round(ratio))
    mult = min(mult, (1 << bits) - 1)
    return mult, shift


def round_shift(product: int, shift: int) -> int:
    """Round-half-away-from-zero of product / 2**shift (mirrors the C runtime)."""
    if shift == 0:
        return product
    half = 1 << (shift - 1)
    if product >= 0:
        return (product + half) >> shift
    return -((-product + half) >> shift)


def round_half_away(values: np.ndarray) -> np.ndarray:
    """Round-half-away-from-zero, matching the integer C runtime."""
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


def select_subset(labels: np.ndarray) -> list[int]:
    """Balanced seeded subset: round-robin one image per class until SUBSET."""
    by_class: dict[int, list[int]] = {c: [] for c in range(10)}
    for index, label in enumerate(labels):
        by_class[int(label)].append(index)
    chosen: list[int] = []
    round_index = 0
    while len(chosen) < SUBSET:
        for digit in range(10):
            if len(chosen) == SUBSET:
                break
            if round_index < len(by_class[digit]):
                chosen.append(by_class[digit][round_index])
        round_index += 1
        if round_index > max(len(v) for v in by_class.values()):
            break
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path.home() / "datasets" / "mnist")
    parser.add_argument("--output", type=Path, default=Path("build/phase11/model.json"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.use_deterministic_algorithms(True, warn_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    train_images, train_labels, test_images, test_labels = load_mnist(args.data)
    # Train on the raw [0,255] input domain so the INT8 input scale s_in maps
    # back to exactly these values; training on [0,1] would desynchronize the
    # quantized and floating-point networks.
    train_x = torch.tensor(train_images.reshape(-1, 784).astype(np.float32))
    train_y = torch.tensor(train_labels.astype(np.int64))
    test_x = torch.tensor(test_images.reshape(-1, 784).astype(np.float32))
    test_y = torch.tensor(test_labels.astype(np.int64))

    model = MLP().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.CrossEntropyLoss()
    dataset = torch.utils.data.TensorDataset(train_x, train_y)
    loader = torch.utils.data.DataLoader(dataset, batch_size=args.batch, shuffle=True,
                                         generator=torch.Generator().manual_seed(SEED))
    for epoch in range(args.epochs):
        model.train()
        for batch_x, batch_y in loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
        if (epoch + 1) % 5 == 0:
            model.eval()
            with torch.no_grad():
                correct = (model(test_x.to(device)).argmax(1).cpu() == test_y).sum().item()
            print(f"epoch {epoch + 1}: test accuracy {correct / len(test_y):.4f}", flush=True)

    model.eval()
    with torch.no_grad():
        float_correct = (model(test_x.to(device)).argmax(1).cpu() == test_y).sum().item()
    print(f"float test accuracy {float_correct / len(test_y):.4f}", flush=True)

    # Calibrate activation ranges on the training set (float).
    with torch.no_grad():
        h = torch.relu(model.fc1(train_x.to(device)))
        y = model.fc2(h)
        hidden_max = float(h.max().item())
        output_max = float(y.abs().max().item())

    w1 = model.fc1.weight.detach().cpu().numpy()
    b1 = model.fc1.bias.detach().cpu().numpy()
    w2 = model.fc2.weight.detach().cpu().numpy()
    b2 = model.fc2.bias.detach().cpu().numpy()

    s_in = 255.0 / 127.0
    s_w1 = float(np.max(np.abs(w1))) / 127.0
    s_h = hidden_max / 127.0
    s_w2 = float(np.max(np.abs(w2))) / 127.0
    s_o = output_max / 127.0

    q_w1 = np.clip(np.round(w1 / s_w1), -128, 127).astype(np.int8)
    q_w2 = np.clip(np.round(w2 / s_w2), -128, 127).astype(np.int8)

    # Requantization constants (bias stays real in the artifact and is applied
    # elementwise in the quantized output domain by reference/export).
    m1, sh1 = fixed_point(s_in * s_w1 / s_h)
    m2, sh2 = fixed_point(s_h * s_w2 / s_o)

    subset = select_subset(test_labels)
    images = test_images.reshape(-1, 784)[subset]
    labels = test_labels[subset].astype(int).tolist()
    q_images = np.clip(np.round(images.astype(np.float64) / s_in), -128, 127).astype(np.int8)

    # Integer reference inference (mirrors the firmware exactly).
    bias_q1 = round_half_away(b1 / s_h).astype(np.int64)
    bias_q2 = round_half_away(b2 / s_o).astype(np.int64)

    def requant(acc: np.ndarray, mult: int, shift: int, bias_q: np.ndarray) -> np.ndarray:
        vectorized = np.vectorize(lambda p: round_shift(int(p), shift))
        t = vectorized(acc.astype(np.int64) * mult) + bias_q
        return np.clip(t, -128, 127).astype(np.int64)

    acc1 = q_images.astype(np.int32) @ q_w1.T.astype(np.int32)
    h1 = requant(acc1, m1, sh1, bias_q1)
    h1 = np.maximum(h1, 0)
    acc2 = h1.astype(np.int32) @ q_w2.T.astype(np.int32)
    out = requant(acc2, m2, sh2, bias_q2)
    classes = out.argmax(1).tolist()
    integer_correct = sum(1 for c, l in zip(classes, labels) if c == l)
    with torch.no_grad():
        float_subset = model(test_x[subset].to(device)).argmax(1).cpu().tolist()
    float_subset_correct = sum(1 for c, l in zip(float_subset, labels) if c == l)
    disagreements = sum(1 for a, b in zip(classes, float_subset) if a != b)
    print(f"float subset accuracy: {float_subset_correct}/{len(labels)}", flush=True)
    print(f"integer reference accuracy on subset: {integer_correct}/{len(labels)} "
          f"(float/int disagreements {disagreements})", flush=True)

    artifact = {
        "schema": SCHEMA,
        "provenance": {
            "seed": SEED, "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
            "device": str(device), "torch": torch.__version__,
            "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
            "data_sha256": {
                "train_images": sha256_bytes(train_images.tobytes()),
                "train_labels": sha256_bytes(train_labels.tobytes()),
                "test_images": sha256_bytes(test_images.tobytes()),
                "test_labels": sha256_bytes(test_labels.tobytes()),
            },
            "float_test_accuracy": float_correct / len(test_y),
            "integer_subset_accuracy": integer_correct / len(labels),
        },
        "input": {"shape": [28, 28], "scale": s_in, "quantization": "symmetric-int8"},
        "layers": [
            {"name": "fc1", "in": 784, "out": 32, "activation": "relu",
             "weights": q_w1.flatten().tolist(), "weight_scale": s_w1,
             "bias_real": b1.tolist(), "bias_q": bias_q1.tolist(), "out_scale": s_h,
             "requant": {"mult": m1, "shift": sh1}},
            {"name": "fc2", "in": 32, "out": 10, "activation": "none",
             "weights": q_w2.flatten().tolist(), "weight_scale": s_w2,
             "bias_real": b2.tolist(), "bias_q": bias_q2.tolist(), "out_scale": s_o,
             "requant": {"mult": m2, "shift": sh2}},
        ],
        "test": {
            "subset": subset, "labels": labels, "images": q_images.flatten().tolist(),
            "reference_logits": out.tolist(), "reference_classes": classes,
            "float_classes": float_subset,
        },
    }
    payload = json.dumps(artifact, sort_keys=True).encode()
    artifact["hash"] = sha256_bytes(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output} (hash {artifact['hash'][:16]})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
