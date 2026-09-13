#!/usr/bin/env python3
"""Complete fixed Phase 8 supplement; legacy 22 and DMA 14 remain separate gates."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from bench_results import ROOT, command, source_state
from dot8_results import fingerprint_tools, source_at_revision
from run_phase6_regressions import identity

SCHEMA = "aster.regressions.phase8.v1"
PARALLELISM = 8
TIMINGS = ((0,0),(0,7),(1,1),(1,7))


def plan():
    entries = []
    def add(identifier,target,**settings):
        entries.append(dict(id=identifier,target=target,settings={k:str(v) for k,v in settings.items()}))
    add("host","host-tests"); add("arithmetic","dot8-unit")
    for enabled,cache in product((0,1),(0,1)):
        add(f"probe-e{enabled}-c{cache}","dot8-probe",DOT8_PROBE_ENABLE=enabled,DOT8_PROBE_CACHE=cache)
    for harts in (1,2): add(f"counters-h{harts}","dot8-counters",HART_COUNT=harts)
    for harts,cache,(sync,wait) in product((1,2),(0,1),TIMINGS):
        add(f"runtime-h{harts}-c{cache}-s{sync}-w{wait}","dot8-runtime",HART_COUNT=harts,ENABLE_L1=cache,
            SYNC_MEMORY=sync,MEMORY_WAIT_CYCLES=wait,L1_LINE_WORDS=4,L1_LINE_COUNT=16)
    for harts,(words,lines) in product((1,2),((2,2),(8,4))):
        add(f"runtime-geometry-h{harts}-w{words}-n{lines}","dot8-runtime",HART_COUNT=harts,ENABLE_L1=1,
            SYNC_MEMORY=1,MEMORY_WAIT_CYCLES=3,L1_LINE_WORDS=words,L1_LINE_COUNT=lines)
    for enabled,harts,cache in product((0,1),(1,2),(0,1)):
        add(f"linux-e{enabled}-h{harts}-c{cache}","linux-dot8-sim",HART_COUNT=harts,ENABLE_L1=cache,
            DOT8_LINUX_ENABLE=enabled,DOT8_LINUX_BAUD=781250)
    for cache in (0,1):
        add(f"linux-baud-c{cache}","linux-dot8-sim",HART_COUNT=2,ENABLE_L1=cache,DOT8_LINUX_ENABLE=1,DOT8_LINUX_BAUD=115200)
    for name in ("dot","fir","gemm"):
        for k,alignment in product((0,7,4096 if name == "dot" else 64),("aligned","unaligned")):
            add(f"sensitivity-{name}-k{k}-a{alignment}","dot8-bench",HART_COUNT=1,ENABLE_L1=1,SYNC_MEMORY=0,MEMORY_WAIT_CYCLES=7,
                L1_LINE_WORDS=2,L1_LINE_COUNT=2,DOT8_WORKLOAD=name,DOT8_K=k,DOT8_ALIGNMENT=alignment,DOT8_JOBS=3,
                DOT8_SEED=0xc0ffee,DOT8_BOOTS=2,DOT8_UART_SEED=0xa57e8)
    return entries


def invocation(entry,build):
    # Each independent case owns its model AND firmware tree. Concurrent make
    # must never race different definitions into one shared firmware path.
    return ["make","--no-print-directory","-j2","BUILD_DIR="+str(Path(build)/entry["id"])]+[k+"="+v for k,v in entry["settings"].items()]+[entry["target"]]


def run(output):
    from audit_phase8_regressions import validate_log
    if output.is_symlink() or output.exists(): raise ValueError("new regression output required; old evidence is preserved")
    output = output.resolve()
    if command(["git","status","--porcelain"]): raise ValueError("complete regression evidence requires clean source")
    sources,fingerprint = source_state(); revision = command(["git","rev-parse","HEAD"])
    source_at_revision(dict(dirty=False,revision=revision,source_files=sources))
    config = json.loads(command(["make","--no-print-directory","-s","dot8-config"]))
    toolchain = fingerprint_tools(config); output.mkdir(parents=True); build = output/"build"
    m = dict(schema=SCHEMA,status="running",revision=revision,dirty=False,source_files=sources,source_sha256=fingerprint,
        toolchain=toolchain,python=identity(sys.executable),host_compiler=identity("g++"),platform=platform.platform(),
        plan=plan(),parallelism=PARALLELISM,build_directory=str(build),results=[None]*len(plan()),started_utc=datetime.now(timezone.utc).isoformat())
    def save(): (output/"manifest.json").write_text(json.dumps(m,indent=2,sort_keys=True)+"\n")
    def stable():
        if source_state() != (sources,fingerprint) or command(["git","rev-parse","HEAD"]) != revision or command(["git","status","--porcelain"]):
            raise ValueError("source changed during dot8 regressions")
    save()
    def execute(index,entry):
        stable(); args = invocation(entry,build); name = f"{index:02d}-{entry['id']}.log"
        print(f"RUN DOT8 {index}/{len(plan())} {entry['id']}: {output/name}",flush=True); started = time.monotonic()
        with (output/name).open("xb") as log: result = subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        elapsed = time.monotonic()-started; raw = (output/name).read_bytes()
        record = dict(id=entry["id"],command=args,exit_code=result.returncode,elapsed_seconds=elapsed,
            log=name,log_sha256=hashlib.sha256(raw).hexdigest(),log_bytes=len(raw))
        failure = None
        try:
            if result.returncode: raise ValueError(entry["id"]+" failed; inspect retained log")
            validate_log(entry,raw.decode("utf-8")); stable()
        except BaseException as error: failure = error
        return index,record,failure
    try:
        failures = []
        with ThreadPoolExecutor(max_workers=PARALLELISM) as pool:
            pending = [pool.submit(execute,index,entry) for index,entry in enumerate(plan(),1)]
            for future in as_completed(pending):
                if future.cancelled(): continue
                index,record,failure = future.result(); m["results"][index-1] = record
                if failure:
                    failures.append(str(failure)); m.update(status="failed",error="; ".join(failures))
                    for queued in pending: queued.cancel()
                    print(f"FAIL DOT8 {record['id']}: {failure}; retaining all started cases",flush=True)
                else: print(f"PASS DOT8 {record['id']} ({record['elapsed_seconds']:.1f}s)",flush=True)
                save()
        if failures: raise ValueError("; ".join(failures))
        stable()
        if fingerprint_tools(config) != toolchain or identity(sys.executable) != m["python"] or identity("g++") != m["host_compiler"]:
            raise ValueError("toolchain changed during dot8 regressions")
        m.update(status="complete",finished_utc=datetime.now(timezone.utc).isoformat()); save()
        print(f"PASS: complete clean-source dot8 supplement, {len(plan())} entries, {output/'manifest.json'}",flush=True)
    except BaseException as error:
        m.update(status="failed",error=str(error),finished_utc=datetime.now(timezone.utc).isoformat()); save(); raise
    return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    try: run(args.output)
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
