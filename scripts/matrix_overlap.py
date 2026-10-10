#!/usr/bin/env python3
"""20.4's overlap gate (docs/soc.md §11: "multicore overlap proven from per-hart counters"), from matrix runs.

Each two-worker record's harts have work intervals (AsterBench v12's work_start and work_end), set by matrix.md
§10.7's rules: hart 1 stamps its own; hart 0's runs from 0 to the end of its last share where it only waits after
it (stamped), to the window's end where it works after its last wait (it then spans its own wait), or is 0 and 0
where it only starts an engine and polls it. The classes:
  - overlap, stamped: both intervals stamped and overlapping by more than MIN_OVERLAP cycles (both of hart 1's
    stamps), and both harts retired instructions: the harts ran at once;
  - overlap, stamped shares (20.5, tuning.md §6): hart 0's interval spans its wait, but the program stamped each
    hart's share in the window's last stretch (a MATRIX_SHARE line beside the record), and the shares overlap by
    more than MIN_OVERLAP: the harts' own work ran at once;
  - overlap, by speedup: hart 0's interval spans its wait and there are no overlapping shares, but the same kernel
    on one worker (the one-worker record of the same case, axes and window: DOT8 when the two-worker record
    retired DOT8s, else scalar) is slower, so the work ran on both harts at once (matrix.md §10.14; a
    cross-check where shares are stamped);
  - not shown, spans hart 0's wait: hart 0's interval spans its wait and the one-worker twin is not slower than
    the two workers, or there is none (as for ECG's pipelines, whose overlap proof is 20.5's: matrix.md §4.8);
  - no overlap: stamped intervals that do not overlap by more than MIN_OVERLAP (the hand-over outlasts the work);
  - engine overlap: the DMA's overlap cases (hart 0 polls, hart 1 works), against their serial twins, saved
    cycles when positive (§4.3's comparison; polling alone is not freed time);
  - serial by design: the DMA's serial twins.
A tuned variant (20.5, tuning.md §3: its case its original's, with "__" and a tag) is paired with its original,
the same case, method and axes on one worker (or two, where the original has two), for its speedup.

A two-worker record whose hart 0 is 0 and 0 outside the DMA's cases is a stamping fault, as is one missing a
hart's interval: the script lists them and exits 1. Runs from different commits are refused.

  matrix_overlap.py RUN_DIR... [--json OUT]    (each RUN_DIR holds a manifest.json and its records)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

MIN_OVERLAP = 40              # cycles: hart 1's two stamps (matrix.md §10.7: about 20 each)


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


def revisions(runs: list[Path]) -> set:
    return {json.loads((run / "manifest.json").read_text())["source"]["revision"] for run in runs}


def key(entry: dict, case: str, method: str, window: str) -> tuple:
    """The same configuration and axes but for the workers."""
    axes = {k: v for k, v in entry["axes"].items() if k != "workers"}
    return (entry["family"], case, method, entry["sim"], json.dumps(axes, sort_keys=True), window)


def shares_of(entry: dict, window: str) -> tuple | None:
    """The entry's MATRIX_SHARE stamps for the window (each hart's share in its last stretch), if any."""
    for line in entry.get("extras", []):
        if line.startswith("MATRIX_SHARE,"):
            f = dict(t.split("=", 1) for t in line.split(",")[1:])
            if f.get("window") == window:
                return tuple(int(f[k]) for k in ("h0_begin", "h0_end", "h1_begin", "h1_end"))
    return None


def analyse(runs: list[Path]) -> dict:
    allrec = {}
    for run in runs:
        allrec.update(records_of(run))
    cycles_of = {}
    for e, recs in allrec.values():
        for r in recs:
            cycles_of[key(e, e["case"], e["method"], r["window"]) + (e["axes"].get("workers"),)] = int(r["h0_cycles"])
    rows, classes, faults = [], Counter(), []
    for e, recs in allrec.values():
        if e["axes"].get("workers") != 2:
            continue
        for r in recs:
            s0, e0, s1, e1 = (int(r[k]) for k in ("h0_work_start", "h0_work_end", "h1_work_start", "h1_work_end"))
            cycles = int(r["h0_cycles"])
            retired = (int(r["h0_retired"]), int(r["h1_retired"]))
            overlap = max(0, min(e0, e1) - max(s0, s1))
            dma = e["family"] == "dma"
            twin = speedup = None
            if dma and e["case"].startswith("overlap_serial_"):
                cls = "serial by design"
            elif dma:
                twin = e["case"].replace("overlap_", "overlap_serial_")
                single = cycles_of.get(key(e, twin, e["method"], r["window"]) + (2,))
                speedup = single / cycles if single else None
                cls = "engine overlap" if single and single > cycles else "engine: no saving"
            else:
                single = None
                if "__" in e["case"]:                    # a tuned variant: against its original
                    twin = e["case"].split("__")[0]
                    single = next((cycles_of[k] for w in (1, 2) if (k := key(e, twin, e["method"], r["window"]) + (w,))
                                   in cycles_of), None)
                elif e["method"] == "multicore":
                    twin = "dot8" if int(r["h0_dot8_retire"]) + int(r["h1_dot8_retire"]) else "scalar"
                    single = cycles_of.get(key(e, e["case"].replace("multicore", twin), twin, r["window"]) + (1,))
                speedup = single / cycles if single else None
                share = shares_of(e, r["window"])
                share_overlap = (max(0, min(share[1], share[3]) - max(share[0], share[2]))
                                 if share and share[1] > share[0] and share[3] > share[2] else 0)
                if s0 == e0 == 0 or not e1 > s1:
                    cls = "fault"
                    faults.append(f"{e['id']} {r['window']}: hart 0 {s0}-{e0}, hart 1 {s1}-{e1}")
                elif e0 == cycles and share_overlap > MIN_OVERLAP:      # (spans its wait; the shares show it)
                    cls = "overlap, stamped shares"
                    overlap = share_overlap
                elif e0 == cycles:                       # hart 0's interval spans its own wait
                    cls = "overlap, by speedup" if speedup and speedup > 1.0 else "not shown, spans hart 0's wait"
                elif overlap > MIN_OVERLAP and all(retired):
                    cls = "overlap, stamped"
                else:
                    cls = "no overlap"
            classes[cls] += 1
            rows.append(dict(id=e["id"], window=r["window"], cycles=cycles, intervals=[s0, e0, s1, e1],
                             overlap=overlap, retired=retired, twin=twin if speedup else None,
                             speedup=round(speedup, 4) if speedup else None, cls=cls))

    def ids(cls):
        return sorted({r["id"].split("/")[1] for r in rows if r["cls"] == cls})
    stamped = [r for r in rows if r["cls"] == "overlap, stamped"]
    paired = [r for r in rows if r["speedup"] and r["cls"] in ("overlap, stamped", "overlap, stamped shares",
                                                               "overlap, by speedup")]
    return dict(records=len(rows), classes=dict(classes), min_overlap=MIN_OVERLAP,
                stamped_cases=ids("overlap, stamped"), share_cases=ids("overlap, stamped shares"),
                by_speedup_cases=ids("overlap, by speedup"),
                not_shown_cases=ids("not shown, spans hart 0's wait"), no_overlap_cases=ids("no overlap"),
                engine_cases=ids("engine overlap"), engine_no_saving=ids("engine: no saving"),
                stamped_overlap_share_min=min((round(r["overlap"] / r["cycles"], 4) for r in stamped), default=None),
                speedup_min=min((r["speedup"] for r in paired), default=None),
                speedup_max=max((r["speedup"] for r in paired), default=None),
                stamped_not_faster=sorted({r["id"] for r in stamped if r["speedup"] and r["speedup"] <= 1.0}),
                faults=faults, rows=rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if len(revisions(args.runs)) != 1:
        print(f"FAIL: the runs come from several commits: {sorted(revisions(args.runs))}")
        return 1
    result = analyse(args.runs)
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    print(f"two-worker records: {result['records']}; {result['classes']}")
    for k in ("stamped_cases", "share_cases", "by_speedup_cases", "not_shown_cases", "no_overlap_cases", "engine_cases",
              "engine_no_saving"):
        print(f"  {k}: {result[k]}")
    print(f"  stamped overlap at least {result['stamped_overlap_share_min']} of the window; speedups over one "
          f"worker {result['speedup_min']} to {result['speedup_max']}; stamped yet not faster: "
          f"{len(result['stamped_not_faster'])}")
    for f in result["faults"][:20]:
        print(f"  FAULT {f}")
    return 1 if result["faults"] else 0


if __name__ == "__main__":
    sys.exit(main())
