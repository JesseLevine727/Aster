#!/usr/bin/env python3
"""Phase 10 cross-engine study: plan, capture, audit and summarize.

The fixed primary plan is defined by ``asterbench_v8.study_plan``. Each capture
builds one AsterBench v8 firmware for one (kernel, method, shape, placement,
cache) configuration, runs it on the coherent all-engine SoC, and validates the
emitted records with the independent v8 oracle before retaining them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import asterbench_v8 as bench

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase10.study.v1"
DEFAULT_SEED = 0x13570000


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance() -> dict[str, object]:
    return {
        "revision": _git("rev-parse", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "seed": f"0x{DEFAULT_SEED:08x}",
        "jobs": 2,
    }


def placement_id(name: str) -> int:
    return list(bench.PLACEMENTS).index(name)


def run_capture(item: dict[str, object]) -> list[str]:
    seed = (DEFAULT_SEED ^ (int(item["capture"]) * 0x9e3779b9)) & bench.MASK32
    command = [
        "make", "-s", "xe-bench",
        f"XE_KERNEL={item['kernel']}", f"XE_METHOD={item['method']}",
        f"XE_M={item['m']}", f"XE_N={item['n']}", f"XE_K={item['k']}",
        f"XE_TAPS={item['taps']}", f"XE_PLACEMENT={placement_id(str(item['placement']))}",
        f"XE_SEED=0x{seed:08x}", f"XE_JOBS={provenance()['jobs']}",
        f"XE_CAPTURE={item['capture']}", f"ENABLE_L1={item['l1']}",
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"capture {item['capture']} failed:\n{result.stdout}\n{result.stderr}")
    lines = result.stdout.splitlines(keepends=True)
    bench.validate_stream(lines, method=str(item["method"]), kernel=str(item["kernel"]),
                          jobs=int(provenance()["jobs"]))
    return [line for line in lines if line.startswith("ASTERBENCH,")]


def summarize(captures: list[dict[str, object]]) -> dict[str, object]:
    rows = []
    for capture in captures:
        for line in capture["records"]:
            record = bench.validate_line(line, method=str(capture["method"]),
                                         kernel=str(capture["kernel"]))
            rows.append({
                "capture": capture["capture"], "kernel": capture["kernel"],
                "method": capture["method"], "placement": capture["placement"],
                "l1": capture["l1"], "m": record["m"], "n": record["n"], "k": record["k"],
                "h0_cycles": record["h0_cycles"], "h0_retired": record["h0_retired"],
                "h1_cycles": record["h1_cycles"],
            })
    ratios = []
    groups: dict[tuple, dict[str, dict[str, int]]] = {}
    for row in rows:
        key = (row["kernel"], row["m"], row["n"], row["k"], row["placement"], row["l1"])
        groups.setdefault(key, {})[row["method"]] = row
    for key, methods in sorted(groups.items()):
        if "scalar" not in methods:
            continue
        scalar = methods["scalar"]["h0_cycles"]
        for method, row in methods.items():
            ratios.append({
                "kernel": key[0], "m": key[1], "n": key[2], "k": key[3],
                "placement": key[4], "l1": key[5], "method": method,
                "h0_cycles": row["h0_cycles"],
                "scalar_over_method": scalar / row["h0_cycles"] if row["h0_cycles"] else None,
            })
    return {"primary_captures": len(captures), "rows": rows, "ratios": ratios}


def capture(args: argparse.Namespace) -> int:
    plan = bench.study_plan()
    if args.limit:
        plan = plan[: args.limit]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    captures = []
    for item in plan:
        records = run_capture(item)
        captures.append({**item, "records": records})
        print(f"capture {item['capture']}/{plan[-1]['capture']} "
              f"{item['kernel']}/{item['method']} k={item['k']} {item['placement']} l1={item['l1']}",
              flush=True)
    study = {
        "schema": SCHEMA,
        "provenance": provenance(),
        "plan": plan,
        "captures": captures,
        "summary": summarize(captures),
    }
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True))
    print(f"wrote {study_path} ({len(captures)} captures)")
    return 0


def audit(args: argparse.Namespace) -> int:
    study = json.loads(Path(args.study).read_text())
    require = bench.require
    require(study.get("schema") == SCHEMA, "study schema is not Phase 10")
    expected = bench.study_plan()
    if args.limit:
        expected = expected[: args.limit]
    require(study["plan"] == expected, "study plan does not match the frozen Phase 10 plan")
    require(len(study["captures"]) == len(expected), "study capture count is incomplete")
    seen = set()
    for capture, planned in zip(study["captures"], expected):
        for key in ("capture", "kernel", "method", "m", "n", "k", "taps", "placement", "l1"):
            require(capture[key] == planned[key], f"capture {planned['capture']} disagrees on {key}")
        require(capture["capture"] not in seen, "duplicate capture index")
        seen.add(capture["capture"])
        for line in capture["records"]:
            bench.validate_line(line, method=str(planned["method"]), kernel=str(planned["kernel"]),
                                jobs=int(study["provenance"]["jobs"]))
    summary = summarize(study["captures"])
    require(summary == study["summary"], "retained summary disagrees with recomputation")
    ratios = summary["ratios"]
    require(ratios, "study has no ratios")
    print(f"PASS: Phase 10 study audit ({len(study['captures'])} captures, {len(ratios)} ratio rows)")
    return 0


def compare(args: argparse.Namespace) -> int:
    left = json.loads(Path(args.left).read_text())
    right = json.loads(Path(args.right).read_text())
    require = bench.require
    require(left.get("schema") == SCHEMA and right.get("schema") == SCHEMA, "compare requires Phase 10 studies")
    require(left["plan"] == right["plan"], "compared studies use different plans")
    require(left["summary"] == right["summary"],
            "repeated study disagrees; independent captures must reproduce exactly")
    print(f"PASS: Phase 10 fresh repeat reproduces {left['summary']['primary_captures']} captures and "
          f"{len(left['summary']['ratios'])} ratio rows exactly")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("plan")
    cap = sub.add_parser("capture")
    cap.add_argument("--output", required=True)
    cap.add_argument("--limit", type=int, default=0)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    aud.add_argument("--limit", type=int, default=0)
    cmp = sub.add_parser("compare")
    cmp.add_argument("left")
    cmp.add_argument("right")
    args = parser.parse_args()
    if args.action == "plan":
        print(json.dumps(bench.study_plan(), indent=2, sort_keys=True))
        return 0
    if args.action == "capture":
        return capture(args)
    if args.action == "compare":
        return compare(args)
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
