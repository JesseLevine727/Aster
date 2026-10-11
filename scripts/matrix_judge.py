#!/usr/bin/env python3
"""20.5's tuning judged (docs/tuning.md §3): each tuned variant ("<original>__<tag>") against its original, at R, on
every layout both fit, warm and cold, in every window the variant has.

A variant's entry is paired with its original's: the same id with the original's case (so the same configuration,
layout and cache state). Per configuration (the variant's id without its layout and cache state), the verdict:
  - wins: faster in every window, on every layout, warm and cold (only a win is adopted);
  - loses: slower in all of them;
  - not distinguishable: faster in some and slower in others (published, not adopted).
Reported with each configuration's range of change over the layouts, and L0's figures. With --stacked NEW:BASE, a
variant whose change stacks on another's is judged the same way against that variant (each configuration by its chunk
and taps, or "-"), for its own change alone: a stacked variant is adopted only where its own change wins.

  matrix_judge.py RUN_DIR... [--stacked NEW:BASE ...] [--json OUT]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from matrix_overlap import records_of


def pairs(allrec: dict) -> tuple[dict, list[str]]:
    """config -> [(layout, state, window, original, variant)], and the variants' entries with no original."""
    out, alone = defaultdict(list), []
    for ident, (e, recs) in sorted(allrec.items()):
        if "__" not in e["case"] or e["case"].endswith("_stages"):
            continue
        original = e["case"].split("__")[0]
        twin = allrec.get(ident.replace(f"/{e['case']}", f"/{original}", 1))
        if twin is None:
            alone.append(ident)
            continue
        layout = e["axes"].get("layout", "L0")
        state = e["axes"]["cache_state"]
        config = re.sub(r"/(warm|cold)(/|$)", r"\2", re.sub(r"/L\d$", "", ident)).rstrip("/")
        theirs = {r["window"]: int(r["h0_cycles"]) for r in twin[1]}
        for r in recs:
            if r["window"] in theirs:
                out[config].append((layout, state, r["window"], theirs[r["window"]], int(r["h0_cycles"])))
    return out, alone


def verdict(rows: list[tuple]) -> str:
    if all(v < o for *_, o, v in rows):
        return "wins"
    if all(v > o for *_, o, v in rows):
        return "loses"
    return "not distinguishable"


def stacked(allrec: dict, new: str, base: str) -> dict:
    """The variant `new` against `base` (the variant it extends), per configuration: the verdict and the range."""
    rows = defaultdict(list)
    for ident, (e, recs) in allrec.items():
        if e["case"] != new:
            continue
        twin = allrec.get(ident.replace(f"/{new}/", f"/{base}/", 1))
        if twin is None:
            continue
        a = e["axes"]
        config = f"c{a['chunk']}t{a['taps']}" if "chunk" in a else "-"
        theirs = {r["window"]: int(r["h0_cycles"]) for r in twin[1]}
        rows[config] += [(a.get("layout", "L0"), a["cache_state"], r["window"], theirs[r["window"]], int(r["h0_cycles"]))
                         for r in recs if r["window"] in theirs]
    return {config: dict(verdict=verdict(v), points=len(v), low=round(min((x / o - 1) * 100 for *_, o, x in v), 2),
                         high=round(max((x / o - 1) * 100 for *_, o, x in v), 2)) for config, v in sorted(rows.items())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--stacked", nargs="*", default=[], metavar="NEW:BASE")
    args = parser.parse_args()
    manifests = [json.loads((run / "manifest.json").read_text()) for run in args.runs]
    revs = {m["source"]["revision"] for m in manifests}
    dirty = any(m["source"]["dirty"] for m in manifests)
    allrec = {}
    for run in args.runs:
        allrec.update(records_of(run))
    by_config, alone = pairs(allrec)
    result = {}
    for config, rows in sorted(by_config.items()):
        change = [(v / o - 1) * 100 for *_, o, v in rows]
        l0 = {f"{s} {w}": (o, v) for lay, s, w, o, v in rows if lay == "L0"}
        result[config] = dict(verdict=verdict(rows), points=len(rows), layouts=len({r[0] for r in rows}),
                              states=sorted({r[1] for r in rows}), windows=sorted({r[2] for r in rows}),
                              low=round(min(change), 2), high=round(max(change), 2), l0=l0)
    stacks = {pair: stacked(allrec, *pair.split(":")) for pair in args.stacked}
    out = dict(revisions=sorted(revs), dirty=dirty, without_original=alone, configs=result, stacked=stacks)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")
    counts = defaultdict(int)
    for v in result.values():
        counts[v["verdict"]] += 1
    print(f"revisions {[r[:7] for r in sorted(revs)]}{' (DIRTY)' if dirty else ''}; {len(result)} variant "
          f"configurations: {dict(counts)}; {len(alone)} variant entries without an original")
    for config, v in result.items():
        if v["verdict"] != "wins" or "/c" not in config.split("/")[-1]:
            print(f"  {v['verdict']:20} {config}: {v['low']:+.2f}% to {v['high']:+.2f}% over {v['layouts']} layouts, "
                  f"{'/'.join(v['states'])}, {'/'.join(v['windows'])}")
    for pair, rows in stacks.items():
        counts = defaultdict(int)
        for v in rows.values():
            counts[v["verdict"]] += 1
        print(f"  stacked {pair}: {dict(counts)}; " + " ".join(
            f"{c}:{v['verdict']}({v['low']:+.1f}..{v['high']:+.1f}%)" for c, v in rows.items()))
    missing = [pair for pair, rows in stacks.items() if not rows]
    return 0 if not dirty and len(revs) == 1 and not alone and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
