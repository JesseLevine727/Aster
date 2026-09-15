#!/usr/bin/env python3
"""Phase 11 four-path MNIST inference study: capture, compare and audit.

Runs the same 32 retained images through scalar, multicore, dot8 and npu on the
all-engine SoC, validates every v9 record with the independent oracle, and
retains per-method cycles plus scalar-relative ratios. Supports independently
fresh repeats.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import asterbench_v9 as bench

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase11.study.v1"
MODEL = ROOT / "docs" / "results" / "phase11" / "model.json"
METHODS = ("scalar", "multicore", "dot8", "npu")


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance(model: dict) -> dict:
    return {"revision": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain")),
            "model_hash": model["hash"]}


def capture_method(method: str, model: dict) -> list[str]:
    result = subprocess.run(["make", "-s", "phase11-infer", f"PHASE11_METHOD={method}"],
                            cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"capture {method} failed:\n{result.stdout}\n{result.stderr}")
    records = [line for line in result.stdout.splitlines(keepends=True) if line.startswith("ASTERBENCH,")]
    if len(records) != len(model["test"]["labels"]):
        raise SystemExit(f"capture {method} emitted {len(records)} records")
    for line in records:
        bench.validate_line(line, model, method=method)
    return records


def summarize(methods: dict[str, list[str]], model: dict) -> dict:
    rows = {}
    for method, records in methods.items():
        parsed = [bench.validate_line(line, model, method=method) for line in records]
        cycles = [r["h0_cycles"] for r in parsed]
        retired = [r["h0_retired"] for r in parsed]
        rows[method] = {
            "images": len(parsed),
            "h0_cycles_total": sum(cycles),
            "h0_cycles_mean": sum(cycles) / len(cycles),
            "h0_cycles_min": min(cycles),
            "h0_cycles_max": max(cycles),
            "h0_retired_total": sum(retired),
            "correct_vs_labels": sum(1 for r in parsed if r["class"] == r["label"]),
        }
    scalar = rows["scalar"]["h0_cycles_mean"]
    for method in METHODS:
        rows[method]["scalar_over_method"] = scalar / rows[method]["h0_cycles_mean"]
    return rows


def capture(args: argparse.Namespace) -> int:
    model = bench.load_model(MODEL)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    repeats = []
    for index in range(args.repeats):
        methods = {method: capture_method(method, model) for method in METHODS}
        repeats.append({"methods": methods, "summary": summarize(methods, model)})
        print(f"repeat {index + 1}/{args.repeats}: " +
              ", ".join(f"{m}={repeats[-1]['summary'][m]['h0_cycles_mean']:.0f}" for m in METHODS), flush=True)
    study = {"schema": SCHEMA, "provenance": provenance(model), "model_hash": model["hash"],
             "repeats": repeats, "summary": repeats[0]["summary"]}
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n")
    print(f"wrote {study_path} ({len(repeats)} repeats)")
    return 0


def audit(args: argparse.Namespace) -> int:
    study = json.loads(Path(args.study).read_text())
    require = bench.require
    require(study.get("schema") == SCHEMA, "study schema is not Phase 11")
    require(study["model_hash"] == bench.load_model(MODEL)["hash"], "study model hash differs")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(study["repeats"], "study has no repeats")
    model = bench.load_model(MODEL)
    for repeat in study["repeats"]:
        require(set(repeat["methods"]) == set(METHODS), "repeat is missing a method")
        for method, records in repeat["methods"].items():
            for line in records:
                bench.validate_line(line, model, method=method)
        require(summarize(repeat["methods"], model) == repeat["summary"], "retained summary disagrees")
    require(study["summary"] == study["repeats"][0]["summary"], "top-level summary disagrees")
    print(f"PASS: Phase 11 study audit ({len(study['repeats'])} repeats x {len(METHODS)} methods x "
          f"{len(model['test']['labels'])} images)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", required=True)
    cap.add_argument("--repeats", type=int, default=1)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    args = parser.parse_args()
    if args.action == "capture":
        return capture(args)
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
