#!/usr/bin/env python3
"""Phase 12 / Batch 4: train and quantize a tiny CIFAR-10 CNN.

The model is two 3x3 convolutions (3->16, 16->32) each followed by ReLU and 2x2
max pool, then a 128->10 fully connected layer over 16x16 RGB inputs (CIFAR-10
downscaled 2x2). Weights and activations are per-tensor symmetric INT8. Emits a
deterministic artifact consumed by the reference and the export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision

SCHEMA = "aster.cifar.model.v2"
SUBSET_PER_CLASS = 2
SEED = 0x13570000
CONV1_OUT, CONV2_OUT = 16, 32


def round_half_away(values: np.ndarray) -> np.ndarray:
    return np.sign(values) * np.floor(np.abs(values) + 0.5)


def fixed_point(ratio: float, bits: int = 31) -> tuple[int, int]:
    if ratio <= 0:
        raise ValueError("requantization ratio must be positive")
    shift = 0
    while ratio < (1 << 30):
        ratio *= 2
        shift += 1
    return min(int(round(ratio)), (1 << bits) - 1), shift


class TinyCNN(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(3, CONV1_OUT, 3)
        self.conv2 = nn.Conv2d(CONV1_OUT, CONV2_OUT, 3)
        self.fc = nn.Linear(CONV2_OUT * 2 * 2, 10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.max_pool2d(F.relu(self.conv1(x)), 2)
        x = F.max_pool2d(F.relu(self.conv2(x)), 2)
        return self.fc(x.flatten(1))


def im2col(image: np.ndarray) -> np.ndarray:
    channels, height, width = image.shape
    kernel = 3
    out_h, out_w = height - kernel + 1, width - kernel + 1
    out = np.zeros((out_h * out_w, channels * kernel * kernel), dtype=np.int64)
    for oy in range(out_h):
        for ox in range(out_w):
            row = oy * out_w + ox
            for c in range(channels):
                for ky in range(kernel):
                    for kx in range(kernel):
                        out[row, c * 9 + ky * 3 + kx] = image[c, oy + ky, ox + kx]
    return out


def round_shift(product: int, shift: int) -> int:
    if shift == 0:
        return product
    half = 1 << (shift - 1)
    if product >= 0:
        return (product + half) >> shift
    return -((-product + half) >> shift)


def requant(acc: np.ndarray, mult: int, shift: int, bias: np.ndarray) -> np.ndarray:
    vectorized = np.vectorize(lambda p: round_shift(int(p), shift))
    return np.clip(vectorized(acc.astype(np.int64) * mult) + bias, -128, 127).astype(np.int64)


def integer_forward(model: dict, image: np.ndarray) -> list[int]:
    c1, c2, fc = model["conv1"], model["conv2"], model["fc"]
    w1 = np.array(c1["weights"], dtype=np.int64).reshape(c1["out_channels"], -1)
    acc = im2col(image) @ w1.T
    q = requant(acc, c1["requant"]["mult"], c1["requant"]["shift"], np.array(c1["bias_q"]))
    conv1 = np.maximum(q, 0).reshape(14, 14, c1["out_channels"]).transpose(2, 0, 1)
    pool1 = conv1.reshape(c1["out_channels"], 7, 2, 7, 2).max(axis=(2, 4))
    w2 = np.array(c2["weights"], dtype=np.int64).reshape(c2["out_channels"], -1)
    acc2 = im2col(pool1) @ w2.T
    q2 = requant(acc2, c2["requant"]["mult"], c2["requant"]["shift"], np.array(c2["bias_q"]))
    conv2 = np.maximum(q2, 0).reshape(5, 5, c2["out_channels"]).transpose(2, 0, 1)
    pool2 = np.zeros((c2["out_channels"], 2, 2), dtype=np.int64)
    for n in range(c2["out_channels"]):
        for py in range(2):
            for px in range(2):
                pool2[n, py, px] = conv2[n, 2 * py:2 * py + 2, 2 * px:2 * px + 2].max()
    pool2 = pool2.reshape(-1)
    w3 = np.array(fc["weights"], dtype=np.int64).reshape(fc["out_features"], -1)
    acc3 = w3 @ pool2
    return requant(acc3, fc["requant"]["mult"], fc["requant"]["shift"], np.array(fc["bias_q"])).tolist()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path.home() / "datasets" / "cifar10")
    parser.add_argument("--output", type=Path, default=Path("build/cifar/model.json"))
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.use_deterministic_algorithms(True, warn_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    train = torchvision.datasets.CIFAR10(root=str(args.data), train=True, download=False)
    test = torchvision.datasets.CIFAR10(root=str(args.data), train=False, download=False)
    train_x = F.avg_pool2d(torch.tensor(train.data, dtype=torch.float32).permute(0, 3, 1, 2), 2)
    train_y = torch.tensor(train.targets, dtype=torch.int64)
    test_x = F.avg_pool2d(torch.tensor(test.data, dtype=torch.float32).permute(0, 3, 1, 2), 2)
    test_y = torch.tensor(test.targets, dtype=torch.int64)

    model = TinyCNN().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(train_x, train_y), batch_size=args.batch, shuffle=True,
        generator=torch.Generator().manual_seed(SEED))
    for epoch in range(args.epochs):
        model.train()
        for bx, by in loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            F.cross_entropy(model(bx), by).backward()
            optimizer.step()
        scheduler.step()
        model.eval()
        with torch.no_grad():
            correct = (model(test_x.to(device)).argmax(1).cpu() == test_y).sum().item()
        print(f"epoch {epoch + 1}: test accuracy {correct / len(test_y):.4f}", flush=True)

    model.eval()
    with torch.no_grad():
        c1 = F.relu(model.conv1(train_x.to(device)))
        p1 = F.max_pool2d(c1, 2)
        c2 = F.relu(model.conv2(p1))
        p2 = F.max_pool2d(c2, 2)
        logits = model.fc(p2.flatten(1))
        s_c1_max = float(c1.max().item())
        s_c2_max = float(c2.max().item())
        s_out_max = float(logits.abs().max().item())
        float_classes = model(test_x.to(device)).argmax(1).cpu()

    w1, b1 = model.conv1.weight.detach().cpu().numpy(), model.conv1.bias.detach().cpu().numpy()
    w2, b2 = model.conv2.weight.detach().cpu().numpy(), model.conv2.bias.detach().cpu().numpy()
    w3, b3 = model.fc.weight.detach().cpu().numpy(), model.fc.bias.detach().cpu().numpy()

    s_in = 255.0 / 127.0
    s_w1 = float(np.max(np.abs(w1))) / 127.0
    s_c1 = s_c1_max / 127.0
    s_w2 = float(np.max(np.abs(w2))) / 127.0
    s_c2 = s_c2_max / 127.0
    s_w3 = float(np.max(np.abs(w3))) / 127.0
    s_out = s_out_max / 127.0

    q_w1 = np.clip(round_half_away(w1 / s_w1), -128, 127).astype(np.int8)
    q_w2 = np.clip(round_half_away(w2 / s_w2), -128, 127).astype(np.int8)
    q_w3 = np.clip(round_half_away(w3 / s_w3), -128, 127).astype(np.int8)
    m1, sh1 = fixed_point(s_in * s_w1 / s_c1)
    m2, sh2 = fixed_point(s_c1 * s_w2 / s_c2)
    m3, sh3 = fixed_point(s_c2 * s_w3 / s_out)
    bias_q1 = round_half_away(b1 / s_c1).astype(np.int64)
    bias_q2 = round_half_away(b2 / s_c2).astype(np.int64)
    bias_q3 = round_half_away(b3 / s_out).astype(np.int64)

    chosen = []
    for label in range(10):
        chosen.extend((test_y == label).nonzero(as_tuple=True)[0].tolist()[:SUBSET_PER_CLASS])
    images = test_x[chosen].numpy()
    labels = test_y[chosen].tolist()
    q_images = np.clip(round_half_away(images / s_in), -128, 127).astype(np.int8)

    artifact = {
        "schema": SCHEMA,
        "provenance": {"seed": SEED, "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
                       "device": str(device), "torch": torch.__version__,
                       "float_test_accuracy": float((float_classes == test_y).float().mean())},
        "input": {"channels": 3, "height": 16, "width": 16, "scale": s_in},
        "conv1": {"in_channels": 3, "out_channels": CONV1_OUT, "kernel": 3,
                  "weights": q_w1.flatten().tolist(), "weight_scale": s_w1, "bias_q": bias_q1.tolist(),
                  "out_scale": s_c1, "requant": {"mult": m1, "shift": sh1}},
        "conv2": {"in_channels": CONV1_OUT, "out_channels": CONV2_OUT, "kernel": 3,
                  "weights": q_w2.flatten().tolist(), "weight_scale": s_w2, "bias_q": bias_q2.tolist(),
                  "out_scale": s_c2, "requant": {"mult": m2, "shift": sh2}},
        "fc": {"in_features": CONV2_OUT * 4, "out_features": 10,
               "weights": q_w3.flatten().tolist(), "weight_scale": s_w3, "bias_q": bias_q3.tolist(),
               "out_scale": s_out, "requant": {"mult": m3, "shift": sh3}},
        "test": {"subset": chosen, "labels": labels, "images": q_images.flatten().tolist()},
    }
    logits_list, classes = [], []
    for n in range(len(chosen)):
        values = integer_forward(artifact, q_images[n].astype(np.int64))
        logits_list.append(values)
        classes.append(int(max(range(10), key=lambda i: values[i])))
    artifact["test"]["reference_logits"] = logits_list
    artifact["test"]["reference_classes"] = classes
    correct = sum(1 for c, l in zip(classes, labels) if c == l)
    print(f"integer subset accuracy {correct}/{len(labels)}", flush=True)

    payload = json.dumps(artifact, sort_keys=True).encode()
    artifact["hash"] = hashlib.sha256(payload).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output} (hash {artifact['hash'][:16]})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
