#!/usr/bin/env python3
"""The Phase 20 report's tables (docs/phase20-report.md; tuning.md §9), each from the final capture's records
(build/matrix/f5-*: one clean commit), at R warm end to end unless a table says otherwise, the default seed, L0.
One table a research question (README.md):
  1. which engine wins for the same work: each workload's methods side by side;
  2. working set, access pattern, banks and memory latency;
  3. cache capacity and coherence;
  4. the DMA: its crossover, its throughput, and the time it frees;
  5. NPU geometry and tile shape: sustained MACs a cycle and utilization;
  6. ECG: overlap, throughput and deadline.
Cycles are the records' h0_cycles (hart 0's window); seconds at 100 MHz; GOPS with a MAC as two operations
(phase17-plus.md §3).

  phase20_report_data.py MATRIX_DIR [--prefix f5] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from matrix_overlap import records_of

CLOCK = 100e6
FAMILIES = ("cpu", "coherence", "dsp", "ml", "ecg", "memory", "npu_gemm", "dma")


def load(matrix: Path, prefix: str) -> dict:
    allrec = {}
    for f in FAMILIES:
        allrec.update(records_of(matrix / f"{prefix}-{f}"))
    return allrec


def r_warm(e: dict, sim: str = "soc_dev", state: str = "warm") -> bool:
    return e["sim"] == sim and e["axes"].get("cache_state") == state and "/seed" not in e["id"] \
        and not e["axes"].get("layout")


def config_key(e: dict, *ignored: str) -> tuple:
    """The same entry but for the axes named (and the seed's suffix, which r_warm already excludes)."""
    return (e["family"], e["case"], e["method"],
            json.dumps({k: v for k, v in e["axes"].items() if k not in ignored}, sort_keys=True))


METHOD_CLASS = {"scalar": "one core", "cpu_copy": "one core", "multicore": "two cores", "dot8": "DOT8",
                "npu": "the NPU", "npu_direct": "the NPU", "npu_im2col": "the NPU", "dma": "the DMA",
                "pipeline": "ECG pipelines"}


def geo(values: list[float]) -> str:
    return f"{math.exp(statistics.mean(math.log(x) for x in values)):.3f}×" if values else "—"


def cycles(recs: list[dict], window: str = "e2e") -> int | None:
    r = next((r for r in recs if r["window"] == window), None)
    return int(r["h0_cycles"]) if r else None


def fmt(n) -> str:
    return "—" if n is None else f"{n:,}"


def table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def rq1(allrec: dict) -> list[str]:
    lines = ["## 1. Which engine wins (R, warm, end to end, cycles; the winner in bold)", ""]
    # the GEMMs: v1's kernels (the npu_gemm family: scalar, DOT8, two workers of DOT8, which pack nothing apart),
    # 19.4's packed DOT8 GEMM (the coherence family's gemm_dot8_*: one and two workers, B packed in the window; the
    # same inputs and checksum), and the NPU
    by = defaultdict(dict)
    for e, recs in allrec.values():
        if e["family"] == "npu_gemm" and r_warm(e) and "__" not in e["case"]:
            a = e["axes"]
            by[(a["m"], a["n"], a["k"])][e["method"]] = cycles(recs)
        elif e["family"] == "coherence" and e["case"].startswith("gemm_dot8_") and "__" not in e["case"] and r_warm(e):
            a = e["axes"]
            by[(a["m"], a["n"], a["k"])][{"dot8": "dot8_packed", "multicore": "multicore_packed"}[e["method"]]] = \
                cycles(recs)
    shapes = [(1, 1, 1), (4, 4, 16), (16, 16, 64), (32, 32, 32), (64, 64, 64), (96, 96, 96), (128, 64, 128),
              (32, 1, 784), (784, 1, 25), (64, 1, 64)]
    rows = []
    for s in shapes:
        m = by.get(s, {})
        if not m:
            continue
        best = min((v, k) for k, v in m.items() if v)
        cpu = min((v for k, v in m.items() if k != "npu" and v), default=None)
        gops = lambda c: f"{2 * s[0] * s[1] * s[2] / (c / CLOCK) / 1e9:.3g}" if c else "—"
        rows.append([f"{s[0]}×{s[1]}×{s[2]}"] + [("**" + fmt(m.get(k)) + "**") if k == best[1] else fmt(m.get(k))
                                                for k in ("scalar", "dot8", "multicore", "dot8_packed",
                                                          "multicore_packed", "npu")]
                    + [f"{cpu / m['npu']:.1f}×" if cpu and m.get("npu") else "—", gops(cpu), gops(m.get("npu"))])
    lines += ["**GEMM (int8, M×N×K):** v1's kernels (scalar, DOT8, two cores of DOT8; nothing packed), 19.4's packed "
              "DOT8 GEMM on one and two cores (B packed inside the window; at npu.md §7's three sizes, the coherence "
              "family's gemm_dot8_*), and the NPU. The same inputs and checksum in each.", ""]
    lines += table(["Shape", "Scalar", "DOT8", "Two cores", "DOT8 packed", "Two cores packed", "NPU",
                    "NPU over best CPU", "Best CPU GOPS", "NPU GOPS"], rows) + [""]
    # the named-method workloads: Conv2D, MNIST, CIFAR
    named = [("Conv2D direct, 32×32, K = 5, four iterations", "dsp", ["conv2d_direct_scalar", "conv2d_direct_multicore",
                                                    "conv2d_direct_dot8", "conv2d_direct_npu"]),
             ("Conv2D im2col (M = 784, N = 1, K = 25)", "dsp", ["conv2d_im2col_scalar", "conv2d_im2col_multicore", "conv2d_im2col_dot8",
                                       "conv2d_im2col_npu"]),
             ("MNIST MLP 784→32→10, 32 images, one at a time", "ml", ["mnist_mlp_scalar", "mnist_mlp_multicore",
                                                           "mnist_mlp_dot8", "mnist_mlp_npu"]),
             ("CIFAR-10 CNN 16×16×3, 20 images", "ml", ["cifar_cnn_scalar", "cifar_cnn_multicore", "cifar_cnn_dot8",
                                                "cifar_cnn_npu_direct"])]
    rows = []
    for label, family, cases in named:
        got = {}
        for e, recs in allrec.values():
            if e["family"] == family and e["case"] in cases and r_warm(e) and e["axes"].get("batch", 1) == 1:
                got[e["case"]] = cycles(recs)
        if not got:
            continue
        best = min(got, key=lambda c: got[c] or 1 << 62)
        rows.append([label] + [("**" + fmt(got.get(c)) + "**") if c == best else fmt(got.get(c)) for c in cases])
    lines += ["**Applications:** one core scalar, two cores, one core DOT8, the NPU (CIFAR: direct)", ""]
    lines += table(["Workload", "Scalar", "Two cores", "DOT8", "NPU"], rows) + [""]
    # the DSP kernels by size: the dot product (K) and the FIR (taps)
    for case, axis, label in (("dot", "k", "Dot product (int8), by K"), ("fir", "k", "FIR (256 outputs), by taps"),
                              ("fft", "k", "FFT (Q15), by points")):
        by = defaultdict(dict)
        for e, recs in allrec.values():
            if e["family"] == "dsp" and e["case"] == case and r_warm(e):
                by[e["axes"].get(axis, "-")][e["method"]] = cycles(recs)
        if not by:
            continue
        methods = sorted({m for v in by.values() for m in v})
        rows = []
        for size in sorted(by, key=lambda x: (isinstance(x, str), x)):
            m = by[size]
            best = min((v, k) for k, v in m.items() if v)[1]
            rows.append([size] + [("**" + fmt(m.get(k)) + "**") if k == best else fmt(m.get(k)) for k in methods])
        lines += [f"**{label}:**", ""] + table([axis] + methods, rows) + [""]
    # the reductions
    rows = []
    for case in ("reduce_fill", "reduce_v1"):
        got = {e["method"]: cycles(recs) for e, recs in allrec.values()
               if e["family"] == "coherence" and e["case"] == case and r_warm(e)}
        if got.get("scalar") and got.get("multicore"):
            rows.append([case, fmt(got["scalar"]), fmt(got["multicore"]), f"{got['scalar'] / got['multicore']:.2f}×"])
    lines += ["**The reductions:**", ""] + table(["Case", "One core", "Two cores", "Speed-up"], rows) + [""]
    return lines


def rq2(allrec: dict) -> list[str]:
    lines = ["## 2. Working set, access pattern, banks and memory latency", ""]
    for case, axis, label in (("working_set", "bytes", "Working set (one hart, bytes)"),
                              ("read_strided", "stride", "Strided reads (stride, words)"),
                              ("walk_random", "bytes", "Random walk (bytes)"),
                              ("read_sequential", "bytes", "Sequential reads (bytes)")):
        by = {}
        for e, recs in allrec.values():
            if e["family"] == "memory" and e["case"] == case and r_warm(e):
                assert e["axes"].get(axis) not in by, (case, e["id"])
                by[e["axes"].get(axis)] = cycles(recs)
        if by:
            rows = [[k, fmt(v)] for k, v in sorted(by.items())]
            lines += [f"**{label}:** cycles, warm", ""] + table([axis, "Cycles"], rows) + [""]
    streams = defaultdict(dict)
    for e, recs in allrec.values():
        for state in ("cold", "warm"):
            if e["family"] == "memory" and e["case"].startswith("stream_") and r_warm(e, state=state) \
                    and e["axes"].get("stagger") == 0:
                assert state not in streams[e["case"]], e["id"]
                streams[e["case"]][state] = cycles(recs)
    if streams:
        lines += ["**Streams:** one hart; two harts in different banks; two harts in the same bank", ""]
        lines += table(["Case", "Cold", "Warm"], [[k, fmt(v.get("cold")), fmt(v.get("warm"))]
                                                 for k, v in sorted(streams.items())]) + [""]
    # memory latency: every R entry's cycles at +1, +2 and +4 waits against R (geometric mean), by family and by
    # method, with the largest
    sims = {"soc_w1": 1, "soc_w2": 2, "soc_w4": 4}
    ignored = ("memory_wait", "cache_kib")
    base = {}
    for e, recs in allrec.values():
        if r_warm(e):
            key = config_key(e, *ignored)
            assert key not in base, e["id"]
            base[key] = cycles(recs)
    ratios, worst = defaultdict(list), defaultdict(lambda: (0, ""))
    for e, recs in allrec.values():
        if e["sim"] in sims and e["axes"].get("cache_state") == "warm" and "/seed" not in e["id"] \
                and not e["axes"].get("layout"):
            b = base.get(config_key(e, *ignored))
            if b and cycles(recs):
                x = cycles(recs) / b
                w = sims[e["sim"]]
                for group in (e["family"], METHOD_CLASS.get(e["method"], e["method"])):
                    ratios[(group, w)].append(x)
                    worst[(group, w)] = max(worst[(group, w)], (x, e["id"]))
    def latency_rows(groups):
        return [[g] + [f"{geo(ratios.get((g, w), []))} ({len(ratios.get((g, w), []))})" for w in (1, 2, 4)]
                + [f"{worst[(g, 4)][0]:.2f}× ({worst[(g, 4)][1]})" if (g, 4) in worst else "—"] for g in groups]
    lines += ["**Memory latency:** cycles with added waits against R's one, the geometric mean of every warm "
              "entry (their count), and the largest at +4", ""]
    lines += table(["Family", "+1 wait", "+2 waits", "+4 waits", "Largest at +4"], latency_rows(FAMILIES)) + [""]
    classes = [c for c in dict.fromkeys(METHOD_CLASS.values()) if (c, 1) in ratios]
    lines += ["**The same, by method:**", ""]
    lines += table(["Method", "+1 wait", "+2 waits", "+4 waits", "Largest at +4"], latency_rows(classes)) + [""]
    return lines


def rq3(allrec: dict) -> list[str]:
    lines = ["## 3. Cache capacity and coherence", ""]
    base, ratios, worst = {}, defaultdict(list), {}
    for e, recs in allrec.values():
        if r_warm(e):
            key = config_key(e, "cache_kib")
            assert key not in base, e["id"]
            base[key] = cycles(recs)
    for e, recs in allrec.values():
        if e["sim"] in ("soc_l1_2k", "soc_l1_4k") and e["axes"].get("cache_state") == "warm" \
                and "/seed" not in e["id"] and not e["axes"].get("layout"):
            b = base.get(config_key(e, "cache_kib"))
            if b and cycles(recs):
                x, k = cycles(recs) / b, e["sim"][-2]
                ratios[(e["family"], k)].append(x)
                worst[(e["family"], k)] = max(worst.get((e["family"], k), (0, "")), (x, e["id"]))
    rows = []
    for family in FAMILIES:
        row = [family]
        for k in ("2", "4"):
            v = ratios.get((family, k))
            row.append(f"{geo(v)} (max {max(v):.2f}×, {len(v)})" if v else "—")
        row.append("; ".join(f"{k} KiB: {worst[(family, k)][1]}" for k in ("2", "4") if (family, k) in worst))
        rows.append(row)
    lines += ["**Cache capacity:** cycles with 2 and 4 KiB caches against R's 8 KiB, the geometric mean of every R "
              "entry, warm (its largest, and the count), and the largest's entry", ""]
    lines += table(["Family", "2 KiB", "4 KiB", "The largest"], rows) + [""]
    by = defaultdict(dict)
    for e, recs in allrec.values():
        if e["family"] == "coherence" and r_warm(e) and "items" in e["axes"] and "__" not in e["case"]:
            by[(e["case"], e["axes"]["items"])][e["method"]] = cycles(recs)
    rows = []
    for (case, items), m in sorted(by.items()):
        if m.get("scalar") and m.get("multicore"):
            rows.append([case, items, fmt(m["scalar"]), fmt(m["multicore"]), f"{m['scalar'] / m['multicore']:.2f}×"])
    lines += ["**Coherence:** one worker against two, by items (a speed-up under 1 is two workers losing)", ""]
    lines += table(["Case", "Items", "One worker", "Two workers", "Two's speed-up"], rows) + [""]
    return lines


def rq4(allrec: dict) -> list[str]:
    lines = ["## 4. The DMA", "",
             "**The crossover** (step 3b's sweep on the eight layouts, results/phase20/tuning-20.5/step3b/"
             "crossover.txt): the DMA beats the CPU's copy from 113 bytes aligned, 89 at the same offset in a word, "
             "and 61 at different offsets (against the shift copy, from 15 bytes).", ""]
    by = defaultdict(dict)
    for e, recs in allrec.values():
        a = e["axes"]
        if e["family"] == "dma" and e["case"] == "copy" and r_warm(e) and a.get("src_off") == 0 \
                and a.get("dst_off") == 0 and a.get("cached") == 0:
            by[a["bytes"]][e["method"]] = (cycles(recs), recs[0])
    rows = []
    for size in sorted(by):
        m = by[size]
        if "dma" in m and "cpu_copy" in m and size:
            busy = int(m["dma"][1]["dma_busy_cycles"])
            rows.append([size, fmt(m["cpu_copy"][0]), fmt(m["dma"][0]), f"{size / busy:.2f}" if busy else "—",
                         "DMA" if m["dma"][0] < m["cpu_copy"][0] else "CPU"])
    lines += ["**Copies, aligned, warm, the destination not cached:** end to end (the DMA's set-up included), "
              "and the DMA's bytes a busy cycle (peak 8)", ""]
    lines += table(["Bytes", "CPU copy", "DMA", "DMA bytes/cycle", "Faster"], rows) + [""]
    pairs = {}
    for e, recs in allrec.values():
        if e["family"] == "dma" and e["case"].startswith("overlap") and r_warm(e):
            pairs[(e["case"], e["axes"].get("bytes"), e["axes"].get("work"))] = cycles(recs)
    rows = []
    for (case, size, work), c in sorted(pairs.items()):
        if case in ("overlap_h0", "overlap_h1"):
            serial = pairs.get((case.replace("overlap_", "overlap_serial_"), size, work))
            if serial:
                rows.append([case, size, work, fmt(serial), fmt(c), f"{(1 - c / serial) * 100:.1f}%"])
    lines += ["**Freed time:** a DMA copy beside unrelated work (a checksum over `work` words, on hart 0 or hart 1) "
              "against the same done one after the other", ""]
    lines += table(["Where the work runs", "Bytes", "Work", "Serial", "Overlapped", "Saved"], rows) + [""]
    return lines


def rq5(allrec: dict) -> list[str]:
    lines = ["## 5. NPU geometry and tile shape", ""]
    peak = {"soc_dev": 64, "soc_n8s1": 64, "soc_n4p8s2": 16, "soc_n4p8s1": 16, "soc_n4p4s2": 16, "soc_n4p4s1": 16}
    names = {"soc_n4p4s1": "4×4, 32-bit, 1 strip", "soc_n4p4s2": "4×4, 32-bit, 2 strips",
             "soc_n4p8s1": "4×4, 64-bit, 1 strip", "soc_n4p8s2": "4×4, 64-bit, 2 strips",
             "soc_n8s1": "8×8, 64-bit, 1 strip", "soc_dev": "8×8, 64-bit, 2 strips (R)"}
    by = defaultdict(dict)
    for e, recs in allrec.values():
        if e["family"] == "npu_gemm" and e["method"] == "npu" and e["axes"].get("cache_state") == "warm" \
                and e["sim"] in peak and "/seed" not in e["id"]:
            r = next((r for r in recs if r["window"] == "kernel"), None)
            if r and int(r["npu_macs"]):
                a = e["axes"]
                by[(a["m"], a["n"], a["k"])][e["sim"]] = int(r["npu_macs"]) / int(r["h0_cycles"])
    shapes = [s for s in [(128, 64, 128), (96, 96, 96), (64, 128, 64), (64, 64, 64), (32, 32, 32), (16, 16, 64),
                          (64, 8, 64), (32, 1, 784), (784, 1, 25)] if s in by]
    sims = [s for s in names if any(s in by[x] for x in shapes)]
    rows = [[f"{s[0]}×{s[1]}×{s[2]}"] + [(f"{by[s][g]:.1f} ({by[s][g] / peak[g] * 100:.0f}%)" if g in by[s] else "—")
                                       for g in sims] for s in shapes]
    lines += ["**Sustained MACs a cycle in the kernel window (utilization of the array's peak),** warm, by geometry",
              ""]
    lines += table(["Shape"] + [names[g] for g in sims], rows) + [""]
    best = max((v, s) for s in shapes for g, v in by[s].items() if g == "soc_dev")
    lines += [f"At R the best shape sustains {best[0]:.1f} MACs a cycle: {best[0] * CLOCK * 2 / 1e9:.1f} GOPS "
              f"(peak 64 MACs a cycle, 12.8 GOPS). Area per sustained MAC a cycle (Phase 19, npu.md §4.6): 8×8 uses "
              f"0.0124 of the device, 4×4 0.0223.", ""]
    return lines


def rq6(allrec: dict, matrix: Path, prefix: str) -> list[str]:
    lines = ["## 6. ECG", ""]
    manifest = json.loads((matrix / f"{prefix}-ecg" / "manifest.json").read_text())
    entries = {e["id"]: e for e in manifest["entries"]}
    rows = []
    for case in ("ecg_scalar", "ecg_dot8", "ecg_dot8__fir4", "ecg_pipeline_v1", "ecg_pipeline_v1__fir4",
                 "ecg_pipeline_overlap", "ecg_pipeline_overlap__fir4", "ecg_pipeline_overlap__fir4_copy",
                 "ecg_pipeline_overlap__fir4_split"):
        for state in ("cold", "warm"):
            method = "scalar" if case == "ecg_scalar" else "dot8" if case.startswith("ecg_dot8") else "pipeline"
            ident = f"ecg/{case}/{method}/soc_dev/{state}"
            if ident not in allrec:
                continue
            e, recs = allrec[ident]
            lat = [dict(t.split("=", 1) for t in line.split(",")[1:]) for line in entries[ident].get("extras", [])
                   if line.startswith("MATRIX_ECG,")]
            c = cycles(recs)
            rows.append([case, state, fmt(c), f"{1024 / (c / CLOCK):,.0f}", lat[0]["latency_max"] if lat else "—",
                         f"{64 * int(CLOCK) // 360:,}"])
    lines += ["**v1's case (16 chunks of 64 samples, 16 taps), end to end:** samples a second, and a chunk's worst "
              "latency against its deadline (the chunk's period at 360 Hz), cycles", ""]
    lines += table(["Pipeline", "State", "Cycles", "Samples/s", "Worst latency", "Deadline"], rows) + [""]
    lines += ["The final configuration's ECG at v1's case is ecg_pipeline_overlap__fir4_split (the adopted set, "
              "matrix_gates.ADOPTED: the FIR on DOT8 directly, hart 1 staging and moving the next chunk).", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--prefix", default="f5")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    allrec = load(args.matrix, args.prefix)
    revs = {json.loads((args.matrix / f"{args.prefix}-{f}" / "manifest.json").read_text())["source"]["revision"][:7]
            for f in FAMILIES}
    lines = [f"# The Phase 20 report's tables (from {', '.join(sorted(revs))}; scripts/phase20_report_data.py)", ""]
    lines += rq1(allrec) + rq2(allrec) + rq3(allrec) + rq4(allrec) + rq5(allrec) + rq6(allrec, args.matrix,
                                                                                         args.prefix)
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
