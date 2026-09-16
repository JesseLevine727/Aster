#!/usr/bin/env python3
"""Phase 12 / Batch 4: train and quantize a tiny CIFAR-10 CNN.

The model is a single 3x3 convolution (3->8), ReLU, 2x2 max pool and a
392->10 fully connected layer over 16x16 RGB inputs (CIFAR-10 downscaled 2x2).
Weights and activations are per-tensor symmetric INT8. Emits a deterministic
artifact consumed by the reference and the export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision

SCHEMA = "aster.cifar.model.v1"
SUBSET_PER_CLASS = 2
SEED = 0x13570000


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
        self.conv = nn.Conv2d(3, 16, 3)
        self.fc = nn.Linear(16 * 7 * 7, 10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.max_pool2d(F.relu(self.conv(x)), 2)
        return self.fc(x.flatten(1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path.home() / "datasets" / "cifar10")
    parser.add_argument("--output", type=Path, default=Path("build/cifar/model.json"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.use_deterministic_algorithms(True, warn_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    train = torchvision.datasets.CIFAR10(root=str(args.data), train=True, download=False)
    test = torchvision.datasets.CIFAR10(root=str(args.data), train=False, download=False)
    train_x = torch.tensor(train.data, dtype=torch.float32).permute(0, 3, 1, 2)
    train_y = torch.tensor(train.targets, dtype=torch.int64)
    test_x = torch.tensor(test.data, dtype=torch.float32).permute(0, 3, 1, 2)
    test_y = torch.tensor(test.targets, dtype=torch.int64)

    # Downscale 32x32 -> 16x16 (integer average of 2x2 blocks).
    train_x = F.avg_pool2d(train_x, 2)
    test_x = F.avg_pool2d(test_x, 2)

    model = TinyCNN().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(train_x, train_y), batch_size=args.batch, shuffle=True,
        generator=torch.Generator().manual_seed(SEED))
    for epoch in range(args.epochs):
        model.train()
        for bx, by in loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            loss = F.cross_entropy(model(bx), by)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            correct = (model(test_x.to(device)).argmax(1).cpu() == test_y).sum().item()
        print(f"epoch {epoch + 1}: test accuracy {correct / len(test_y):.4f}", flush=True)

    model.eval()
    with torch.no_grad():
        conv_out = F.relu(model.conv(test_x.to(device)))
        pooled = F.max_pool2d(conv_out, 2)
        conv_max = float(conv_out.max().item())
        pooled_max = float(pooled.max().item())
        logits = model.fc(pooled.flatten(1))
        output_max = float(logits.abs().max().item())
        float_classes = model(test_x.to(device)).argmax(1).cpu()

    w_conv = model.conv.weight.detach().cpu().numpy()          # [8,3,3,3]
    b_conv = model.conv.bias.detach().cpu().numpy()            # [8]
    w_fc = model.fc.weight.detach().cpu().numpy()              # [10,392]
    b_fc = model.fc.bias.detach().cpu().numpy()                # [10]

    s_in = 255.0 / 127.0
    s_w1 = float(np.max(np.abs(w_conv))) / 127.0
    s_c1 = conv_max / 127.0
    s_w2 = float(np.max(np.abs(w_fc))) / 127.0
    s_out = output_max / 127.0

    q_w1 = np.clip(round_half_away(w_conv / s_w1), -128, 127).astype(np.int8)
    q_w2 = np.clip(round_half_away(w_fc / s_w2), -128, 127).astype(np.int8)
    m1, sh1 = fixed_point(s_in * s_w1 / s_c1)
    m2, sh2 = fixed_point(s_c1 * s_w2 / s_out)
    bias_q1 = round_half_away(b_conv / s_c1).astype(np.int64)
    bias_q2 = round_half_away(b_fc / s_out).astype(np.int64)

    # Balanced seeded test subset (SUBSET_PER_CLASS images per class).
    chosen = []
    for label in range(10):
        indices = (test_y == label).nonzero(as_tuple=True)[0].tolist()
        chosen.extend(indices[:SUBSET_PER_CLASS])
    images = test_x[chosen].numpy()                            # [N,3,16,16]
    labels = test_y[chosen].tolist()
    q_images = np.clip(round_half_away(images / s_in), -128, 127).astype(np.int8)

    # Integer reference: conv (im2col GEMM) + requant + relu + maxpool + fc.
    def requant(acc, mult, shift, bias):
        vectorized = np.vectorize(lambda p: _round_shift(int(p), shift))
        return np.clip(vectorized(acc.astype(np.int64) * mult) + bias, -128, 127).astype(np.int64)

    count = len(chosen)
    oc = w_conv.shape[0]
    conv_logits = np.zeros((count, oc, 14, 14), dtype=np.int64)
    for n in range(count):
        im2col = _im2col(q_images[n].astype(np.int64))
        acc = im2col @ q_w1.reshape(oc, -1).T                 # [196,oc]
        q = requant(acc, m1, sh1, bias_q1)
        conv_logits[n] = np.maximum(q, 0).reshape(14, 14, oc).transpose(2, 0, 1)
    pooled = conv_logits.reshape(count, oc, 7, 2, 7, 2).max(axis=(3, 5)).reshape(count, oc * 49)
    acc2 = pooled @ q_w2.T
    out = requant(acc2, m2, sh2, bias_q2)
    classes = out.argmax(1).tolist()
    correct = sum(1 for c, l in zip(classes, labels) if c == l)
    print(f"integer subset accuracy {correct}/{count}", flush=True)

    artifact = {
        "schema": SCHEMA,
        "provenance": {"seed": SEED, "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
                       "device": str(device), "torch": torch.__version__,
                       "float_subset_accuracy": float((float_classes[chosen] == test_y[chosen]).float().mean())},
        "input": {"channels": 3, "height": 16, "width": 16, "scale": s_in},
        "conv": {"out_channels": oc, "kernel": 3, "weights": q_w1.flatten().tolist(),
                 "weight_scale": s_w1, "bias_q": bias_q1.tolist(), "out_scale": s_c1,
                 "requant": {"mult": m1, "shift": sh1}},
        "fc": {"in_features": oc * 49, "out_features": 10, "weights": q_w2.flatten().tolist(),
               "weight_scale": s_w2, "bias_q": bias_q2.tolist(), "out_scale": s_out,
               "requant": {"mult": m2, "shift": sh2}},
        "test": {"subset": chosen, "labels": labels, "images": q_images.flatten().tolist(),
                 "reference_logits": out.tolist(), "reference_classes": classes},
    }
    payload = json.dumps(artifact, sort_keys=True).encode()
    artifact["hash"] = hashlib.sha256(payload).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.output} (hash {artifact['hash'][:16]})", flush=True)
    return 0


def _round_shift(product: int, shift: int) -> int:
    if shift == 0:
        return product
    half = 1 << (shift - 1)
    if product >= 0:
        return (product + half) >> shift
    return -((-product + half) >> shift)


def _im2col(image: np.ndarray) -> np.ndarray:
    # image [3,16,16] -> [14*14, 27], k = channel*9 + ky*3 + kx
    out = np.zeros((14 * 14, 27), dtype=np.int64)
    for oy in range(14):
        for ox in range(14):
            row = oy * 14 + ox
            for c in range(3):
                for ky in range(3):
                    for kx in range(3):
                        out[row, c * 9 + ky * 3 + kx] = image[c, oy + ky, ox + kx]
    return out


if __name__ == "__main__":
    raise SystemExit(main())
