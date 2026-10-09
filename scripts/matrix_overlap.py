#!/usr/bin/env python3
"""20.4's overlap gate (docs/soc.md §11: "multicore overlap proven from per-hart counters"), from matrix runs.

For every captured two-worker record it reads each hart's work interval (AsterBench v12's work_start and work_end,
stamped by the hart itself; matrix.md §10.7's rules) and its retired instructions, and classes the record:
  - overlap shown: the two intervals overlap and both harts retired instructions in the window;
  - hart 0 polls: hart 0's interval is 0 and 0 (it only submits, dispatches and polls: its overlap is with an
    engine, read from the engine's counters, not from hart 0's);
  - no overlap: the intervals do not overlap.
Where the same case, window and configuration ran on one worker with the same kernel (DOT8 when the two-worker
record retired DOT8s, else scalar), it also gives the speedup, one worker's cycles over two's: overlap that pays.
Cases with no such twin (two harts streaming, the queues, ping-pong, producer/consumer, dispatch and join, ECG's
pipeline, the DMA beside hart 1) are counted apart.

  matrix_overlap.py RUN_DIR... [--json OUT]    (each RUN_DIR holds a manifest.json and its records)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def records_of(run: Path) -> dict:
    manifest = json.loads((run / "manifest.json").read_text())
    out = {}
    for e in manifest["entries"]:
        if e["status"] != "captured":
            continue
        recs = [dict(t.split("=", 1) for t in line.strip().split(",")[1:])
                for line in (run / e["records"]).read_text().splitlines() if line.startswith("ASTERBENCH,")]
        out[e["id"]] = (e, recs)
    return out


def key(entry: dict, method: str) -> tuple:
    """The same case, configuration and axes but for the workers; the case named for the method."""
    axes = {k: v for k, v in entry["axes"].items() if k != "workers"}
    return (entry["family"], entry["case"].replace(entry["method"], method), method, entry["sim"],
            json.dumps(axes, sort_keys=True))


def analyse(runs: list[Path]) -> dict:
    allrec = {}
    for run in runs:
        allrec.update(records_of(run))
    one = {}
    for e, recs in allrec.values():
        if e["axes"].get("workers") == 1:
            for r in recs:
                one[key(e, e["method"]) + (r["window"],)] = int(r["h0_cycles"])
    rows, classes = [], Counter()
    for e, recs in allrec.values():
        if e["axes"].get("workers") != 2:
            continue
        for r in recs:
            s0, e0, s1, e1 = (int(r[k]) for k in ("h0_work_start", "h0_work_end", "h1_work_start", "h1_work_end"))
            cycles = int(r["h0_cycles"])
            overlap = max(0, min(e0, e1) - max(s0, s1))
            retired = (int(r["h0_retired"]), int(r["h1_retired"]))
            twin = "dot8" if int(r["h0_dot8_retire"]) + int(r["h1_dot8_retire"]) else "scalar"
            single = one.get(key(e, twin) + (r["window"],)) if e["method"] == "multicore" else None
            if s0 == e0 == 0:
                cls = "hart 0 polls"
            elif overlap > 0 and all(retired):
                cls = "overlap shown"
            else:
                cls = "no overlap"
            classes[cls] += 1
            rows.append(dict(id=e["id"], window=r["window"], cycles=cycles, overlap=overlap,
                             overlap_share=round(overlap / cycles, 4) if cycles else 0, retired=retired,
                             twin=twin if single else None, speedup=round(single / cycles, 4) if single else None,
                             cls=cls))
    shown = [r for r in rows if r["cls"] == "overlap shown"]
    paired = [r for r in shown if r["speedup"]]
    return dict(records=len(rows), classes=dict(classes), paired=len(paired),
                unpaired=sorted({r["id"].split("/")[1] for r in shown if not r["speedup"]}),
                overlap_share_min=min((r["overlap_share"] for r in shown), default=None),
                speedup_min=min((r["speedup"] for r in paired), default=None),
                speedup_max=max((r["speedup"] for r in paired), default=None),
                slower=sorted({r["id"] for r in paired if r["speedup"] <= 1.0}),
                no_overlap=sorted({r["id"] for r in rows if r["cls"] == "no overlap"}), rows=rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = analyse(args.runs)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    print(f"two-worker records: {result['records']}; {result['classes']}")
    print(f"overlap shown: at least {result['overlap_share_min']} of the window; {result['paired']} records with a "
          f"one-worker twin, speedups {result['speedup_min']} to {result['speedup_max']}, "
          f"{len(result['slower'])} not faster; no twin: {result['unpaired']}")
    if result["no_overlap"]:                            # (published, not failed: the hand-over outlasts the work)
        print(f"no overlap: {result['no_overlap']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
