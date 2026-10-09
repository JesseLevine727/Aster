#!/usr/bin/env python3
"""Two matrix captures compared (20.4 step 4; 20.5's tuning): for every entry and window both captured, how far
hart 0's cycles moved.

  matrix_compare.py OLD_RUN NEW_RUN [--identical]

Per pair it prints the windows compared, how many are unchanged, the median and the range of the relative change
and the three largest. --identical requires every common captured entry to have the same firmware, simulation
and records, byte for byte (two captures of the same build), and at least one in common; it exits 1 otherwise.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from matrix_overlap import records_of


def moved(old: Path, new: Path) -> str:
    a, b = records_of(old), records_of(new)
    d = []
    for i, (_, recs) in a.items():
        if i not in b:
            continue
        for r, s in zip(recs, b[i][1]):
            x, y = int(r["h0_cycles"]), int(s["h0_cycles"])
            if r["window"] == s["window"] and x:
                d.append(((y - x) / x, i, r["window"], x, y))
    if not d:
        return f"{old.name} -> {new.name}: no windows in common"
    big = sorted(d, key=lambda t: abs(t[0]), reverse=True)[:3]
    return (f"{old.name} -> {new.name}: {len(d)} windows, {sum(t[0] == 0 for t in d)} unchanged, median |change| "
            f"{statistics.median(abs(t[0]) for t in d) * 100:.3f}%, range {min(t[0] for t in d) * 100:+.2f}% to "
            f"{max(t[0] for t in d) * 100:+.2f}%; largest: " + "; ".join(f"{t[1]} {t[2]} {t[3]} -> {t[4]}" for t in big))


def identical(old: Path, new: Path) -> tuple[int, list]:
    a = {e["id"]: e for e in json.loads((old / "manifest.json").read_text())["entries"]}
    b = {e["id"]: e for e in json.loads((new / "manifest.json").read_text())["entries"]}
    differ = [f"{i}: listed in one capture" for i in sorted(set(a) ^ set(b))]
    count = 0
    for i, e in b.items():
        if i not in a or e["status"] != "captured" or a[i]["status"] != "captured":
            if i in a and e["status"] != a[i]["status"]:
                differ.append(f"{i}: {a[i]['status']} -> {e['status']}")
            continue
        count += 1
        for k in ("firmware_sha256", "sim_sha256"):
            if a[i].get(k) != e.get(k):
                differ.append(f"{i}: its {k} differs")
        if (old / a[i]["records"]).read_bytes() != (new / e["records"]).read_bytes():
            differ.append(f"{i}: its records differ")
    return count, differ


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--identical", action="store_true")
    args = parser.parse_args()
    if args.identical:
        count, differ = identical(args.old, args.new)
        ok = count > 0 and not differ                     # (no common entry proves nothing)
        print(f"{'PASS' if ok else 'FAIL'}: {args.old.name} and {args.new.name}: {count} captured entries, "
              f"{len(differ)} differ in firmware, simulation or records")
        for d in differ[:20]:
            print(f"  {d}")
        return 0 if ok else 1
    print(moved(args.old, args.new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
