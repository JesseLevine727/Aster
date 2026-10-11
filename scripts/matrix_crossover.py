#!/usr/bin/env python3
"""20.5's copy policy (docs/tuning.md §4.1): the copy helper's thresholds from the finer sweep (matrix.py's dma_fine,
on every layout), the DMA through v1's driver against v1's fair CPU copy, end to end.

The helper (software/runtime/aster_copy.c) classes a copy by word alignment: both aligned, the same offset in a word,
or different offsets. For each class, the destination cached or not, warm and cold, and each layout, the crossover
is the smallest size from which the DMA is faster at every larger size of the sweep (for different offsets, at all
four of the sweep's offset pairs). A class's threshold is the largest crossover over the destination's state, warm
and cold, and the layouts: the helper copies by the DMA only where it wins on all of them (tuning.md §3's rule).

  matrix_crossover.py RUN_DIR [--json OUT]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from matrix_overlap import records_of

CLASSES = {(0, 0): "aligned", (1, 1): "same_offset"}           # (else different_offsets)


def klass(src: int, dst: int) -> str:
    return CLASSES.get((src, dst), "different_offsets")


def cycles(run: Path) -> dict:
    """(class, cached, state, layout) -> {size: {(src, dst): {method: cycles}}}"""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for e, recs in records_of(run).values():
        if e["family"] != "dma_fine":
            continue
        a = e["axes"]
        key = (klass(a["src_off"], a["dst_off"]), a["cached"], a["cache_state"], a.get("layout", "L0"))
        out[key][a["bytes"]][(a["src_off"], a["dst_off"])][e["method"]] = int(recs[0]["h0_cycles"])
    return out


def crossover(by_size: dict) -> tuple[int | None, list[int]]:
    """The smallest size from which the DMA wins at every larger size (at every offset pair), and the sizes where
    it wins below that (isolated wins, not taken)."""
    sizes = sorted(by_size)
    wins = {s: all(p["dma"] < p["cpu_copy"] for p in by_size[s].values()) for s in sizes}
    first = None
    for s in reversed(sizes):
        if not wins[s]:
            break
        first = s
    isolated = [s for s in sizes if wins[s] and (first is None or s < first)]
    return first, isolated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    data = cycles(args.run)
    rows = {}
    for key, by_size in sorted(data.items()):
        incomplete = [s for s, pairs in by_size.items() for p in pairs.values() if set(p) != {"dma", "cpu_copy"}]
        first, isolated = crossover(by_size) if not incomplete else (None, [])
        rows["/".join(map(str, key))] = dict(crossover=first, isolated_wins=isolated, sizes=len(by_size),
                                             incomplete=sorted(set(incomplete)))
    policy = {}
    for k in ("aligned", "same_offset", "different_offsets"):
        mine = {key: v for key, v in rows.items() if key.startswith(k + "/")}
        firsts = [v["crossover"] for v in mine.values()]
        by_cached = {c: max((v["crossover"] for key, v in mine.items() if key.split("/")[1] == str(c)
                             and v["crossover"] is not None), default=None) for c in (0, 1)}
        policy[k] = dict(threshold=None if None in firsts or not firsts else max(firsts),
                         lowest=min((f for f in firsts if f is not None), default=None), by_cached=by_cached,
                         points=len(mine), without_crossover=[key for key, v in mine.items() if v["crossover"] is None])
    result = dict(revision=manifest["source"]["revision"], dirty=manifest["source"]["dirty"],
                  counts=manifest["counts"], policy=policy, rows=rows)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    print(f"revision {result['revision'][:7]}{' (DIRTY)' if result['dirty'] else ''}; {manifest['counts']}")
    for k, p in policy.items():
        print(f"  {k}: threshold {p['threshold']} bytes (the crossovers {p['lowest']} to {p['threshold']} over "
              f"{p['points']} points: cached 0/1 {p['by_cached'][0]}/{p['by_cached'][1]}); "
              f"no crossover in the sweep: {len(p['without_crossover'])}")
    bad = [key for key, v in rows.items() if v["incomplete"]]
    if bad:
        print(f"  INCOMPLETE: {bad[:5]}")
    # (every size of the different offsets' class at all four of the sweep's pairs, every class a threshold)
    short = [f"{key}/{s}" for key, by in data.items() if key[0] == "different_offsets"
             for s, pairs in by.items() if len(pairs) != 4]
    if short:
        print(f"  SIZES WITHOUT ALL FOUR OFFSET PAIRS: {short[:5]}")
    none = [k for k, p in policy.items() if p["threshold"] is None]
    if none:
        print(f"  NO THRESHOLD: {none}")
    return 0 if not result["dirty"] and not bad and not short and not none and manifest["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
