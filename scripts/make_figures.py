#!/usr/bin/env python3
"""The README's figures (docs/figures/), from the final evidence:
  - aster_soc.svg: the Phase 20 SoC (docs/soc.md §2);
  - results.svg: GEMM throughput by engine (docs/results/phase20/report-20.5/tables.md §1), each workload's best
    engine against one core (tables.md §1), and the DMA against the CPU's copy (tables.md §4).
Every figure is a program of the final capture that also ran on the board, cycle for cycle, at 100 MHz.

  make_figures.py [--out docs/figures]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, GRID = "#1f2328", "#57606a", "#d0d7de"
CORE, CACHE, MEM, NPU, DMA, DEV, PS = "#ddf4ff", "#f0f6fc", "#fff8c5", "#ffebe9", "#dafbe1", "#f6f8fa", "#eaeef2"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": INK, "ytick.color": INK, "svg.fonttype": "path"})


def box(ax, x, y, w, h, title, lines=(), fill="#ffffff", bold=True, size=10):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", linewidth=1.2,
                                edgecolor=MUTED, facecolor=fill))
    ty = y + h - 0.32 if lines else y + h / 2
    ax.text(x + w / 2, ty, title, ha="center", va="center", fontsize=size, color=INK,
            fontweight="bold" if bold else "normal")
    for k, line in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.68 - 0.3 * k, line, ha="center", va="center", fontsize=8.5, color=MUTED)


def arrow(ax, a, b, both=True):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="<|-|>" if both else "-|>", mutation_scale=10, linewidth=1.1,
                                 color=MUTED))


def soc_figure(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    ax.set_xlim(0, 15); ax.set_ylim(0, 9.4); ax.axis("off")
    fig.patch.set_facecolor("white")
    ax.text(7.5, 9.05, "The Aster SoC on the PYNQ-Z1 (Zynq-7020) at 100 MHz", ha="center", fontsize=13,
            fontweight="bold", color=INK)
    # the PS
    box(ax, 0.3, 6.9, 2.6, 1.6, "ARM (PS)", ["PYNQ Linux", "loads programs,", "reads the console"], PS)
    # the harts and their caches
    for k, x in enumerate((3.6, 7.1)):
        box(ax, x, 6.6, 3.1, 1.9, f"Aster core {k}", ["RV32IMA, Zicsr, Zifencei", "DOT8 · 7-stage, in order"], CORE)
        box(ax, x, 5.15, 1.5, 1.05, "I$ 8 KiB", [], CACHE, bold=False, size=9)
        box(ax, x + 1.6, 5.15, 1.5, 1.05, "D$ 8 KiB", [], CACHE, bold=False, size=9)
        arrow(ax, (x + 0.75, 6.6), (x + 0.75, 6.2)); arrow(ax, (x + 2.35, 6.6), (x + 2.35, 6.2))
    # the engines
    box(ax, 10.7, 6.6, 3.9, 1.9, "NPU", ["8×8 INT8 array, 12.8 GOPS peak", "two A strips, 64-bit port"], NPU)
    box(ax, 10.7, 5.15, 2.9, 1.05, "DMA", ["64-bit read + write ports"], DMA)
    # the fabric
    box(ax, 3.6, 3.35, 11.0, 1.15, "Banked fabric", ["7 requesters · arbitration · atomics · snoops keep the "
                                                     "data caches coherent"], "#ffffff")
    for x in (4.35, 5.95, 7.85, 9.45):
        arrow(ax, (x, 5.15), (x, 4.5))
    arrow(ax, (11.7, 5.15), (11.7, 4.5)); arrow(ax, (12.6, 5.15), (12.6, 4.5))
    ax.annotate("", xy=(14.1, 4.5), xytext=(14.1, 6.6), arrowprops=dict(arrowstyle="<|-|>", color=MUTED, lw=1.1))
    # the banks
    for k in range(4):
        box(ax, 3.6 + 2.8 * k, 1.75, 2.6, 1.0, f"bank {k}", ["24 KiB block RAM"], MEM, size=9.5)
        arrow(ax, (4.9 + 2.8 * k, 3.35), (4.9 + 2.8 * k, 2.75))
    ax.text(9.1, 1.4, "96 KiB of main memory, 64-bit banks, interleaved by cache line", ha="center", fontsize=8.5,
            color=MUTED, style="italic")
    # the devices and the port
    box(ax, 0.3, 3.35, 2.6, 2.85, "Devices", ["timer, interrupts", "performance counters", "16 KiB console",
                                              "register page"], DEV)
    arrow(ax, (1.6, 6.9), (1.6, 6.2))
    ax.text(1.8, 6.55, "AXI4-Lite", fontsize=8, color=MUTED, va="center")
    arrow(ax, (2.9, 3.9), (3.6, 3.9))
    ax.text(0.3, 0.55, "The final image: 100 MHz with +0.306 ns of setup slack; 72.75% of LUTs, 60% of block RAM, "
                       "12% of DSPs.", fontsize=8.5, color=MUTED)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def results_figure(out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), gridspec_kw=dict(width_ratios=[1.05, 1.25, 1.1]))
    fig.patch.set_facecolor("white")
    # (a) GEMM throughput by engine: R, warm, end to end (tables.md §1)
    ax = axes[0]
    shapes = ["64³", "96³", "128×64×128"]
    macs = [64 ** 3, 96 ** 3, 128 * 64 * 128]
    cycles = {"one core, packed DOT8": [204636, 680971, 805300], "two cores, packed DOT8": [105010, 342610, 401471],
              "NPU": [5321, 15273, 17741]}
    colors = {"one core, packed DOT8": "#9ecbff", "two cores, packed DOT8": "#2f81f7", "NPU": "#cf222e"}
    width = 0.26
    for k, (name, c) in enumerate(cycles.items()):
        gops = [2 * m / (n * 10) for m, n in zip(macs, c)]
        bars = ax.bar([i + (k - 1) * width for i in range(3)], gops, width, label=name, color=colors[name])
        for b, g in zip(bars, gops):
            ax.text(b.get_x() + b.get_width() / 2, g * 1.12, f"{g:.3g}", ha="center", fontsize=8, color=INK)
    ax.set_yscale("log"); ax.set_ylim(0.1, 400)
    ax.set_xticks(range(3)); ax.set_xticklabels(shapes)
    ax.set_ylabel("GOPS, end to end (a MAC is two operations)")
    ax.set_title("INT8 GEMM by engine", fontweight="bold", color=INK)
    ax.axhline(12.8, color=MUTED, ls=":", lw=1); ax.text(2.45, 20, "NPU peak, 12.8 GOPS", ha="right", fontsize=8,
                                                          color=MUTED)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    ax.grid(axis="y", color=GRID, lw=0.6); ax.set_axisbelow(True)
    # (b) each workload's best engine against one core's scalar code: R, warm, end to end, cycles (tables.md §1)
    ax = axes[1]
    best = [("GEMM 16×16×64", "NPU", 207117, 643), ("FIR, 64 taps", "NPU", 198191, 2831),
            ("MNIST MLP", "NPU", 9602117, 186289), ("Dot product, K = 1,024", "NPU", 11437, 505),
            ("CIFAR-10 CNN", "NPU", 56639463, 4080849), ("Conv2D", "NPU", 1132560, 84251),
            ("Streaming ECG", "two cores + DOT8", 177291, 31531), ("Reduction", "two cores", 73918, 37656),
            ("FFT", "two cores", 314610, 197585)]
    items = sorted(((one / b, f"{name} ({engine})") for name, engine, one, b in best))
    ys = range(len(items))
    ax.barh(list(ys), [r for r, _ in items], color=["#cf222e" if "NPU" in n else "#2f81f7" for _, n in items],
            height=0.62)
    for y, (r, _) in zip(ys, items):
        ax.text(r * 1.06, y, f"{r:.3g}×", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(list(ys)); ax.set_yticklabels([n for _, n in items], fontsize=9)
    ax.set_xscale("log"); ax.set_xlim(1, 1000)
    ax.set_xlabel("speed-up over one core's scalar code (cycles, end to end)")
    ax.set_title("The best engine for each workload", fontweight="bold", color=INK)
    ax.grid(axis="x", color=GRID, lw=0.6); ax.set_axisbelow(True)
    # (c) the DMA against the CPU's copy: aligned, warm, the destination not cached (tables.md §4)
    ax = axes[2]
    size = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768]
    cpu = [70, 77, 79, 87, 94, 116, 160, 248, 424, 776, 1480, 2903, 5704, 11350, 29775, 59471]
    dma = [158, 158, 158, 158, 206, 206, 206, 206, 184, 221, 315, 404, 662, 1180, 2216, 4251]
    ax.loglog(size, cpu, "o-", color="#2f81f7", ms=3.5, label="CPU copy, one core")
    ax.loglog(size, dma, "s-", color="#1a7f37", ms=3.5, label="DMA, set-up included")
    ax.axvline(113, color=MUTED, ls=":", lw=1)
    ax.text(125, 9000, "DMA wins from\n113 bytes (aligned)", fontsize=8, color=MUTED)
    ax.set_xlabel("bytes copied"); ax.set_ylabel("cycles, end to end")
    ax.set_title("Data movement: DMA vs CPU", fontweight="bold", color=INK)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    ax.grid(color=GRID, lw=0.6, which="major"); ax.set_axisbelow(True)
    fig.text(0.5, -0.03, "Each point is a program of the final capture (b96e48b) that also ran on the board at 100 MHz, "
                         "cycle for cycle. Sources: docs/results/phase20/.", ha="center", fontsize=8.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/figures")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    soc_figure(args.out / "aster_soc.svg")
    results_figure(args.out / "results.svg")
    print(f"wrote {args.out / 'aster_soc.svg'} and {args.out / 'results.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
