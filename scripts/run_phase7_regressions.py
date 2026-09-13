#!/usr/bin/env python3
"""Run the complete DMA regression supplement from clean, stable source.

This does not replace the 22-target Phase 1–6 run. Closeout requires both
manifests, audited scenarios and compatible hardware sources. Matrices run
sequentially in a fresh build tree, preserving logs and failure state.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

from bench_results import ROOT, command, source_state
from dma_results import fingerprint_tools, source_at_revision
from run_phase6_regressions import identity

TARGETS = ("host-tests", "dma-engine", "dma-arbiter", "dma-counters", "dma-warm-stop",
           "dma-cache-matrix", "dma-cache-boundaries", "dma-atomic-fabric", "dma-runtime-matrix",
           "dma-bench-cases", "dma-bench-sensitivity", "linux-dma-matrix",
           "linux-dma-bench-cases", "linux-dma-bench-baud")
SCHEMA = "aster.regressions.phase7.v1"


def run(output):
    if output.is_symlink(): raise ValueError("symlink regression output")
    output = output.resolve()
    if output.exists(): raise ValueError("regressions need a new directory; old evidence is preserved")
    if command(["git","status","--porcelain"]): raise ValueError("complete regression evidence requires clean source")
    sources,fingerprint = source_state(); revision = command(["git","rev-parse","HEAD"])
    source_at_revision(dict(dirty=False,revision=revision,source_files=sources))
    config = json.loads(command(["make","--no-print-directory","-s","dma-config"]))
    toolchain = fingerprint_tools(config); output.mkdir(parents=True); build = output/"build"
    m = dict(schema=SCHEMA,status="running",revision=revision,dirty=False,source_files=sources,source_sha256=fingerprint,
        toolchain=toolchain,python=identity(sys.executable),host_compiler=identity("g++"),platform=platform.platform(),
        targets=list(TARGETS),build_directory=str(build),results=[],started_utc=datetime.now(timezone.utc).isoformat())
    def save(): (output/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n")
    def stable():
        if source_state() != (sources,fingerprint) or command(["git","rev-parse","HEAD"]) != revision or command(["git","status","--porcelain"]):
            raise ValueError("source changed during DMA regression run")
    save()
    try:
        for index,target in enumerate(TARGETS,1):
            stable(); invocation = ["make","--no-print-directory","-j2",f"BUILD_DIR={build}",target]
            name = f"{index:02d}-{target}.log"; print(f"RUN DMA {index}/{len(TARGETS)} {target}: {output/name}",flush=True)
            started = time.monotonic()
            with (output/name).open("xb") as log:
                result = subprocess.run(invocation,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            elapsed = time.monotonic()-started; raw = (output/name).read_bytes()
            m["results"].append(dict(target=target,command=invocation,exit_code=result.returncode,elapsed_seconds=elapsed,
                log=name,log_sha256=hashlib.sha256(raw).hexdigest(),log_bytes=len(raw))); save()
            if result.returncode: raise ValueError(target+" failed; inspect retained log")
            if re.search(rb"(?m)(^FAIL:|^ERROR:|^FAILED\b|^Traceback|%Error|make[^\n]*\*\*\*)",raw):
                raise ValueError("failed DMA regression output")
            if target == "host-tests":
                if not re.search(rb"(?m)^Ran [1-9]\d* tests in [\d.]+s$",raw) or not re.search(rb"(?m)^OK$",raw):
                    raise ValueError("missing host-suite completion")
            elif not re.search(rb"(?m)^PASS:",raw): raise ValueError("missing DMA test observations")
            print(f"PASS DMA {target} ({elapsed:.1f}s)",flush=True)
        stable()
        if fingerprint_tools(config) != toolchain or identity(sys.executable) != m["python"] or identity("g++") != m["host_compiler"]:
            raise ValueError("toolchain changed during DMA regressions")
        m.update(status="complete",finished_utc=datetime.now(timezone.utc).isoformat()); save()
        print(f"PASS: complete clean-source DMA regression supplement, {len(TARGETS)} targets, {output/'manifest.json'}",flush=True)
    except BaseException as error:
        m.update(status="failed",error=str(error),finished_utc=datetime.now(timezone.utc).isoformat()); save(); raise
    return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    try: run(args.output)
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
