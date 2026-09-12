#!/usr/bin/env python3
"""Capture/audit/compare real AsterBench v3 jobs, counter scoreboards and provenance."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

import asterbench_parallel as bench
from bench_results import ROOT, command, sha, source_state, validate_metadata
from run_parallel_sim import kernel_bounds

OBS_FIELDS = {"boot", "job", "cycles", "kernel_overlap_cycles"} | {
    f"h{h}_kernel_{field}" for h in range(2) for field in ("retired", "first", "last")}
RESULT_FIELDS = {"schema", "serial_boots", "records", "observations", "kernel_bounds", "metadata", "log_sha256"}


def log_records(log, jobs):
    lines = [line for line in log.splitlines(keepends=True) if line.startswith("ASTERBENCH")]
    observations = [json.loads(line[len("ASTEROBS,"):]) for line in log.splitlines() if line.startswith("ASTEROBS,")]
    if len(lines) != jobs*2 or len(observations) != jobs*2:
        raise ValueError("expected all job records and RTL observations for two boots")
    if "PASS: parallel jobs, independent kernel retirement and exact RTL counter scoreboard (2 boots)\n" not in log:
        raise ValueError("missing terminal RTL scoreboard PASS")
    if any(line.startswith("FAIL:") for line in log.splitlines()):
        raise ValueError("failed simulation in capture log")
    return ["".join(lines[:jobs]), "".join(lines[jobs:])], observations


def validate_result(result, log=None):
    if not isinstance(result, dict) or set(result) != RESULT_FIELDS or result["schema"] != "aster.parallel.capture.v1":
        raise ValueError("unsupported/incomplete parallel capture")
    validate_metadata(result["metadata"])
    serial = result["serial_boots"]
    if not isinstance(serial, list) or len(serial) != 2:
        raise ValueError("two complete warm-boot captures required")
    records = [bench.parse_stream(s) for s in serial]
    # JSON equality alone admits True == 1 and 1.0 == 1. Compare canonical
    # serialization as well as parsed values to retain strict numeric types.
    if json.dumps(records, sort_keys=True) != json.dumps(result["records"], sort_keys=True):
        raise ValueError("typed records differ from captured serial bytes")
    if records[0] != records[1]:
        raise ValueError("deterministic simulation changed across warm boots")
    bounds = result["kernel_bounds"]
    if not isinstance(bounds, list) or len(bounds) != 2 or any(type(x) is not int for x in bounds) or not 0 <= bounds[0] < bounds[1] <= 65536:
        raise ValueError("invalid observed ELF kernel bounds")
    observations = result["observations"]
    if not isinstance(observations, list) or len(observations) != 2*len(records[0]):
        raise ValueError("missing per-job RTL observations")
    for i, obs in enumerate(observations):
        boot, index = divmod(i, len(records[0]))
        r = records[boot][index]
        if not isinstance(obs, dict) or set(obs) != OBS_FIELDS or any(type(v) is not int or v < 0 for v in obs.values()):
            raise ValueError("invalid RTL observation fields")
        if obs["boot"] != boot or obs["job"] != index+1 or obs["cycles"] != r["cycles"]:
            raise ValueError("RTL observation belongs to another measurement")
        for h in range(2):
            prefix = f"h{h}_kernel_"
            count, first, last = [obs[prefix+key] for key in ("retired", "first", "last")]
            if h < r["workers"]:
                if not r[f"h{h}_words"]*r["rounds"] <= count <= r[f"h{h}_retired"] or not 0 < first <= last <= r["cycles"] or count > last-first+1:
                    raise ValueError("missing real hardware kernel retirement")
            elif count or first or last:
                raise ValueError("inactive worker unexpectedly executed")
        overlap = 0 if r["workers"] == 1 else max(0, min(obs["h0_kernel_last"], obs["h1_kernel_last"]) -
                                                  max(obs["h0_kernel_first"], obs["h1_kernel_first"]) + 1)
        if obs["kernel_overlap_cycles"] != overlap or (r["workers"] == 2 and r["bytes"] >= 256 and r["rounds"] >= 4 and overlap == 0):
            raise ValueError("invalid/missing independent kernel overlap evidence")
    digest = result["log_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid capture log hash")
    if log is not None:
        if hashlib.sha256(log.encode()).hexdigest() != digest:
            raise ValueError("capture log hash mismatch")
        logged_serial, logged_obs = log_records(log, len(records[0]))
        if logged_serial != serial or logged_obs != observations:
            raise ValueError("capture envelope differs from raw simulator log")
    return records


def validate_options(args):
    if args.memory_wait is None:
        args.memory_wait = args.sync_memory
    if not (args.harts in (1, 2) and 1 <= args.workers <= args.harts and 2 <= args.words <= 1024
            and 1 <= args.rounds <= 64 and 1 <= args.jobs <= 16 and 0 <= args.seed <= 0xffffffff):
        raise ValueError("invalid parallel workload/core configuration")
    if args.l1 not in (0, 1) or args.sync_memory not in (0, 1) or not args.sync_memory <= args.memory_wait <= 1024:
        raise ValueError("invalid cache/memory timing configuration")
    for n in (args.line_words, args.line_count):
        if not 2 <= n <= 1024 or n & (n-1):
            raise ValueError("invalid cache geometry")


def capture(args, build_directory=None):
    validate_options(args)
    if args.output.exists() or args.output.with_suffix(".log").exists():
        raise ValueError("output/log already exists")
    sources, fingerprint = source_state()
    revision = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain"]))
    context = nullcontext(str(build_directory)) if build_directory else tempfile.TemporaryDirectory(prefix="aster-parallel-")
    with context as directory:
        settings = [f"BUILD_DIR={Path(directory).resolve()}", f"RISCV_PREFIX={args.riscv_prefix}",
                    f"HART_COUNT={args.harts}", f"ENABLE_L1={args.l1}", f"SYNC_MEMORY={args.sync_memory}",
                    f"MEMORY_WAIT_CYCLES={args.memory_wait}", f"L1_LINE_WORDS={args.line_words}",
                    f"L1_LINE_COUNT={args.line_count}", f"PARALLEL_WORDS={args.words}",
                    f"PARALLEL_ROUNDS={args.rounds}", f"PARALLEL_JOBS={args.jobs}",
                    f"PARALLEL_WORKERS={args.workers}", f"PARALLEL_SEED=0x{args.seed:08x}"]
        config = json.loads(command(["make", "--no-print-directory", "-s", *settings, "parallel-config"]))
        invocation = ["make", "--output-sync=target", "-j2", *settings, "parallel"]
        process = subprocess.run(invocation, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".log").write_text(process.stdout)
        if process.returncode:
            raise ValueError(f"parallel build/simulation failed: {args.output.with_suffix('.log')}")
        serial, observations = log_records(process.stdout, args.jobs)
        records = [bench.parse_stream(s) for s in serial]
        requested = dict(harts=args.harts, workers=args.workers, bytes=args.words*4, rounds=args.rounds,
                         jobs=args.jobs, base_seed=args.seed, l1=args.l1, sync_memory=args.sync_memory,
                         memory_wait=args.memory_wait, line_words=args.line_words, line_count=args.line_count)
        if any(row[k] != v for rows in records for row in rows for k, v in requested.items()):
            raise ValueError("running configuration differs from requested build")
        compiler = shutil.which(config["compiler"])
        if not compiler:
            raise ValueError("cannot fingerprint compiler")
        result = {"schema": "aster.parallel.capture.v1", "serial_boots": serial, "records": records,
                  "observations": observations, "kernel_bounds": list(kernel_bounds(config["elf"], config["nm"])),
                  "log_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(), "metadata": {
            "revision": revision, "dirty": dirty, "source_sha256": fingerprint, "source_files": sources,
            "compiler": compiler, "compiler_version": command([compiler, "--version"]).splitlines()[0],
            "compiler_sha256": sha(compiler), "cflags": config["cflags"], "ldflags": config["ldflags"],
            "verilator_version": command([config["verilator"], "--version"]),
            "firmware_sha256": sha(config["firmware"]), "elf_sha256": sha(config["elf"]),
            "simulator_sha256": sha(config["simulator"]), "build_command": invocation, "platform": platform.platform()}}
        if source_state() != (sources, fingerprint) or command(["git", "rev-parse", "HEAD"]) != revision:
            raise ValueError("source changed during capture")
        validate_result(result, process.stdout)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(f"PASS: verified parallel capture {args.output}")
    return result


def load(path):
    result = json.loads(path.read_text())
    validate_result(result, path.with_suffix(".log").read_text())
    return result


def compare(a, b):
    aa, bb = validate_result(a)[0], validate_result(b)[0]
    if len(aa) != len(bb):
        raise ValueError("different job counts")
    for x, y in zip(aa, bb):
        for key in ("version", "name", "bytes", "rounds", "jobs", "job", "base_seed", "seed", "checksum"):
            if x[key] != y[key]:
                raise ValueError(f"incomparable parallel workloads: {key}")
    # Reporting across revisions/configurations is allowed but the differences
    # stay visible. A same-platform scaling study must hold these settings fixed.
    cycles = [sum(r["cycles"] for r in rows) for rows in (aa, bb)]
    return {"cycle_speedup": cycles[0]/cycles[1], "summed_job_cycles": cycles,
            "nominal_time_speedup": (cycles[0]/aa[0]["clock_hz"])/(cycles[1]/bb[0]["clock_hz"]),
            "per_job_speedup": [x["cycles"]/y["cycles"] for x, y in zip(aa, bb)],
            "configurations": [{k: row[k] for k in ("harts", "workers", "clock_hz", "l1", "sync_memory", "memory_wait", "line_words", "line_count")}
                               for row in (aa[0], bb[0])],
            "revisions": [r["metadata"]["revision"] for r in (a, b)],
            "compiler_flags": [r["metadata"]["cflags"] for r in (a, b)],
            "includes": "dispatch, private input copy, kernel, shared result copy/checksum and completion wait; excludes initialization/oracle/UART"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="action", required=True)
    run = commands.add_parser("capture")
    run.add_argument("--output", type=Path, required=True)
    for key, default in (("harts", 2), ("workers", 2), ("words", 64), ("rounds", 4), ("jobs", 3),
                         ("l1", 1), ("sync-memory", 0), ("line-words", 4), ("line-count", 16)):
        run.add_argument("--"+key, type=int, default=default)
    run.add_argument("--memory-wait", type=int)
    run.add_argument("--seed", type=lambda s: int(s, 0), default=0x13570000)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    audit = commands.add_parser("audit")
    audit.add_argument("result", type=Path)
    diff = commands.add_parser("compare")
    diff.add_argument("baseline", type=Path); diff.add_argument("candidate", type=Path)
    args = p.parse_args()
    try:
        if args.action == "capture": capture(args)
        elif args.action == "audit": load(args.result); print("PASS: parallel capture audit")
        else: print(json.dumps(compare(load(args.baseline), load(args.candidate)), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
