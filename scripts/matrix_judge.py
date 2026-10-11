#!/usr/bin/env python3
"""20.5's tuning judged (docs/tuning.md §3): each tuned variant ("<original>__<tag>") against its original, at R, on
every layout both fit, warm and cold, in every window the variant has.

A variant's entry is paired with its original's: the same id with the original's case (so the same configuration,
layout and cache state). Per configuration (the variant's id without its layout and cache state), the verdict:
  - wins: faster in every window, on every layout, warm and cold (only a win is adopted);
  - loses: slower in all of them;
  - not distinguishable: faster in some and slower in others (published, not adopted).
Reported with each configuration's range of change over the layouts, and L0's figures. A variant whose change stacks
on another's (matrix_gates.STACKED_ON) is judged the same way against that variant, for its own change alone: it is
adopted only where its own change wins too. With --check-adopted, matrix_gates.ADOPTED is checked against the
judgment (each adopted variant a win at R where it is adopted), and the adopted variants' other configurations that do
not win are listed, to be reported beside the win (tuning.md §3).

  matrix_judge.py RUN_DIR... [--check-adopted] [--json OUT]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import matrix_gates
from matrix_overlap import records_of


def config_of(ident: str) -> str:
    """An entry's configuration: its id without its layout and cache state."""
    return re.sub(r"/(warm|cold)(/|$)", r"\2", re.sub(r"/L\d$", "", ident)).rstrip("/")


def twin_of(ident: str, case: str, base: str | None = None) -> str:
    """The id of an entry's twin: its original's (or base's), the same configuration, layout and cache state."""
    return ident.replace(f"/{case}", f"/{base or case.split('__')[0]}", 1)


def pairs(allrec: dict, new: str | None = None, base: str | None = None) -> tuple[dict, list[str], dict]:
    """config -> [(layout, state, window, theirs, mine)]; the entries with no twin; config -> its axes (without the
    layout and cache state). Each tuned variant against its original, or (with new and base) the variant `new`
    against `base`, the variant it extends."""
    out, alone, axes = defaultdict(list), [], {}             # (axes: each configuration's, its case among them)
    for ident, (e, recs) in sorted(allrec.items()):
        if new is None and ("__" not in e["case"] or e["case"].endswith("_stages")) or new and e["case"] != new:
            continue
        twin = allrec.get(twin_of(ident, e["case"], base))
        if twin is None:
            alone.append(ident)
            continue
        config = config_of(ident)
        axes[config] = {k: v for k, v in e["axes"].items() if k not in ("layout", "cache_state")} | {"case": e["case"]}
        # (each record by its window and its place among the window's records: a program may emit several in one
        # window, smp_overhead's round trip and handoff, stamp_cost's)
        theirs = dict(zip(record_keys(twin[1]), (int(r["h0_cycles"]) for r in twin[1])))
        for key, r in zip(record_keys(recs), recs):
            if key in theirs:                             # (a window's second record labelled e2e#1, and so on)
                label = key[0] if key[1] == 0 else f"{key[0]}#{key[1]}"
                out[config].append((e["axes"].get("layout", "L0"), e["axes"]["cache_state"], label,
                                    theirs[key], int(r["h0_cycles"])))
    return out, alone, axes


def record_keys(recs: list[dict]) -> list[tuple]:
    """Each record's window and its place among that window's records, in order."""
    seen, out = defaultdict(int), []
    for r in recs:
        out.append((r["window"], seen[r["window"]]))
        seen[r["window"]] += 1
    return out


def verdict(rows: list[tuple]) -> str:
    if all(v < o for *_, o, v in rows):
        return "wins"
    if all(v > o for *_, o, v in rows):
        return "loses"
    return "not distinguishable"


def spread(rows: list[tuple], side: int) -> float | None:
    """The largest spread over the layouts (max/min - 1, %) in any one cache state and window, of the original (side
    3) or the variant (4); None on one layout."""
    by = defaultdict(list)
    for r in rows:
        by[(r[1], r[2])].append(r[side])
    spreads = [max(v) / min(v) - 1 for v in by.values() if len(v) > 1]
    return round(max(spreads) * 100, 2) if spreads else None


def summary(by_config: dict, axes: dict) -> dict:
    out = {}
    for config, rows in sorted(by_config.items()):
        change = [(v / o - 1) * 100 for *_, o, v in rows]
        out[config] = dict(verdict=verdict(rows), points=len(rows), layouts=len({r[0] for r in rows}),
                           states=sorted({r[1] for r in rows}), windows=sorted({r[2] for r in rows}),
                           low=round(min(change), 2), high=round(max(change), 2),
                           l0={f"{s} {w}": (o, v) for lay, s, w, o, v in rows if lay == "L0"}, axes=axes[config],
                           spread_theirs=spread(rows, 3), spread_mine=spread(rows, 4))
    return out


def stacked(allrec: dict, new: str, base: str) -> tuple[dict, list[str]]:
    """The variant `new` against `base` (the variant it extends), per configuration; and new's entries without
    base's twin."""
    by_config, alone, axes = pairs(allrec, new, base)
    return summary(by_config, axes), alone


def case_of(v: dict) -> str:
    """A configuration's case (its entries' case: the id's name can carry a sweep point, the batched MNIST's)."""
    return v["axes"]["case"]


def is_r(config: str) -> bool:
    return config.split("/")[3] == "soc_dev"


def check_adopted(result: dict, stacks: dict) -> tuple[list[str], list[str]]:
    """matrix_gates.ADOPTED against the judgment: at R, each adopted variant wins against its original at each of its
    adopted sweep points, and its stacked change against the variant it extends there too; a variant adopted at some
    points (ADOPTED_POINTS) at exactly those where both win. The problems, and the adopted variants' configurations
    that do not win: R's points where they are not adopted, and the other configurations at the points where they
    are (reported beside the win: tuning.md §3)."""
    problems, beside = [], []
    stacked_on = {new: (base, rows) for (new, base), rows in stacks.items()}
    present = {case_of(v) for v in result.values()}
    problems += [f"{case}: adopted, but not in the judgment" for case in sorted(matrix_gates.ADOPTED - present)]
    for case in sorted(matrix_gates.ADOPTED & present):
        base, srows = stacked_on.get(case, (None, {}))
        if matrix_gates.stacked_base(case) is not None and base is None:
            problems.append(f"{case}: stacked on {matrix_gates.stacked_base(case)}, not judged against it")
        point_wins, on_layouts = {}, False                # (a point won only where every configuration there wins)
        for config, v in result.items():
            if case_of(v) != case:
                continue
            s = srows.get(config) if base else None
            both = v["verdict"] == "wins" and (base is None or (s is not None and s["verdict"] == "wins"))
            at = matrix_gates.adopted_at(case, v["axes"])
            if is_r(config) and case in matrix_gates.ADOPTED_POINTS:
                point = matrix_gates.point_of(case, v["axes"])
                point_wins[point] = point_wins.get(point, True) and both
            on_layouts |= is_r(config) and v["layouts"] > 1
            if is_r(config) and at and not both:
                problems.append(f"{config}: adopted, but {v['verdict']}"
                                + (f", against {base} {s['verdict'] if s else 'not judged'}" if base else ""))
            elif not both and (at or is_r(config)):           # (another configuration: where it is adopted)
                beside.append(f"{config}: {v['verdict']} ({v['low']:+.2f}% to {v['high']:+.2f}%)"
                              + (f"; against {base} {s['verdict']} ({s['low']:+.2f}% to {s['high']:+.2f}%)"
                                 if s else "") + ("" if at or not is_r(config) else " (not adopted at this point)"))
        # (adopted at exactly its winning points, on the layouts' judgment; within them, on L0's alone)
        wins_at = {point for point, won in point_wins.items() if won}
        points = matrix_gates.ADOPTED_POINTS.get(case, (None, None))[1]
        if points is not None and (points != wins_at if on_layouts else not points <= wins_at):
            problems.append(f"{case}: adopted at {sorted(points)}, wins at {sorted(wins_at)}")
    return problems, beside


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--check-adopted", action="store_true",
                        help="matrix_gates.ADOPTED against this judgment (exit 1 on a problem)")
    args = parser.parse_args()
    manifests = [json.loads((run / "manifest.json").read_text()) for run in args.runs]
    revs = {m["source"]["revision"] for m in manifests}
    dirty = any(m["source"]["dirty"] for m in manifests)
    allrec = {}
    for run in args.runs:
        allrec.update(records_of(run))
    status = {e["id"]: e["status"] for m in manifests for e in m["entries"]}
    # (an entry neither captured nor unsupported, failed or never run, would narrow a judgment)
    failed = sorted(i for i, st in status.items() if st not in ("captured", "unsupported"))
    by_config, alone, axes = pairs(allrec)
    # (a variant's entry whose original cannot run there, unsupported with its reason, is the variant's alone: the
    # split variants on the one-hart SoC, below their thresholds; any other is a missing twin)
    unpaired = [i for i in alone if status.get(twin_of(i, allrec[i][0]["case"])) == "unsupported"]
    alone = [i for i in alone if i not in unpaired]
    result = summary(by_config, axes)
    present = {case_of(v) for v in result.values()}
    stacks, stack_alone = {}, []
    for new in sorted(present):
        base = matrix_gates.stacked_base(new)
        if base is not None:
            stacks[(new, base)], missing = stacked(allrec, new, base)
            stack_alone += missing
    problems, beside = check_adopted(result, stacks) if args.check_adopted else ([], [])
    out = dict(revisions=sorted(revs), dirty=dirty, without_original=alone, original_unsupported=unpaired,
               configs=result,
               stacked={f"{n}:{b}": rows for (n, b), rows in stacks.items()}, stacked_without_base=stack_alone,
               adopted_problems=problems, adopted_beside=beside)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")
    counts = defaultdict(int)
    for v in result.values():
        counts[v["verdict"]] += 1
    print(f"revisions {[r[:7] for r in sorted(revs)]}{' (DIRTY)' if dirty else ''}; {len(result)} variant "
          f"configurations: {dict(counts)}; {len(alone)} variant entries without an original, {len(unpaired)} "
          f"whose original cannot run there (unsupported), {len(stack_alone)} stacked without the variant they extend")
    for config, v in result.items():
        print(f"  {v['verdict']:20} {config}: {v['low']:+.2f}% to {v['high']:+.2f}% over {v['layouts']} layouts, "
              f"{'/'.join(v['states'])}, {'/'.join(v['windows'])}" + (
                  f"; the spread over the layouts {v['spread_theirs']}% -> {v['spread_mine']}%"
                  if v["spread_theirs"] is not None else ""))
    for (new, base), rows in stacks.items():
        for config, v in rows.items():
            print(f"  stacked {v['verdict']:20} {config} against {base}: {v['low']:+.2f}% to {v['high']:+.2f}% over "
                  f"{v['layouts']} layouts")
    if args.check_adopted:
        unadopted = sum(line.endswith("(not adopted at this point)") for line in beside)
        print(f"the adopted set: {'PASS' if not problems else 'FAIL'}; of the adopted variants' configurations that do "
              f"not win, {unadopted} are R's points where they are not adopted, {len(beside) - unadopted} other "
              f"configurations at points where they are (reported beside the win)")
        for line in problems:
            print(f"  PROBLEM {line}")
        for line in beside:
            print(f"  beside the win: {line}")
    if failed:
        print(f"  entries neither captured nor unsupported in the runs: {len(failed)}, e.g. {failed[:3]}")
    return 0 if not dirty and len(revs) == 1 and not alone and not stack_alone and not problems and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
