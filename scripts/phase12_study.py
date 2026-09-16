#!/usr/bin/env python3
"""Phase 12 streaming ECG study: capture, compare and audit.

Runs the heterogeneous pipeline on the all-engine SoC, validates every record
with the strict v10 validator and the independent pipeline oracle, and retains
independently fresh repeats.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import asterbench_v10 as bench
import workload_reference as reference

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase12.study.v1"
NAME = "streaming_ecg"


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance() -> dict:
    return {"revision": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))}


def capture_record() -> str:
    result = subprocess.run(["make", "-s", "ecg"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"capture failed:\n{result.stdout}\n{result.stderr}")
    record = (ROOT / "build" / "streaming_ecg.record").read_text()
    parsed = bench.validate_line(record, name=NAME)
    reference.verify(parsed, NAME)
    return record


def capture(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    repeats = []
    for _ in range(args.repeats):
        record = capture_record()
        repeats.append(record)
        parsed = bench.validate_line(record, name=NAME)
        print(f"repeat {len(repeats)}/{args.repeats}: cycles={parsed['cycles']} "
              f"checksum={parsed['checksum']:#010x}", flush=True)
    study = {"schema": SCHEMA, "provenance": provenance(), "name": NAME, "repeats": repeats}
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n")
    print(f"wrote {study_path} ({len(repeats)} repeats)")
    return 0


def audit(args: argparse.Namespace) -> int:
    study = json.loads(Path(args.study).read_text())
    require = bench.require
    require(study.get("schema") == SCHEMA and study["name"] == NAME, "study is not a Phase 12 study")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(study["repeats"], "study has no repeats")
    parsed = [bench.validate_line(record, name=NAME) for record in study["repeats"]]
    for record in parsed:
        reference.verify(record, NAME)
    require(all(item == parsed[0] for item in parsed), "fresh repeats do not reproduce the record")
    print(f"PASS: Phase 12 study audit ({len(parsed)} repeats, cycles={parsed[0]['cycles']}, "
          f"checksum={parsed[0]['checksum']:#010x})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", required=True)
    cap.add_argument("--repeats", type=int, default=3)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    args = parser.parse_args()
    if args.action == "capture":
        return capture(args)
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
