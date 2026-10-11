#!/usr/bin/env python3
"""20.5's copy policy (docs/tuning.md §4.1): the copy helper's thresholds from the finer sweep (matrix.py's dma_fine,
on every layout), end to end, checked against software/runtime/aster_copy.c's aster_copy_policy.

The helper (software/runtime/aster_copy.c) classes a copy by word alignment: both aligned, the same offset in a word,
or different offsets. Its CPU copy (step 3b) is v1's fair copy at the same offset; at different offsets, the shift
copy ("copy__aster" in the sweep) from the policy's `shift` bytes, v1's below. For each class, the destination cached
or not, warm and cold, and each layout, a crossover is the smallest size from which one copy is faster at every
larger size of the sweep (for different offsets, at all six of the sweep's offset pairs). A threshold is the largest
crossover over the destination's state, warm and cold, and the layouts: tuning.md §3's rule, a change only where it
wins on all of them.
- shift: the shift copy against v1's fair copy, at different offsets;
- aligned, same_offset: the DMA against v1's fair copy;
- different_offsets: the DMA against the helper's CPU copy there (the shift copy from `shift` bytes, v1's below).
The header's thresholds must be these; it exits 1 otherwise.

  matrix_crossover.py RUN_DIR [--json OUT]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matrix
from matrix_overlap import records_of

CLASSES = {(0, 0): "aligned", (1, 1): "same_offset"}           # (else different_offsets)


def klass(src: int, dst: int) -> str:
    return CLASSES.get((src, dst), "different_offsets")


def cycles(run: Path) -> dict:
    """(class, cached, state, layout) -> {size: {(src, dst): {copy: cycles}}}, copy one of fair, dma and shift."""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for e, recs in records_of(run).values():
        if e["family"] != "dma_fine":
            continue
        a = e["axes"]
        copy = "shift" if e["case"] == "copy__aster" else "dma" if e["method"] == "dma" else "fair"
        key = (klass(a["src_off"], a["dst_off"]), a["cached"], a["cache_state"], a.get("layout", "L0"))
        out[key][a["bytes"]][(a["src_off"], a["dst_off"])][copy] = int(recs[0]["h0_cycles"])
    return out


def crossover(by_size: dict, faster) -> tuple[int | None, list[int]]:
    """The smallest size from which `faster(size, point)` holds at every larger size (at every offset pair), and
    the sizes where it holds below that (isolated, not taken)."""
    sizes = sorted(by_size)
    wins = {s: all(faster(s, p) for p in by_size[s].values()) for s in sizes}
    first = None
    for s in reversed(sizes):
        if not wins[s]:
            break
        first = s
    return first, [s for s in sizes if wins[s] and (first is None or s < first)]


def threshold(rows: dict, name: str) -> dict:
    firsts = [v[name] for v in rows.values()]
    known = [f for f in firsts if f is not None]
    return dict(threshold=None if None in firsts or not firsts else max(firsts), lowest=min(known, default=None),
                points=len(rows), without_crossover=sum(f is None for f in firsts),
                by_cached={c: max((v[name] for k, v in rows.items() if k.split("/")[1] == str(c)
                                   and v[name] is not None), default=None) for c in (0, 1)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    data = cycles(args.run)
    shift_rows, bad = {}, []
    for key, by_size in sorted(data.items()):
        if key[0] != "different_offsets":
            continue
        if any(set(p) != {"fair", "dma", "shift"} for pairs in by_size.values() for p in pairs.values()):
            bad.append("/".join(map(str, key)))
            continue
        first, isolated = crossover(by_size, lambda s, p: p["shift"] < p["fair"])
        shift_rows["/".join(map(str, key))] = dict(shift=first, isolated=isolated)
    shift = threshold(shift_rows, "shift")
    rows = {}
    for key, by_size in sorted(data.items()):
        name = "/".join(map(str, key))
        if name in bad or any({"fair", "dma"} - set(p) for pairs in by_size.values() for p in pairs.values()):
            bad.append(name)
            continue
        if key[0] == "different_offsets":
            cpu = lambda s, p: p["shift"] if shift["threshold"] is not None and s >= shift["threshold"] else p["fair"]
        else:
            cpu = lambda s, p: p["fair"]
        first, isolated = crossover(by_size, lambda s, p: p["dma"] < cpu(s, p))
        rows[name] = dict(dma=first, isolated_wins=isolated, sizes=len(by_size))
    policy = {k: threshold({n: v for n, v in rows.items() if n.startswith(k + "/")}, "dma")
              for k in ("aligned", "same_offset", "different_offsets")}
    policy["shift"] = shift
    header = matrix.copy_policy()
    derived = {k: v["threshold"] for k, v in policy.items()}
    problems = [f"{k}: the sweep gives {derived[k]}, aster_copy.c {header[k]}" for k in header if derived[k] != header[k]]
    problems += [f"incomplete: {name}" for name in sorted(set(bad))[:5]]
    result = dict(revision=manifest["source"]["revision"], dirty=manifest["source"]["dirty"],
                  counts=manifest["counts"], policy=policy, header=header, problems=problems, rows=rows,
                  shift_rows=shift_rows)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    ok = not problems and not result["dirty"] and manifest["counts"]["failed"] == 0
    print(f"{'PASS' if ok else 'FAIL'}: the copy policy re-derived from {args.run.name} ({result['revision'][:7]}"
          f"{', DIRTY' if result['dirty'] else ''}): " + "; ".join(f"{k} {v}" for k, v in derived.items()))
    for k, p in policy.items():
        print(f"  {k}: {p['threshold']} bytes (the crossovers {p['lowest']} to {p['threshold']} over {p['points']} "
              f"points: cached 0/1 {p['by_cached'][0]}/{p['by_cached'][1]}); no crossover in the sweep: "
              f"{p['without_crossover']}")
    for line in problems:
        print(f"  {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
