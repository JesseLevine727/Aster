#!/usr/bin/env python3
"""20.5's split policy (docs/tuning.md §4.3) re-derived from its sweep (matrix.py's split_fine, on every layout) and
checked against software/runtime/aster_split.h: for the dot's K and each coherence kind's items, the size from which
two workers beat one at every larger size of the sweep, in every window, on every layout, warm and cold (none:
never). The header's thresholds must be these; it exits 1 otherwise.

  matrix_split.py RUN_DIR [--json OUT]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matrix
from matrix_overlap import records_of

KINDS = {name: kind for kind, name, workers in matrix.COHERENT_KINDS if workers == (1, 2)}


def crossovers(run: Path) -> dict:
    """case -> (the smallest size from which two workers win at every larger size, or None; the sizes; the points
    each size has)"""
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for e, recs in records_of(run).values():
        if e["family"] != "split_fine":
            continue
        a = e["axes"]
        size = a["k"] if e["case"] == "dot" else a["items"]
        for r in recs:
            by[e["case"]][size][(a.get("layout", "L0"), a["cache_state"], r["window"])][e["method"]] = int(r["h0_cycles"])
    out = {}
    for case, sizes in by.items():
        wins = {s: all(p.get("multicore", 1 << 62) < p.get("scalar", -1) for p in points.values())
                for s, points in sizes.items()}
        first = None
        for s in sorted(sizes, reverse=True):
            if not wins[s]:
                break
            first = s
        out[case] = (first, sorted(sizes), {s: len(points) for s, points in sizes.items()})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    found = crossovers(args.run)
    policy = matrix.split_policy()
    never = 0xFFFFFFFF
    rows, problems = {}, []
    for case, (first, sizes, points) in sorted(found.items()):
        header = policy["dot_k"] if case == "dot" else policy["items"].get(KINDS.get(case))
        derived = never if first is None else first
        rows[case] = dict(derived=None if first is None else first, header=None if header == never else header,
                          sizes=sizes, points=points)
        if header != derived:
            problems.append(f"{case}: the sweep gives {rows[case]['derived']}, aster_split.h {rows[case]['header']}")
        if len(set(points.values())) != 1:
            problems.append(f"{case}: sizes with different numbers of points {points}")
    missing = sorted((set(KINDS) | {"dot"}) - set(found))
    if missing:
        problems.append(f"not in the sweep: {missing}")
    result = dict(revision=manifest["source"]["revision"], dirty=manifest["source"]["dirty"], counts=manifest["counts"],
                  cases=rows, problems=problems)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    ok = not problems and not result["dirty"] and manifest["counts"]["failed"] == 0
    print(f"{'PASS' if ok else 'FAIL'}: the split policy re-derived from {args.run.name} ({result['revision'][:7]}"
          f"{', DIRTY' if result['dirty'] else ''}): " + "; ".join(
              f"{case} {v['derived'] if v['derived'] is not None else 'never'}" for case, v in rows.items()))
    for p in problems:
        print(f"  {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
