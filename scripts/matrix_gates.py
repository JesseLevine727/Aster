#!/usr/bin/env python3
"""20.4's scaling and v1 gates, measured from matrix runs (docs/soc.md §11; docs/matrix.md §5). 20.4 measures
them; 20.5 requires them.

  - Scaling: two workers against one, DOT8 for the GEMMs, on the gate's reduction (fill and sum in parallel,
    `reduce_fill`) and the three GEMM cases (`gemm_dot8_*`): one worker's cycles over two's, for every
    configuration both ran in and each window. The gate asks 1.8x.
  - Against v1: each v1-retained workload's best v2 method against v1's best (matrix.md §5's table), in cycles,
    with v1's inputs and window (the e2e window), cold as v1's figures were (v1's programs START once after
    reset), at R (soc_dev), on the default seed. MNIST's figure is per image (v1's per-image window; the
    batched NPU is throughput, not v1's latency, and is left out). ECG's is v1's case: 64-sample chunks, 16
    taps, v1's model. The CPU kernels compare with v1's aster_minimal figures.

  - 20.5 (tuning.md §3, §4.2): each gate at one cache size and one layout (the defaults: 4 KiB and L0, the layout
    of record), so that the 2 and 8 KiB caches and the layouts are judged on their own entries.

  matrix_gates.py RUN_DIR... [--cache-kib 4] [--layout L1] [--json OUT]
"""
import argparse
import json
import sys
from pathlib import Path

from matrix_overlap import records_of

SCALING = ("reduce_fill", "gemm_dot8_64x64x64", "gemm_dot8_96x96x96", "gemm_dot8_128x64x128")
SCALING_GATE = 1.8
R_SIM = {4: "soc_dev", 2: "soc_l1_2k", 8: "soc_l1_8k"}      # (R at each cache size, 20.5)

# v1's best (matrix.md §5): the family, the cases that compete, and v1's cycles
V1 = {
    "Conv2D 32x32 K=5, x4": ("dsp", lambda c: c.startswith("conv2d_"), 4_837_408),
    "reduction, 1,024 words x4 (v1's)": ("coherence", lambda c: c == "reduce_v1", 233_112),
    "MNIST MLP, per image": ("ml", lambda c: c.startswith("mnist_mlp_") and c != "mnist_mlp_npu_batched", 433_903),
    # (v1's model, not the 8-feature one; tuned variants count, the stamped twins do not)
    "streaming ECG, 16 x 64": ("ecg", lambda c: c.startswith("ecg_") and not c.split("__")[0].endswith("_f8")
                               and not c.endswith("_stages"), 1_528_505),
    "CIFAR-10, 20 images": ("ml", lambda c: c.startswith("cifar_cnn_"), 65_568_218),
    "CoreMark (aster_minimal)": ("cpu", lambda c: c == "coremark", 1_922_272),
    "Dhrystone (aster_minimal)": ("cpu", lambda c: c == "dhrystone", 3_128_553),
    "sort/search (aster_minimal)": ("cpu", lambda c: c == "sort_search", 2_183_301),
    "FFT (aster_minimal)": ("cpu", lambda c: c == "fft", 3_057_473),
    "strided (aster_minimal)": ("cpu", lambda c: c == "strided", 9_087),
    "Conv2D (aster_minimal)": ("cpu", lambda c: c == "conv2d", 5_820_652),
}


def config(entry: dict) -> str:
    axes = {k: v for k, v in entry["axes"].items() if k not in ("workers",)}
    return entry["sim"] + " " + json.dumps(axes, sort_keys=True)


def at(e: dict, kib: int, layout: str | None) -> bool:
    """The entry is at this cache size and layout (None: L0, no pads)."""
    return e["axes"].get("cache_kib", 4) == kib and e["axes"].get("layout") == layout


def scaling(allrec: dict, kib: int = 4, layout: str | None = None) -> dict:
    out = {}
    for case in SCALING:
        one, two = {}, {}
        for e, recs in allrec.values():
            if e["case"] != case or "/seed" in e["id"] or not at(e, kib, layout):
                continue
            side = one if e["axes"].get("workers") == 1 else two
            for r in recs:
                side[(config(e), r["window"])] = (int(r["h0_cycles"]), e["sim"], e["axes"]["cache_state"])
        rows = [dict(config=k[0], window=k[1], sim=one[k][1], cache_state=one[k][2], one=one[k][0], two=two[k][0],
                     speedup=round(one[k][0] / two[k][0], 4)) for k in sorted(one) if k in two]
        e2e = [r["speedup"] for r in rows if r["window"] == "e2e"]
        out[case] = dict(pairs=len(rows), e2e_min=min(e2e, default=None), e2e_max=max(e2e, default=None),
                         r_warm={r["window"]: (r["one"], r["two"], r["speedup"]) for r in rows
                                 if r["sim"] == R_SIM[kib] and r["cache_state"] == "warm"},
                         meets=bool(e2e) and min(e2e) >= SCALING_GATE, rows=rows)
    return out


def against_v1(allrec: dict, kib: int = 4, layout: str | None = None) -> dict:
    out = {}
    for name, (family, competes, v1) in V1.items():
        best = None
        for e, recs in allrec.values():
            if e["family"] != family or not competes(e["case"]) or e["sim"] != R_SIM[kib] \
                    or e["axes"].get("cache_state") != "cold" or "/seed" in e["id"] or not at(e, kib, layout):
                continue
            if family == "ecg" and (e["axes"].get("chunk"), e["axes"].get("taps")) != (64, 16):
                continue
            for r in recs:
                if r["window"] != "e2e":
                    continue
                cycles = int(r["h0_cycles"]) / (int(r["iterations"]) if name.startswith("MNIST") else 1)
                if best is None or cycles < best[0]:
                    best = (cycles, e["id"])
        out[name] = dict(v1=v1, v2=round(best[0], 2) if best else None, method=best[1] if best else None,
                         ratio=round(v1 / best[0], 2) if best else None, faster=bool(best) and best[0] < v1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--cache-kib", type=int, default=4, choices=sorted(R_SIM))
    parser.add_argument("--layout", help="a layout's entries (L1-L7); the default is L0, no pads")
    args = parser.parse_args()
    if args.layout == "L0":
        args.layout = None                  # (L0's entries carry no layout axis)
    allrec = {}
    for run in args.runs:
        allrec.update(records_of(run))
    result = dict(cache_kib=args.cache_kib, layout=args.layout or "L0",
                  scaling=scaling(allrec, args.cache_kib, args.layout),
                  against_v1=against_v1(allrec, args.cache_kib, args.layout))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    for case, s in result["scaling"].items():
        print(f"scaling {case}: {s['pairs']} pairs, e2e {s['e2e_min']} to {s['e2e_max']}x "
              f"({'meets' if s['meets'] else 'misses'} {SCALING_GATE}x); R warm {s['r_warm']}")
    for name, v in result["against_v1"].items():
        print(f"v1 {name}: v1 {v['v1']:,}, v2 {v['v2']} ({v['method']}): {v['ratio']}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
