#!/usr/bin/env python3
"""Count the buffers OpenROAD's repair steps inserted in LibreLane runs, by cell.

For each run (a LibreLane run directory) this reads the final netlist and
counts the cells whose instance names carry a repair step's prefix (`fanout`,
`load_slew`, `wire`, `max_cap`, `input`, `output`, `rebuffer`, `split`, `hold`),
grouped by prefix and cell type; the delay cells (`dlygate*`, `dlymetal*`,
`clkdlybuf*`) among them, hold repair's (`hold`) apart from the rest; and the
longest serial chain of setup repair's `rebuffer` cells (each driving the
next). From the slow corner's post-route max.rpt it counts the failing
register-to-register paths listed (one per endpoint) and how many of them pass
through a delay cell, and the most delay-cell time on one of them. The SHA-256
of each file read is printed with the counts.

    buffer_census.py asic/sky130/runs/p18-aster-jt asic/sky130/runs/p18-aster-nodly-u40
"""

from __future__ import annotations

import collections
import hashlib
import re
import sys
from pathlib import Path

PREFIXES = ("fanout", "load_slew", "wire", "max_cap", "input", "output", "rebuffer", "split", "hold")
DELAY = re.compile(r"^(dlygate|dlymetal|clkdlybuf)")
SLOW = "max_ss_100C_1v60"


def census(run: Path) -> list[str]:
    netlist = sorted((run / "final/nl").glob("*.nl.v"))[0]
    report = sorted(run.glob(f"*-openroad-stapostpnr/{SLOW}/max.rpt"),
                    key=lambda p: int(p.parts[-3].split("-")[0]))[-1]
    text = netlist.read_text()
    cells = collections.Counter()
    for cell, inst in re.findall(r"sky130_fd_sc_hd__(\w+)\s+(\\?\S+)\s*\(", text):
        prefix = re.match(r"\\?([a-z_]+?)(?:\d|$)", inst)
        if prefix and prefix.group(1).rstrip("_") in PREFIXES:
            cells[(prefix.group(1).rstrip("_"), cell)] += 1
    # Setup repair's rebuffer cells: the longest chain in which each drives the next.
    feeds, driver = {}, {}
    for inst, pins in re.findall(r"sky130_fd_sc_hd__\w+\s+(rebuffer\d+)\s*\((.*?)\);", text, re.S):
        a, x = re.search(r"\.A\(([^)]*)\)", pins), re.search(r"\.X\(([^)]*)\)", pins)
        if a and x:
            feeds[inst] = a.group(1).strip()
            driver[x.group(1).strip()] = inst
    depth: dict[str, int] = {}
    for start in feeds:
        trail, inst = [], start
        while inst and inst not in depth:
            trail.append(inst)
            inst = driver.get(feeds[inst])
        base = depth.get(inst, 0) if inst else 0
        for step in reversed(trail):
            base += 1
            depth[step] = base
    chain = max(depth.values(), default=0)
    delay_hold = sum(n for (prefix, cell), n in cells.items() if DELAY.match(cell) and prefix == "hold")
    delay_design = sum(n for (prefix, cell), n in cells.items() if DELAY.match(cell) and prefix != "hold")
    failing = with_delay = 0
    most = 0.0
    rpt = report.read_text()
    for block in rpt.split("Startpoint:")[1:]:
        if block[:block.index("Path Group")].count("flip-flop") != 2:
            continue
        slack = float(re.search(r"(-?[\d.]+)\s+slack", block).group(1))
        if slack >= 0:
            break
        failing += 1
        arrival = block.split("data arrival time")[0]
        delays = [float(m.group(1)) for m in re.finditer(
            r"\s(\d+\.\d+)\s+\d+\.\d+\s+[\^v]\s+\S+/X \(sky130_fd_sc_hd__(?:dlygate|dlymetal|clkdlybuf)", arrival)]
        if delays:
            with_delay += 1
            most = max(most, sum(delays))
    lines = [f"== {run.name}",
             f"   {hashlib.sha256(netlist.read_bytes()).hexdigest()}  {netlist.relative_to(run)}",
             f"   {hashlib.sha256(report.read_bytes()).hexdigest()}  {report.relative_to(run)}",
             f"   delay cells inserted by design repair: {delay_design}, by hold repair: {delay_hold}",
             f"   longest serial chain of rebuffer cells: {chain}",
             f"   {SLOW}: failing register-to-register paths listed {failing}, "
             f"through a delay cell {with_delay}, most delay-cell time on one {most:.2f} ns"]
    for (prefix, cell), n in sorted(cells.items(), key=lambda item: -item[1])[:12]:
        lines.append(f"   {prefix:10s} {cell:18s} {n}")
    return lines


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        print("\n".join(census(Path(arg))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
