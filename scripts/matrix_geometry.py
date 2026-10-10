#!/usr/bin/env python3
"""20.5's cache geometry (docs/tuning.md §4.2): the L1 caches at 2 and 8 KiB against 4 KiB, from matrix runs made
with --caches, all from one clean commit.

Each entry captured at another size is paired with its 4 KiB twin (the same id at soc_dev), and each window both
recorded gives the ratio of hart 0's cycles at that size to 4 KiB's. Reported:
  - per family and size: the windows compared (all of them, and e2e alone), how many are faster, slower and
    unchanged, the median and the range of the change, and the largest moves;
  - the gates (matrix_gates.py) at each size and each layout run: the scaling gate's lowest e2e speedup, and the
    v1 gate's lowest ratio;
  - the layouts (tuning.md §3): each gate workload's e2e change at each size, on every layout it ran at, and
    whether it is faster on every layout, slower on every layout, or mixed.
No size is adopted here: that is the owner's sign-off (tuning.md §4.2).

  matrix_geometry.py RUN_DIR... [--json OUT]
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matrix_gates
from matrix_overlap import records_of

SIZES = (2, 8)


def twins(allrec: dict) -> tuple[list[dict], list[str]]:
    """Each window of each entry at 2 or 8 KiB, with its 4 KiB twin's cycles; and the entries with no twin."""
    rows, alone = [], []
    for ident, (e, recs) in sorted(allrec.items()):
        kib = e["axes"].get("cache_kib")
        if kib not in SIZES:
            continue
        twin = allrec.get(ident.replace(f"/{matrix_gates.R_SIM[kib]}/", "/soc_dev/"))
        if twin is None:
            alone.append(ident)
            continue
        if [(r["name"], r["window"]) for r in recs] != [(r["name"], r["window"]) for r in twin[1]]:
            raise SystemExit(f"{ident}: its records' windows differ from its 4 KiB twin's")
        for r, s in zip(recs, twin[1]):
            four, other = int(s["h0_cycles"]), int(r["h0_cycles"])
            if four:
                rows.append(dict(id=ident, family=e["family"], case=e["case"], method=e["method"], kib=kib,
                                 layout=e["axes"].get("layout", "L0"), cache_state=e["axes"]["cache_state"],
                                 window=r["window"], four=four, other=other, ratio=other / four))
    return rows, alone


def summary(rows: list[dict]) -> dict:
    if not rows:
        return dict(windows=0)
    change = [r["ratio"] - 1 for r in rows]
    big = sorted(rows, key=lambda r: abs(r["ratio"] - 1), reverse=True)[:3]
    return dict(windows=len(rows), faster=sum(c < 0 for c in change), slower=sum(c > 0 for c in change),
                unchanged=sum(c == 0 for c in change), median=round(statistics.median(change) * 100, 3),
                low=round(min(change) * 100, 2), high=round(max(change) * 100, 2),
                largest=[f"{r['id']} {r['window']} {r['four']} -> {r['other']}" for r in big])


def per_family(rows: list[dict]) -> dict:
    out = {}
    for kib in SIZES:
        l0 = [r for r in rows if r["kib"] == kib and r["layout"] == "L0"]
        for family in sorted({r["family"] for r in l0}):
            mine = [r for r in l0 if r["family"] == family]
            out[f"{family} {kib} KiB"] = dict(all=summary(mine), e2e=summary([r for r in mine if r["window"] == "e2e"]))
    return out


def gates(allrec: dict) -> dict:
    layouts = sorted({e["axes"].get("layout") for e, _ in allrec.values() if e["axes"].get("layout")})
    out = {}
    for kib in (4, *SIZES):
        for layout in (None, *layouts):
            s = matrix_gates.scaling(allrec, kib, layout)
            v = matrix_gates.against_v1(allrec, kib, layout)
            mins = [x["e2e_min"] for x in s.values() if x["e2e_min"] is not None]
            ratios = {name: x["ratio"] for name, x in v.items() if x["ratio"] is not None}
            if not mins and not ratios:
                continue
            out[f"{kib} KiB {layout or 'L0'}"] = dict(
                scaling_lowest=min(mins, default=None), scaling_meets=bool(mins) and min(mins) >= matrix_gates.SCALING_GATE,
                scaling_cases=sum(x["pairs"] > 0 for x in s.values()), v1_lowest=min(ratios.values(), default=None),
                v1_faster=sum(x["faster"] for x in v.values()), v1_measured=len(ratios), v1=ratios)
    return out


def across_layouts(rows: list[dict], allrec: dict) -> dict:
    """Each gate workload's e2e change at each size, per layout."""
    gate_ids = set()
    for ident, (e, _) in allrec.items():
        if e["axes"].get("cache_kib") in SIZES and (e["case"] in matrix_gates.SCALING or any(
                e["family"] == f and competes(e["case"]) for f, competes, _ in matrix_gates.V1.values())):
            gate_ids.add(ident)
    seen = defaultdict(dict)
    for r in rows:
        if r["id"] in gate_ids and r["window"] == "e2e":
            seen[(r["family"], r["case"], r["method"], r["cache_state"], r["kib"])][r["layout"]] = r["ratio"]
    out = {}
    for (family, case, method, state, kib), by in sorted(seen.items()):
        if len(by) < 2:
            continue
        verdict = "faster on every layout" if all(x < 1 for x in by.values()) else \
            "slower on every layout" if all(x > 1 for x in by.values()) else "mixed"
        out[f"{family}/{case}/{method} {state} {kib} KiB"] = dict(
            layouts=len(by), low=round((min(by.values()) - 1) * 100, 2), high=round((max(by.values()) - 1) * 100, 2),
            verdict=verdict, by_layout={k: round((v - 1) * 100, 2) for k, v in sorted(by.items())})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    revs = {json.loads((run / "manifest.json").read_text())["source"]["revision"] for run in args.runs}
    dirty = [str(run) for run in args.runs if json.loads((run / "manifest.json").read_text())["source"]["dirty"]]
    allrec = {}
    for run in args.runs:
        allrec.update(records_of(run))
    rows, alone = twins(allrec)
    result = dict(revisions=sorted(revs), dirty=dirty, pairs=len({r["id"] for r in rows}), windows=len(rows),
                  without_twin=alone, families=per_family(rows), gates=gates(allrec),
                  layouts=across_layouts(rows, allrec))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    print(f"revisions {result['revisions']}{' (DIRTY: ' + ', '.join(dirty) + ')' if dirty else ''}; "
          f"{result['pairs']} entries paired with their 4 KiB twins, {result['windows']} windows; "
          f"{len(alone)} without a twin")
    for name, s in result["families"].items():
        a, e = s["all"], s["e2e"]
        print(f"  {name}: {a['windows']} windows, {a['faster']} faster, {a['slower']} slower, {a['unchanged']} same; "
              f"median {a['median']:+}%, {a['low']:+}% to {a['high']:+}%"
              + (f"; e2e median {e['median']:+}%, {e['low']:+}% to {e['high']:+}%" if e["windows"] else ""))
    for name, g in result["gates"].items():
        print(f"  gates {name}: scaling lowest {g['scaling_lowest']} ({'meets' if g['scaling_meets'] else 'MISSES'} "
              f"{matrix_gates.SCALING_GATE}x, {g['scaling_cases']} cases); v1 lowest {g['v1_lowest']}x, "
              f"{g['v1_faster']} of {g['v1_measured']} faster")
    verdicts = defaultdict(int)
    for v in result["layouts"].values():
        verdicts[v["verdict"]] += 1
    if result["layouts"]:
        print(f"  across layouts: {dict(verdicts)} (the gate workloads' e2e at 2 and 8 KiB)")
    return 0 if not dirty and len(revs) == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
