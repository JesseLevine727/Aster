#!/usr/bin/env python3
"""v1.2 NPU geometry study: capture and audit.

Runs the engine scoreboard and the NPU convolution workload at 2x2, 4x4 and 8x8,
retains the raw outputs, and checks that every geometry produces the same
independent-oracle checksum.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import asterbench_v10 as bench
import workload_reference as reference

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.v1.2.study.v1"
GEOMETRIES = [2, 4, 8]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance():
    return {"revision": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))}


def run(command):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({' '.join(command)}):\n{result.stdout}\n{result.stderr}")
    return result.stdout


def capture_engine(geometry):
    build = f"build/npu_engine_r{geometry}"
    (ROOT / build).mkdir(parents=True, exist_ok=True)
    output = run(["make", "-s", "npu-engine", f"NPU_ROWS={geometry}", f"NPU_COLS={geometry}",
                  f"BUILD_DIR={build}"])
    match = re.search(r"reads=(\d+) writes=(\d+) cycles=(\d+)", output)
    require(match is not None, f"engine {geometry}x{geometry} did not report counters")
    return {"output": output.strip(), "reads": int(match.group(1)),
            "writes": int(match.group(2)), "cycles": int(match.group(3))}


def capture_conv(geometry):
    build = f"build/npu_conv_r{geometry}"
    (ROOT / build).mkdir(parents=True, exist_ok=True)
    output = run(["make", "-s", "conv-engine", "CONV_ENGINE=npu", f"NPU_ROWS={geometry}",
                  f"NPU_COLS={geometry}", f"BUILD_DIR={build}"])
    record = (ROOT / build / "conv2d_npu.record").read_text()
    parsed = bench.validate_line(record, name="conv2d_npu")
    reference.verify(parsed, "conv2d_npu")
    return {"output": output.strip(), "record": record, "cycles": parsed["cycles"],
            "checksum": f"0x{parsed['checksum']:08x}"}


def capture(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    geometries = []
    for geometry in GEOMETRIES:
        engine = capture_engine(geometry)
        conv = capture_conv(geometry)
        geometries.append({"geometry": geometry, "engine": engine, "conv2d_npu": conv})
        print(f"{geometry}x{geometry}: engine reads={engine['reads']} cycles={engine['cycles']} "
              f"conv cycles={conv['cycles']} checksum={conv['checksum']}", flush=True)
    study = {"schema": SCHEMA, "provenance": provenance(), "geometries": geometries}
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n")
    print(f"wrote {study_path}")
    return 0


def audit_study(study):
    require(study.get("schema") == SCHEMA, "study is not a v1.2 study")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require([item["geometry"] for item in study["geometries"]] == GEOMETRIES,
            "study geometries differ")
    checksums = set()
    for item in study["geometries"]:
        parsed = bench.validate_line(item["conv2d_npu"]["record"], name="conv2d_npu")
        reference.verify(parsed, "conv2d_npu")
        require(f"0x{parsed['checksum']:08x}" == item["conv2d_npu"]["checksum"],
                "retained checksum differs from re-evaluation")
        checksums.add(item["conv2d_npu"]["checksum"])
    require(len(checksums) == 1, "geometries disagree on the oracle checksum")
    return {"geometries": len(GEOMETRIES), "checksum": checksums.pop()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", required=True)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    args = parser.parse_args()
    if args.action == "capture":
        return capture(args)
    result = audit_study(json.loads(Path(args.study).read_text()))
    print(f"PASS: v1.2 geometry study audit ({result['geometries']} geometries, "
          f"checksum={result['checksum']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
