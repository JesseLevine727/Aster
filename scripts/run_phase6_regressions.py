#!/usr/bin/env python3
"""Run the complete Phase 1-6 regression plan from clean, stable source.

Every recursive matrix runs serially relative to the others, in one fresh
isolated build tree. This avoids mixed-configuration rebuild races. Logs and
partial failure state are retained; this script never cleans an existing tree.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

from bench_results import ROOT, command, sha, source_state
from coherent_results import fingerprint_tools, source_at_revision

TARGETS = (
    "check", "phase1-matrix", "cache-matrix", "cache-boundaries", "phase4-soc-matrix",
    "fabric-matrix", "multicore-runtime-matrix", "multicore-adversarial-matrix",
    "parallel-matrix", "parallel-workloads", "atomic-runtime-matrix",
    "coherent-runtime-matrix", "atomic-faults-matrix", "coherent-cache-matrix",
    "coherent-soc-matrix", "linux-coherent-matrix", "coherent-bench-matrix",
    "coherent-bench-boundaries", "coherent-bench-sizes", "riscv-reference-matrix",
    "coherent-litmus-matrix", "coherent-litmus-boundaries",
)


def identity(executable):
    path = Path(shutil.which(executable) or executable).resolve()
    return {"path": str(path), "sha256": sha(path), "version": command([str(path), "--version"]).splitlines()[0]}


def run(output, *, check_only=False):
    output = output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory; no existing build/evidence is overwritten")
    if command(["git", "status", "--porcelain"]):
        raise ValueError("complete regression evidence requires clean committed source")
    sources, fingerprint = source_state()
    revision = command(["git", "rev-parse", "HEAD"])
    source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    config = json.loads(command(["make", "--no-print-directory", "-s", "coherent-config"]))
    toolchain = fingerprint_tools(config)
    output.mkdir(parents=True)
    build = output/"build"
    targets = ("check",) if check_only else TARGETS
    manifest = dict(schema="aster.regressions.phase6.v1", status="running", revision=revision, dirty=False,
                    source_files=sources, source_sha256=fingerprint, toolchain=toolchain,
                    python=identity(sys.executable), host_compiler=identity("g++"), platform=platform.platform(),
                    targets=list(targets), build_directory=str(build), results=[],
                    started_utc=datetime.now(timezone.utc).isoformat())

    def save():
        # Generated evidence, deliberately retained even when a child fails.
        (output/"manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")

    save()
    try:
        for index, target in enumerate(targets, 1):
            if source_state() != (sources, fingerprint) or command(["git", "rev-parse", "HEAD"]) != revision or command(["git", "status", "--porcelain"]):
                raise ValueError("source changed during regression sequence")
            invocation = ["make", "--no-print-directory", "-j2", f"BUILD_DIR={build}", target]
            name = f"{index:02d}-{target}.log"
            print(f"RUN {index}/{len(targets)} {target}: {output/name}", flush=True)
            started = time.monotonic()
            with (output/name).open("xb") as log:
                result = subprocess.run(invocation, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            elapsed = time.monotonic()-started
            raw = (output/name).read_bytes()
            entry = dict(target=target, command=invocation, exit_code=result.returncode,
                         elapsed_seconds=elapsed, log=name, log_sha256=hashlib.sha256(raw).hexdigest(), log_bytes=len(raw))
            manifest["results"].append(entry); save()
            if result.returncode:
                raise ValueError(f"{target} failed; inspect the retained log")
            if not any(line.startswith(b"PASS") or b"PASS:" in line for line in raw.splitlines()):
                raise ValueError(f"{target} did not emit any passing test evidence")
            print(f"PASS {target} ({elapsed:.1f}s)", flush=True)
        if source_state() != (sources, fingerprint) or command(["git", "rev-parse", "HEAD"]) != revision or command(["git", "status", "--porcelain"]):
            raise ValueError("source changed before regression closeout")
        if fingerprint_tools(config) != toolchain or identity(sys.executable) != manifest["python"] or identity("g++") != manifest["host_compiler"]:
            raise ValueError("toolchain changed during regression sequence")
        manifest["status"] = "complete"
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat(); save()
        label = "fresh-checkout make check" if check_only else "complete Phase 1-6 regression plan"
        print(f"PASS: clean-source {label} ({len(targets)} targets), {output/'manifest.json'}", flush=True)
    except BaseException as error:
        manifest["status"] = "failed"; manifest["error"] = str(error)
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat(); save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="fresh make check only; NOT the complete regression plan")
    args = parser.parse_args()
    try:
        run(args.output, check_only=args.check_only)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
