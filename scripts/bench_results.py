#!/usr/bin/env python3
"""Capture real AsterBench runs with provenance, or compare saved v2 results."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

from asterbench import COUNTERS, parse_record

ROOT = Path(__file__).resolve().parents[1]
METADATA_FIELDS = {
    "revision", "dirty", "source_sha256", "source_files", "compiler", "compiler_version",
    "compiler_sha256", "cflags", "ldflags", "verilator_version", "firmware_sha256",
    "elf_sha256", "simulator_sha256", "build_command", "platform",
}


def command(args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_state():
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=ROOT).decode().split("\0")
    inputs = {name: sha(ROOT / name) for name in sorted(set(names)) if name == "Makefile" or
              name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/"))}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return inputs, fingerprint


def validate_result(result):
    if not isinstance(result, dict) or type(result.get("schema")) is not int or result["schema"] != 1:
        raise ValueError("unsupported result schema")
    if not all(key in result for key in ("serial_record", "record", "metadata")):
        raise ValueError("incomplete result envelope")
    record = parse_record(result["serial_record"])
    if (result["record"] != record or not isinstance(result["record"], dict) or
            any(type(result["record"][key]) is not type(value) for key, value in record.items())):
        raise ValueError("parsed result disagrees with captured serial bytes")
    validate_metadata(result["metadata"])
    return record


def validate_metadata(metadata):
    """Shared source/toolchain manifest contract for v2 and v3 captures."""
    if not isinstance(metadata, dict) or set(metadata) != METADATA_FIELDS:
        raise ValueError("missing/unknown provenance fields")
    if (not isinstance(metadata["dirty"], bool) or not isinstance(metadata["revision"], str) or
            not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", metadata["revision"])):
        raise ValueError("invalid revision provenance")
    for key in METADATA_FIELDS - {"source_files", "dirty", "build_command"}:
        if not isinstance(metadata[key], str) or not metadata[key]:
            raise ValueError(f"invalid metadata: {key}")
        if key.endswith("sha256") and not re.fullmatch(r"[0-9a-f]{64}", metadata[key]):
            raise ValueError(f"invalid hash: {key}")
    if (not isinstance(metadata["build_command"], list) or not metadata["build_command"] or
            any(not isinstance(arg, str) or not arg for arg in metadata["build_command"])):
        raise ValueError("missing build command")
    sources = metadata["source_files"]
    if not isinstance(sources, dict) or not sources or any(
        not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for name, value in sources.items()
    ):
        raise ValueError("invalid source manifest")
    if hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest() != metadata["source_sha256"]:
        raise ValueError("source manifest fingerprint mismatch")


def validate_options(args):
    if args.memory_wait is None:
        args.memory_wait = args.sync_memory
    if args.l1 not in (0, 1) or args.sync_memory not in (0, 1) or not args.sync_memory <= args.memory_wait <= 1024:
        raise ValueError("invalid cache/memory timing configuration")
    for value in (args.line_words, args.line_count):
        if not 2 <= value <= 1024 or value & (value-1):
            raise ValueError("cache dimensions must be powers of two, 2..1024")
    if args.workload not in ("memcpy", "walk_sequential", "walk_random"):
        raise ValueError("unsupported workload")
    if not 2 <= args.words <= 4096 or args.words & (args.words-1):
        raise ValueError("workload words must be a power of two, 2..4096")
    if not 1 <= args.repetitions <= 64 or not 0 <= args.seed <= 0xffffffff:
        raise ValueError("invalid repetitions or seed")


def expected_checksum(name, words, repetitions, seed):
    if name in ("walk_sequential", "walk_random"):
        return (words * (words-1) // 2 * repetitions) & 0xffffffff
    if name != "memcpy":
        raise ValueError("no workload reference available")
    checksum = 0
    for index in range(words):
        checksum = ((checksum * 33) ^ (seed ^ (index * 0x1021))) & 0xffffffff
    return checksum


def capture(args, build_directory=None):
    validate_options(args)
    if args.output.exists() or args.output.with_suffix(".log").exists():
        raise ValueError("output/log already exists; choose a new capture path")
    sources, fingerprint = source_state()
    revision = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain"]))
    # An experiment batch may share a fresh isolated build root. Model paths
    # encode all RTL knobs; firmware paths encode workload knobs. Compiler
    # settings stay fixed within that isolated batch.
    context = nullcontext(str(build_directory)) if build_directory else tempfile.TemporaryDirectory(prefix="aster-bench-")
    with context as directory:
        build = Path(directory)
        settings = [f"BUILD_DIR={build}", f"ENABLE_L1={args.l1}", f"SYNC_MEMORY={args.sync_memory}",
                    f"RISCV_PREFIX={args.riscv_prefix}", f"MEMORY_WAIT_CYCLES={args.memory_wait}",
                    f"L1_LINE_WORDS={args.line_words}", f"L1_LINE_COUNT={args.line_count}",
                    f"BENCH_WORKLOAD={args.workload}", f"BENCH_WORDS={args.words}",
                    f"BENCH_REPETITIONS={args.repetitions}", f"BENCH_SEED=0x{args.seed:08x}"]
        config = json.loads(command(["make", "--no-print-directory", "-s", *settings, "bench-config"]))
        invocation = ["make", "--output-sync=target", "-j2", *settings, "bench"]
        process = subprocess.run(invocation, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".log").write_text(process.stdout)
        if process.returncode:
            raise ValueError(f"build/benchmark failed; inspect {args.output.with_suffix('.log')}")
        records = [line for line in process.stdout.splitlines(keepends=True) if line.startswith("ASTERBENCH")]
        if len(records) != 1:
            raise ValueError("expected exactly one captured benchmark record")
        record = parse_record(records[0])
        requested = {"l1": args.l1, "sync_memory": args.sync_memory, "memory_wait": args.memory_wait,
                     "line_words": args.line_words, "line_count": args.line_count, "name": args.workload,
                     "bytes": args.words*4, "repetitions": args.repetitions, "seed": args.seed}
        if any(record[key] != value for key, value in requested.items()):
            raise ValueError("executing configuration disagrees with requested build")
        if record["checksum"] != expected_checksum(args.workload, args.words, args.repetitions, args.seed):
            raise ValueError("workload checksum disagrees with independent host reference")
        if source_state() != (sources, fingerprint) or command(["git", "rev-parse", "HEAD"]) != revision:
            raise ValueError("source changed during capture; repeat against a stable revision/worktree")
        compiler = shutil.which(config["compiler"])
        if compiler is None:
            raise ValueError("cannot fingerprint compiler")
        result = {"schema": 1, "serial_record": records[0], "record": record, "metadata": {
            "revision": revision, "dirty": dirty, "source_sha256": fingerprint, "source_files": sources,
            "compiler": compiler, "compiler_version": command([compiler, "--version"]).splitlines()[0],
            "compiler_sha256": sha(compiler), "cflags": config["cflags"], "ldflags": config["ldflags"],
            "verilator_version": command([config["verilator"], "--version"]),
            "firmware_sha256": sha(config["firmware"]),
            "elf_sha256": sha(config["elf"]),
            "simulator_sha256": sha(config["simulator"]),
            "build_command": invocation, "platform": platform.platform(),
        }}
        validate_result(result)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"PASS: saved verified AsterBench capture to {args.output}")
    return result


def compare(baseline, candidate):
    a, b = validate_result(baseline), validate_result(candidate)
    for key in ("version", "name", "bytes", "repetitions", "seed", "checksum"):
        if a[key] != b[key]:
            raise ValueError(f"incomparable workload: {key} differs")
    return {
        "baseline_revision": baseline["metadata"]["revision"],
        "candidate_revision": candidate["metadata"]["revision"],
        "cycle_speedup": a["cycles"] / b["cycles"],
        "nominal_time_speedup": (a["cycles"] / a["clock_hz"]) / (b["cycles"] / b["clock_hz"]),
        "counters": {key: {"baseline": a[key], "candidate": b[key], "delta": b[key]-a[key]}
                     for key in COUNTERS},
        "configurations": [{key: item[key] for key in
                             ("clock_hz", "l1", "sync_memory", "line_words", "line_count", "memory_wait")}
                            for item in (a, b)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("capture")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--l1", type=int, choices=(0, 1), default=1)
    run.add_argument("--sync-memory", type=int, choices=(0, 1), default=0)
    run.add_argument("--memory-wait", type=int, default=None)
    run.add_argument("--line-words", type=int, default=4)
    run.add_argument("--line-count", type=int, default=16)
    run.add_argument("--workload", choices=("memcpy", "walk_sequential", "walk_random"), default="memcpy")
    run.add_argument("--words", type=int, default=64)
    run.add_argument("--repetitions", type=int, default=4)
    run.add_argument("--seed", type=lambda value: int(value, 0), default=0x13570000)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    diff = commands.add_parser("compare")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "capture":
            capture(args)
        else:
            print(json.dumps(compare(json.loads(args.baseline.read_text()),
                                     json.loads(args.candidate.read_text())), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
