#!/usr/bin/env python3
"""20.5 step 3's supporting figures (docs/tuning.md §4.1, §4.4, §5; phase20.md, 20.5 step 3), from its captures:

  mnist-batching.txt   MNIST's cycles an image and a batch's latency, per batch size, one and two harts (t3-ml)
  ecg-stages.txt       the stamped ECG pipelines' median stage times and chunk period at v1's case (t3-ecg)
  ecg-outputs.txt      §5's outputs: samples a second, each chunk's latency against its 360 Hz deadline (t3-ecg)
  dma-freed.txt        §4.1's freed time: the DMA's busy cycles in the copies the variants make (t3-ml, t3-ecg)
  bank-conflicts.txt   §4.4: the fabric's bank conflicts in the placement cases, at L0 and over the layouts (t3-judge)
  icache.txt           §4.4's code against the instruction cache: the warm windows' instruction misses at L0 and
                       their fewest over the layouts, for the originals and the adopted variants (t3-judge)

  matrix_tuning_report.py MATRIX_DIR OUT_DIR [--prefix t3b]    (MATRIX_DIR holds PREFIX-ml, -ecg and -judge)
"""
import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matrix
import matrix_gates
from matrix_overlap import records_of


def revision(run: Path) -> str:
    return json.loads((run / "manifest.json").read_text())["source"]["revision"][:7]


def incomplete(run: Path) -> list[str]:
    """Why a run cannot be used: a dirty tree, or entries neither captured nor unsupported (each would narrow a
    figure's range silently)."""
    m = json.loads((run / "manifest.json").read_text())
    bad = [e["id"] for e in m["entries"] if e["status"] not in ("captured", "unsupported")]
    return (["the tree was dirty"] if m["source"]["dirty"] else []) + ([f"{len(bad)} entries {bad[:2]}"] if bad else [])


def e2e(recs: list[dict]) -> dict:
    return next(r for r in recs if r["window"] == "e2e")


def mnist_batching(ml: dict, rev: str) -> list[str]:
    lines = [f"MNIST at R (8 KiB), e2e: cycles an image and a batch interval's latency (100 MHz); t3-ml, {rev}"]
    for case, batch in (("mnist_mlp_npu", 1), ("mnist_mlp_npu__2h", 1), ("mnist_mlp_npu_batched1", 1),
                        ("mnist_mlp_npu_batched4", 4), ("mnist_mlp_npu_batched__2h4", 4), ("mnist_mlp_npu_batched8", 8),
                        ("mnist_mlp_npu_batched__2h8", 8), ("mnist_mlp_npu_batched32", 32),
                        ("mnist_mlp_npu_batched__2h32", 32)):
        row = []
        for state in ("warm", "cold"):
            cycles = int(e2e(ml[f"ml/{case}/npu/soc_dev/{state}"][1])["h0_cycles"])
            row.append(f"{state} {cycles / 32:,.0f} an image, {cycles / 32 * batch:,.0f} a batch "
                       f"({cycles / 32 * batch / 100:,.1f} us)")
        lines.append(f"{case}: " + "; ".join(row))
    return lines


def ecg_stages(manifest: dict, rev: str) -> list[str]:
    lines = ["ECG's stamped twins at R, warm, e2e, v1's case (64 samples, 16 taps): each stage's median a chunk "
             f"(cycles), and the chunk period (classify to classify); t3-ecg, {rev}"]
    entries = {e["id"]: e for e in manifest["entries"]}
    for case in ("ecg_pipeline_overlap_stages", "ecg_pipeline_overlap__fir4_stages",
                 "ecg_pipeline_overlap__fir4_copy_stages", "ecg_pipeline_overlap__fir4_split_stages",
                 "ecg_pipeline_v1_stages", "ecg_pipeline_v1__fir4_stages"):
        e = entries[f"ecg/{case}/pipeline/soc_dev/warm"]
        st = [dict(t.split("=", 1) for t in line.split(",")[1:]) for line in e["extras"]
              if line.startswith("MATRIX_STAGE,window=e2e,")]
        med = {k: statistics.median([int(s[k].split("-")[1]) - int(s[k].split("-")[0]) for s in st[1:-1]])
               for k in ("bring", "fir", "classify", "dma", "npu")}
        period = statistics.median([int(st[i + 1]["classify"].split("-")[0]) - int(st[i]["classify"].split("-")[0])
                                    for i in range(len(st) - 1)])
        lines.append(f"{case}: {med}, period {period}")
    return lines


def ecg_outputs(ecg: dict, manifest: dict, rev: str) -> list[str]:
    entries = {e["id"]: e for e in manifest["entries"]}
    lines = [f"ECG at R, v1's case (16 chunks of 64 samples, 16 taps), e2e: samples a second at 100 MHz, and each "
             f"chunk's latency (cycles: worst, mean) against its deadline, the chunk's period at 360 Hz; t3-ecg, {rev}"]
    for case, method in (("ecg_pipeline_v1", "pipeline"), ("ecg_pipeline_v1__fir4", "pipeline"),
                         ("ecg_dot8", "dot8"), ("ecg_dot8__fir4", "dot8"), ("ecg_pipeline_overlap", "pipeline"),
                         ("ecg_pipeline_overlap__fir4", "pipeline"), ("ecg_pipeline_overlap__fir4_split", "pipeline")):
        for state in ("cold", "warm"):
            ident = f"ecg/{case}/{method}/soc_dev/{state}"
            r = e2e(ecg[ident][1])
            lat = [dict(t.split("=", 1) for t in line.split(",")[1:]) for line in entries[ident]["extras"]
                   if line.startswith("MATRIX_ECG,")][0]
            deadline = 64 * int(r["clock_hz"]) // 360
            lines.append(f"{case} {state}: {1024 / (int(r['h0_cycles']) / int(r['clock_hz'])):,.0f} samples/s; "
                         f"latency worst {lat['latency_max']}, mean {lat['latency_mean']}, deadline {deadline:,}")
    return lines


def dma_freed(ml: dict, ecg: dict, rev: str) -> list[str]:
    lines = [f"The DMA's busy cycles in the copies the variants make, e2e, at R; t3-ml and t3-ecg, {rev}. A copy the "
             "CPU waits on (polling) frees no time (matrix.md §4.3); __2h_next's run during the image's layers"]
    for case, method in (("cifar_cnn_npu_direct__2h_copy", "npu_direct"), ("cifar_cnn_npu_direct__2h_next", "npu_direct"),
                         ("cifar_cnn_npu_im2col__2h_copy", "npu_im2col"),
                         ("cifar_cnn_npu_im2col__2h_next", "npu_im2col")):
        for state in ("cold", "warm"):
            r = e2e(ml[f"ml/{case}/{method}/soc_dev/{state}"][1])
            busy, jobs, cycles = int(r["dma_busy_cycles"]), int(r["dma_jobs"]), int(r["h0_cycles"])
            lines.append(f"{case} {state}: {jobs} jobs, busy {busy:,} cycles ({busy / jobs:.0f} a job), "
                         f"{busy / cycles * 100:.2f}% of the window's {cycles:,}")
    for case in ("ecg_pipeline_overlap", "ecg_pipeline_overlap__fir4", "ecg_pipeline_overlap__fir4_split"):
        r = e2e(ecg[f"ecg/{case}/pipeline/soc_dev/cold"][1])
        jobs = int(r["dma_jobs"])
        lines.append(f"{case} cold: {jobs} jobs" + (f", busy {int(r['dma_busy_cycles']):,} cycles "
                                                    f"({int(r['dma_busy_cycles']) / jobs:.0f} a job)" if jobs else ""))
    return lines


def by_layout(judge: dict, select) -> dict:
    """(config, state, window) -> {layout: record} for the entries `select` admits."""
    out = defaultdict(dict)
    for ident, (e, recs) in judge.items():
        if not select(e):
            continue
        config = re.sub(r"/L\d$", "", ident)
        config = config.replace(f"/{e['axes']['cache_state']}", "", 1)
        for r in recs:
            out[(config, e["axes"]["cache_state"], r["window"])][e["axes"].get("layout", "L0")] = r
    return out


def bank_conflicts(judge: dict, rev: str) -> list[str]:
    lines = [f"The fabric's bank conflicts (all four banks) in the placement cases' windows, two workers: at L0, their "
             f"range over the layouts, and the largest share of the window's cycles; t3-judge, {rev}"]
    rows = by_layout(judge, lambda e: e["method"] == "multicore"
                     and (e["case"].startswith("reduce_") or e["case"].startswith("gemm_dot8_")))
    for (config, state, window), lay in sorted(rows.items()):
        counts = {k: sum(int(r[f"f_bank{b}_conflicts"]) for b in range(4)) for k, r in lay.items()}
        share = max(counts[k] / int(r["h0_cycles"]) for k, r in lay.items()) * 100
        lines.append(f"{config} {state} {window}: L0 {counts['L0']}, layouts {min(counts.values())} to "
                     f"{max(counts.values())}, at most {share:.3f}% of the cycles")
    return lines


def icache(judge: dict, rev: str) -> list[str]:
    lines = [f"The warm windows' instruction-cache misses (both harts) at L0, and the fewest over the layouts with that "
             f"layout's cycles against L0's, for the originals, the adopted variants at their adopted points and the "
             f"code-placement twins of both (__hot, _hot): "
             f"each window with 32 misses or more at L0; t3-judge, {rev}. A layout that removes them shows conflict "
             f"misses (the code pad moves the program's code against the runtime's); misses no layout moves are not"]
    def judged(e: dict) -> bool:                       # (an original, an adopted variant, or the twin of either)
        case = matrix.program_case(e["case"])
        return "__" not in case or matrix_gates.adopted_at(case, e["axes"])
    rows = by_layout(judge, lambda e: e["axes"]["cache_state"] == "warm" and not e["case"].endswith("_stages")
                     and judged(e))
    found = []
    for (config, state, window), lay in rows.items():
        misses = {k: int(r["h0_icache_misses"]) + int(r.get("h1_icache_misses") or 0) for k, r in lay.items()}
        if "L0" not in misses or misses["L0"] < 32:
            continue
        best = min(misses, key=lambda k: (misses[k], k))
        l0, at = int(lay["L0"]["h0_cycles"]), int(lay[best]["h0_cycles"])
        found.append((-misses["L0"], f"{config} {window}: L0 {misses['L0']} misses, {l0:,} cycles; fewest "
                                      f"{misses[best]} at {best}, {at:,} cycles ({(at / l0 - 1) * 100:+.1f}%)"))
    lines += [line for _, line in sorted(found)]
    lines.append(f"{len(found)} of {len(rows)} warm windows have 32 misses or more at L0")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--prefix", default="t3", help="the captures' prefix (t3: step 3's; t3b: step 3b's)")
    args = parser.parse_args()
    runs = {name: args.matrix / f"{args.prefix}-{name[3:]}" for name in ("t3-ml", "t3-ecg", "t3-judge")}
    problems = {name: why for name, run in runs.items() if (why := incomplete(run))}
    if problems:
        print(f"runs not usable: {problems}")
        return 1
    revs = {name: revision(run) for name, run in runs.items()}
    if len(set(revs.values())) != 1:
        print(f"the runs' revisions differ: {revs}")
        return 1
    rev = revs["t3-ml"]
    ml, ecg, judge = (records_of(runs[n]) for n in ("t3-ml", "t3-ecg", "t3-judge"))
    ecg_manifest = json.loads((runs["t3-ecg"] / "manifest.json").read_text())
    for name, lines in (("mnist-batching.txt", mnist_batching(ml, rev)), ("ecg-stages.txt", ecg_stages(ecg_manifest, rev)),
                        ("ecg-outputs.txt", ecg_outputs(ecg, ecg_manifest, rev)), ("dma-freed.txt", dma_freed(ml, ecg, rev)),
                        ("bank-conflicts.txt", bank_conflicts(judge, rev)), ("icache.txt", icache(judge, rev))):
        if args.prefix != "t3":                          # (the header names the captures it came from)
            lines[0] = re.sub(r"\bt3-(ml|ecg|judge)\b", lambda m: f"{args.prefix}-{m.group(1)}", lines[0])
        (args.out / name).write_text("\n".join(lines) + "\n")
        print(f"{name}: {len(lines) - 1} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
