#!/usr/bin/env python3
"""Phase 14 design-space study driver.

Runs a named sweep over the frozen v1.0 parameters, captures one validated
AsterBench v10 record per (configuration, workload), and emits a comparison
table. Every retained record is re-validated against its independent oracle and
carries source/configuration provenance.
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
SCHEMA = "aster.phase14.study.v1"

# workload key -> (make target, extra make vars, record file name)
WORKLOADS = {
    "strided": ("workload", {"WORKLOAD": "strided"}, "workload_strided.record"),
    "sort_search": ("workload", {"WORKLOAD": "sort_search"}, "workload_sort_search.record"),
    "fft": ("workload", {"WORKLOAD": "fft"}, "workload_fft.record"),
    "conv2d": ("workload", {"WORKLOAD": "conv2d"}, "workload_conv2d.record"),
    "coremark": ("coremark", {}, "coremark.record"),
    "dhrystone": ("dhrystone", {}, "dhrystone.record"),
    "reduce_scalar": ("reduce", {"REDUCE_WORKERS": "1"}, "reduce_scalar.record"),
    "reduce_parallel": ("reduce", {"REDUCE_WORKERS": "2"}, "reduce_parallel.record"),
    "conv2d_dot8": ("conv-engine", {"CONV_ENGINE": "dot8"}, "conv2d_dot8.record"),
    "conv2d_npu": ("conv-engine", {"CONV_ENGINE": "npu"}, "conv2d_npu.record"),
    "streaming_ecg": ("ecg", {}, "streaming_ecg.record"),
    "cifar_cnn": ("cifar", {}, "cifar_cnn.record"),
}

MEMORY_WORKLOADS = ["strided", "sort_search", "fft", "conv2d"]

SWEEPS = {
    "memory-latency": {
        "question": "When does memory bandwidth become the bottleneck?",
        "configs": [{"id": f"wait{value}", "vars": {"MEMORY_WAIT_CYCLES": str(value)}}
                    for value in (0, 1, 4, 16, 64)],
        "workloads": MEMORY_WORKLOADS,
    },
    "cache-geometry": {
        "question": "How much do L1 cache sizes affect real workloads?",
        "configs": [
            {"id": "nocache", "vars": {"ENABLE_L1": "0"}},
            {"id": "w4n8", "vars": {"L1_LINE_WORDS": "4", "L1_LINE_COUNT": "8"}},
            {"id": "w4n16", "vars": {"L1_LINE_WORDS": "4", "L1_LINE_COUNT": "16"}},
            {"id": "w4n32", "vars": {"L1_LINE_WORDS": "4", "L1_LINE_COUNT": "32"}},
            {"id": "w8n16", "vars": {"L1_LINE_WORDS": "8", "L1_LINE_COUNT": "16"}},
        ],
        "workloads": MEMORY_WORKLOADS,
    },
    "core-scaling": {
        "question": "How well does performance scale from one to two cores?",
        "configs": [
            {"id": "h1", "vars": {"HART_COUNT": "1"}, "workloads": ["reduce_scalar"]},
            {"id": "h2", "vars": {"HART_COUNT": "2"},
             "workloads": ["reduce_scalar", "reduce_parallel"]},
        ],
        "workloads": ["reduce_scalar", "reduce_parallel"],
    },
    "compute-placement": {
        "question": "When is a custom instruction enough and when is a separate accelerator justified?",
        "configs": [{"id": "xe", "vars": {}}],
        "workloads": ["conv2d", "conv2d_dot8", "conv2d_npu"],
    },
}

HEX_METRICS = ["cycles", "retired", "memory_transactions", "backing_transactions",
               "cache_accesses", "cache_misses", "dma_bytes", "accelerator_cycles"]


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance():
    return {"revision": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))}


def run_make(target, variables, build_dir):
    command = (["make", "-s", target, f"BUILD_DIR={build_dir}"]
               + [f"{key}={value}" for key, value in variables.items()])
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"capture failed ({' '.join(command)}):\n{result.stdout}\n{result.stderr}")
    return result.stdout


def capture(sweep, config, workload):
    target, workload_vars, record_name = WORKLOADS[workload]
    # Each configuration gets its own build directory so the Verilator model and
    # firmware are rebuilt with the swept parameters instead of being reused.
    build_dir = f"build/dse/{sweep}/{config['id']}"
    (ROOT / build_dir).mkdir(parents=True, exist_ok=True)
    variables = dict(config["vars"])
    variables.update(workload_vars)
    run_make(target, variables, build_dir)
    record = (ROOT / build_dir / record_name).read_text()
    parsed = bench.validate_line(record, name=workload)
    reference.verify(parsed, workload)
    return record, parsed


def parse_fields(record):
    fields = {}
    for token in record.strip().split(",")[1:]:
        key, _, value = token.partition("=")
        fields[key] = value
    return fields


def summarize(study):
    rows = []
    for config in study["configs"]:
        for workload, record in config["records"].items():
            bench.validate_line(record, name=workload)
            fields = parse_fields(record)
            row = {"config": config["id"], "workload": workload, "checksum": fields["checksum"],
                   "size": int(fields["size"]), "iterations": int(fields["iterations"])}
            for metric in HEX_METRICS:
                row[metric] = int(fields[metric], 16)
            rows.append(row)
    return rows


def capture_sweep(args):
    if args.sweep not in SWEEPS:
        raise SystemExit(f"unknown sweep {args.sweep!r}")
    spec = SWEEPS[args.sweep]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    configs = []
    for config in spec["configs"]:
        records = {}
        for workload in config.get("workloads", spec["workloads"]):
            record, parsed = capture(args.sweep, config, workload)
            records[workload] = record
            print(f"{args.sweep} {config['id']} {workload}: cycles={parsed['cycles']} "
                  f"checksum=0x{parsed['checksum']:08x}", flush=True)
        configs.append({"id": config["id"], "vars": config["vars"], "records": records})
    study = {"schema": SCHEMA, "sweep": args.sweep, "question": spec["question"],
             "provenance": provenance(), "configs": configs, "summary": []}
    study["summary"] = summarize(study)
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n")
    print(f"wrote {study_path} ({len(configs)} configs x {len(spec['workloads'])} workloads)")
    return 0


def audit_sweep(study):
    require = bench.require
    require(study.get("schema") == SCHEMA, "study is not a Phase 14 study")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(study["configs"], "study has no configurations")
    for config in study["configs"]:
        require(config["records"], "configuration has no records")
        for workload, record in config["records"].items():
            parsed = bench.validate_line(record, name=workload)
            reference.verify(parsed, workload)
    require(study["summary"] == summarize(study), "study summary differs from re-evaluation")
    return {"configs": len(study["configs"]),
            "records": sum(len(config["records"]) for config in study["configs"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--sweep", required=True, choices=sorted(SWEEPS))
    cap.add_argument("--output", required=True)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    args = parser.parse_args()
    if args.action == "capture":
        return capture_sweep(args)
    result = audit_sweep(json.loads(Path(args.study).read_text()))
    print(f"PASS: Phase 14 study audit ({result['configs']} configs, {result['records']} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
