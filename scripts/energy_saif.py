#!/usr/bin/env python3
r"""Phase 21 (docs/phase21.md §3.3): a run's interval SAIF files grouped into its records' windows and merged, one
SAIF a window, with the checks:
  - every file holds the same nets, in the same instances;
  - each net's T0 + T1 + TX + TZ + TB is its file's duration;
  - no net is unknown (TX) but Vivado's unconnected pins (NLW_*_UNCONNECTED), which drive nothing;
  - the windows: the intervals, in order, partition exactly into the records' windows, in the console's order, each
    window's intervals' durations summing to its record's cycles (h0_cycles) × 10 ns;
  - the alignment: the clock's toggles in a merged window are twice its cycles.
The merged file sums each net's T0, T1, TX, TZ, TB, TC (and IG where present) and the durations. It is written for
Vivado's read_saif (step 1 found two things xsim writes that Vivado cannot read):
  - an escaped name, which xsim writes "\\name\ " (name's own [ ] . escaped), is written as name with those
    escapes, the form Vivado matches (as the bus bits xsim writes, "s_axi_wstrb\[1\]");
  - a net whose name xsim garbled ("(null)" for a generate scope in it: 55 of the netlist's nets) is left out; it
    matches no design net, and the count is recorded.

  energy_saif.py RUN_DIR [--keep-intervals]
reads RUN_DIR/run.json, RUN_DIR/run.console and RUN_DIR/saif/interval*.saif; writes RUN_DIR/windows.json and
RUN_DIR/saif/window<k>_<name>.saif (xz-compressed alongside with --xz).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import re
import sys
from pathlib import Path

PERIOD_PS = 10_000
FIELDS = ("T0", "T1", "TX", "TZ", "TB", "TC", "IG")
# a net's line: its name (escaped names end in an escaped space, "\\ "), its separator, its values
NET = re.compile(r"^(\s*)\((.+?)(\s+)((?:\((?:T0|T1|TX|TZ|TB|TC|IG) \d+\)\s*)+)\)\s*$")
VALUE = re.compile(r"\((T0|T1|TX|TZ|TB|TC|IG) (\d+)\)")


class SaifError(Exception):
    pass


def parse(path: Path) -> tuple[int, list[str], list[tuple]]:
    """(duration, the file's lines as a template, and each net's (line index, name, values)). The template keeps
    the structure (instances, NET/PORT blocks) so a merged file is written in the same form."""
    lines = path.read_text().splitlines()
    duration = None
    nets = []
    for i, line in enumerate(lines):
        if duration is None and "(DURATION" in line:
            duration = int(line.split()[1].rstrip(")"))
            continue
        m = NET.match(line)
        if m:
            values = dict.fromkeys(FIELDS, None)
            for key, value in VALUE.findall(m.group(4)):
                values[key] = int(value)
            nets.append((i, m.group(2), values))
        elif "(T0" in line:
            raise SaifError(f"{path}: line {i + 1} not read: {line.strip()[:120]}")
    if duration is None:
        raise SaifError(f"{path}: no DURATION")
    return duration, lines, nets


def check_file(path: Path, duration: int, nets: list[tuple]) -> None:
    for _, name, v in nets:
        if sum(v[k] or 0 for k in ("T0", "T1", "TX", "TZ", "TB")) != duration:
            raise SaifError(f"{path.name}: {name}'s times are not the duration {duration}")
        if v["TX"] and not name.startswith("NLW_"):
            raise SaifError(f"{path.name}: {name} unknown for {v['TX']} ps")


def records_of(console: str) -> list[dict]:
    out = []
    for line in console.splitlines():
        if line.startswith("ASTERBENCH,"):
            fields = dict(t.split("=", 1) for t in line.split(",")[1:])
            out.append(dict(window=fields["window"], name=fields["name"], cycles=int(fields["h0_cycles"])))
    return out


def partition(intervals: list[dict], records: list[dict]) -> list[list[int]]:
    """Each record's intervals, in order: their durations must sum exactly to its cycles."""
    groups, k = [], 0
    for r in records:
        group, total = [], 0
        while k < len(intervals) and total < r["cycles"]:
            cycles = (intervals[k]["end_ps"] - intervals[k]["start_ps"]) // PERIOD_PS
            if (intervals[k]["end_ps"] - intervals[k]["start_ps"]) % PERIOD_PS:
                raise SaifError(f"interval {k} is not a whole number of cycles")
            group.append(k)
            total += cycles
            k += 1
        if total != r["cycles"]:
            raise SaifError(f"the {r['window']} window's intervals sum to {total} cycles, its record {r['cycles']}")
        groups.append(group)
    if k != len(intervals):
        raise SaifError(f"{len(intervals) - k} intervals after the last record's window")
    return groups


ESCAPED = re.compile(r"\\\\((?:\\.|[^\\\s])+)\\ ")


def for_vivado(line: str) -> str:
    return ESCAPED.sub(lambda m: m.group(1), line)


def merge(paths: list[Path], out: Path) -> dict:
    total, template, merged, keys = 0, None, None, None
    for path in paths:
        duration, lines, nets = parse(path)
        check_file(path, duration, nets)
        these = [n for _, n, _ in nets]
        if keys is None:
            template, keys = lines, these
            merged = [dict(v) for _, _, v in nets]
            index = [i for i, _, _ in nets]
        else:
            if these != keys:
                raise SaifError(f"{path.name}: not the same nets as {paths[0].name}")
            for acc, (_, _, v) in zip(merged, nets):
                for f in FIELDS:
                    if v[f] is not None:
                        acc[f] = (acc[f] or 0) + v[f]
        total += duration
    lines = list(template)
    for i, line in enumerate(lines):
        if "(DURATION" in line:
            lines[i] = re.sub(r"\(DURATION\s+\d+\)", f"(DURATION  {total})", line)
            break
    for i, name, acc in zip(index, keys, merged):
        m = NET.match(lines[i])
        lines[i] = f"{m.group(1)}({name}{m.group(3)}" + " ".join(f"({f} {acc[f]})" for f in FIELDS
                                                                if acc[f] is not None) + ")"
    garbled = sum(1 for i in index if "null\\)" in lines[i])
    lines = [for_vivado(line) for line in lines if "null\\)" not in line]
    text = "\n".join(lines) + "\n"
    out.write_text(text)
    clock = next((acc for name, acc in zip(keys, merged) if name == "aclk"), None)
    return dict(duration_ps=total, nets=len(keys), garbled_nets_left_out=garbled, clock_toggles=clock and clock["TC"],
                sha256=hashlib.sha256(text.encode()).hexdigest())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--xz", action="store_true", help="also write each merged SAIF xz-compressed")
    args = parser.parse_args()
    run = json.loads((args.run / "run.json").read_text())
    if not run["ok"]:
        raise SystemExit(f"{run['id']}: the run did not reproduce its references; its activity is not used")
    console = (args.run / "run.console").read_bytes().decode("latin-1")
    records = records_of(console)
    intervals = run["intervals"]
    files = sorted((args.run / "saif").glob("interval*.saif"))
    if len(files) != len(intervals):
        raise SystemExit(f"{len(files)} interval files for {len(intervals)} intervals")
    groups = partition(intervals, records)
    windows = []
    for k, (record, group) in enumerate(zip(records, groups)):
        out = args.run / "saif" / f"window{k}_{record['window']}.saif"
        info = merge([files[i] for i in group], out)
        if info["duration_ps"] != record["cycles"] * PERIOD_PS:
            raise SaifError(f"window {k}: {info['duration_ps']} ps, its record {record['cycles']} cycles")
        if info["clock_toggles"] != 2 * record["cycles"]:
            raise SaifError(f"window {k}: the clock toggled {info['clock_toggles']} times in {record['cycles']} cycles")
        if args.xz:
            (out.parent / (out.name + ".xz")).write_bytes(lzma.compress(out.read_bytes(), preset=9))
        windows.append(dict(window=record["window"], name=record["name"], cycles=record["cycles"],
                            intervals=group, saif=out.name, **info))
    (args.run / "windows.json").write_text(json.dumps(dict(id=run["id"], windows=windows), indent=1) + "\n")
    print(f"PASS {run['id']}: {len(intervals)} intervals in {len(windows)} windows "
          + ", ".join(f"{w['window']} {w['cycles']} cycles" for w in windows))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SaifError as error:
        print(f"FAIL: {error}")
        sys.exit(1)
