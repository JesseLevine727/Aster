#!/usr/bin/env python3
"""The board run's reference (tuning.md §7.2): every R program of a capture simulated as the board runs it
(tb_soc's +board mode, which reads the board's registers: tohost's cycle and hart 0's instructions retired at it,
and the window's), on the capture's own simulation (its hash checked against every entry's). Each must end as
the capture's simulation did (status and tohost's cycle); the board's loads are then compared with it
(scripts/matrix_board.py --reference) for hart 0's retired count, which the capture's status line does not carry.

  matrix_board_reference.py RUN_DIR... --output FILE [--jobs N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import matrix
import matrix_board

FIELDS = ("tohost_cycles", "tohost_retired", "window_cycles", "window_retired", "console_bytes")


def simulate(sim: Path, program: dict) -> dict:
    command = [str(sim), f"+bin={program['binary']}", f"+tohost={matrix_board.tohost(program['elf']):x}",
               f"+max_cycles={matrix.MAX_CYCLES}", "+board"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=36000)
    line = next((l for l in result.stdout.splitlines() if l.startswith("BOARD ")), "")
    out = dict(id=program["id"], status=line.split()[1] if len(line.split()) > 1 else f"(none) {result.stderr[-200:]}")
    for token in line.split()[2:]:
        key, _, value = token.partition("=")
        if key in FIELDS:
            out[key] = int(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parser.parse_args()
    sim = matrix.SIM_DIR / "soc_dev"
    sim_sha = hashlib.sha256(sim.read_bytes()).hexdigest()
    entries = {e["id"]: e for run in args.runs for e in json.loads((run / "manifest.json").read_text())["entries"]}
    programs = matrix_board.r_programs(args.runs)
    wrong = [p["id"] for p in programs if entries[p["id"]]["sim_sha256"] != sim_sha]
    if wrong:
        print(f"FAIL: {len(wrong)} programs were captured on another simulation ({wrong[:3]})")
        return 1
    rows, problems = [], []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for n, row in enumerate(pool.map(lambda p: simulate(sim, p), programs), 1):
            want = entries[row["id"]]["soc"]
            if row["status"] != want["status"] or row.get("tohost_cycles") != want["cycles"]:
                problems.append(f"{row['id']}: {row['status']} at {row.get('tohost_cycles')}, captured "
                                f"{want['status']} at {want['cycles']}")
            rows.append(row)
            if n % 500 == 0:
                print(f"{n} of {len(programs)}", flush=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = dict(sim="soc_dev", sim_sha256=sim_sha, revision=revision,
               captures=sorted({json.loads((r / "manifest.json").read_text())["source"]["revision"] for r in args.runs}),
               programs=len(rows), problems=problems, rows=sorted(rows, key=lambda r: r["id"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{'PASS' if not problems else 'FAIL'}: {len(rows)} R programs simulated as on the board; "
          f"{len(problems)} not as captured")
    for p in problems[:20]:
        print(f"  {p}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
