#!/usr/bin/env python3
"""Read-only fixed Phase 8 scenario/sequence audit; does not replace legacy/DMA gates."""
import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import asterbench_dot8 as bench
from audit_phase6_regressions import Log
from dot8_results import digest, source_at_revision, validate_toolchain
from run_phase8_regressions import SCHEMA, PARALLELISM, plan, invocation

require = bench.require
RUNTIME_SUFFIX = "; all 16 byte alignments, tails, dot/FIR/GEMM, actual output stores, full guards/RAM, LRSC, dirty DMA publication, ABI 6"
STOP_SUFFIX = "; all admitted sums completed, no reset race, full retained RAM"
PROBE_SUFFIX = "; complete RAM and RVFI writeback, aliases/x0, M/A, traps, admission/reset"


def validate_log(entry,raw):
    require(entry in plan(),"unknown/modified Phase 8 regression case")
    log = Log(raw); target = entry["target"]; s = entry["settings"]; host_count = 0
    value = lambda key: int(s[key],0)
    if target == "host-tests":
        reported = re.findall(r"(?m)^Ran ([1-9]\d*) tests in [\d.]+s$",raw)
        tests = re.findall(r"(?m)^(test_\w+ \([^\n()]+\)) \.\.\. ",raw)
        require(len(reported) == 1 and int(reported[0]) >= 176 and len(tests) == len(set(tests)) == int(reported[0]) and
                re.findall(r"(?m)^OK$",raw) == ["OK"] and not re.search(r"\bskipped\b|\bexpected failure\b",raw), "incomplete host suite")
        host_count = int(reported[0]); log.finish(0)
    elif target == "dot8-unit":
        rows = log.rows("dot8 PCPI",r"dot8 PCPI seed=(\d+) decode=131072 lane_products=262144 register_fields=32768 packed=6144 jobs=301061 reset_stages=4; exact signed sum, admission wait, captured operands, held reply, exactly-once events")
        require(rows == [(1,),(0xa57e8,),(0xc0ffee,)],"missing/reordered exhaustive arithmetic seeds")
    elif target == "dot8-probe":
        rows = log.rows("dot8 real hart",r"dot8 real hart enabled=(\d+) icache=(\d+) seed=0xa57e8 programs=(\d+) dot8=(\d+) atomic=(\d+) retired=(\d+) resets=(\d+) prefetch_overlap=(\d+) cycles=(\d+) accept_to_retire_edges=(\d+)\.\.(\d+)"+re.escape(PROBE_SUFFIX))
        require(len(rows) == 1,"missing/duplicate real-core probe")
        enabled,cache,programs,dots,atomics,retired,resets,overlap,cycles,low,high = rows[0]
        require((enabled,cache) == (value("DOT8_PROBE_ENABLE"),value("DOT8_PROBE_CACHE")),"wrong optional core configuration")
        require((programs,dots,atomics,retired,resets) == ((33827,426413,98403,4163737,32) if enabled else (33794,0,0,2095228,0)),"incomplete real instruction/alias/trap/A/reset coverage")
        require(cycles > retired and (overlap > 0 if enabled else overlap == 0) and
                (low,high) == ((4,303 if cache else 81) if enabled else (0,0)),"missing prefetch/retirement-latency observations")
    elif target == "dot8-counters":
        log.exact(f"dot8 ABI 6 harts={value('HART_COUNT')} eight counters, 4096 command/event cases, 20000 seeded steps, common-window priority/exclusion, full 32/64-bit wrap, absent-hart zeros, all offsets")
    elif target == "dot8-runtime":
        harts,cache,wait = value("HART_COUNT"),value("ENABLE_L1"),value("MEMORY_WAIT_CYCLES")
        rows = log.rows("dot8 C runtime",r"dot8 C runtime harts=(\d+) cache=(\d+) wait=(\d+) cycles=(\d+) pairs=(\d+) output_methods=(\d+) dots=(\d+),(\d+) DMA-overlap=(\d+)"+re.escape(RUNTIME_SUFFIX))
        require(len(rows) == 2,"two full warm runtime boots required")
        for h,c,w,cycles,pairs,methods,d0,d1,overlap in rows:
            require((h,c,w) == (harts,cache,wait) and cycles > 1000000 and pairs == 736*harts and methods == 1472*harts+3 and
                    (d0,d1) == ((25393,25689) if harts == 2 else (25753,0)) and overlap > 0,"runtime matrix/actual DMA-compute overlap missing")
        rows = log.rows("dot8 warm stop",r"dot8 warm stop hart=(\d+) point=(\d+) escalation=(\d+) selective=(\d+)"+re.escape(STOP_SUFFIX))
        expected = [(h,p,0) for h in range(harts) for p in range(4)]
        if harts == 2: expected += [(0,4,0)]+[(0,p,1) for p in range(4)]
        require([r[:3] for r in rows] == expected,"missing/reordered active compute/response/retirement stop/escalation boundaries")
        for h,p,e,count in rows:
            if harts == 1 or h == 1: require(count == 0,"unexpected selective reset")
            elif p == 4: require(count >= 8,"missing eight repeated selective resets")
            elif e: require(count == (0 if p == 0 else 1),"wrong selective/global escalation history")
            else: require(count <= 1,"unexpected repeated selective reset")
        rows = log.rows("dot8 runtime closeout",r"dot8 runtime closeout warm_boots=2 stopped=(\d+) CPU-stores=(\d+) DMA-stores=(\d+); no reset after initial POR")
        require(len(rows) == 1 and rows[0][0] == (15 if harts == 2 else 6) and rows[0][1] > harts*1000000 and rows[0][2] >= 1024,
                "incomplete acknowledged-stop/full-RAM closeout")
        require(all(line.startswith("PASS: dot8 C runtime ") for line in log.lines[:2]) and
                all(line.startswith("PASS: dot8 warm stop ") for line in log.lines[2:-1]) and
                log.lines[-1].startswith("PASS: dot8 runtime closeout "),"reordered runtime evidence")
    elif target == "linux-dot8-sim":
        enabled,harts,cache,baud = (value(k) for k in ("DOT8_LINUX_ENABLE","HART_COUNT","ENABLE_L1","DOT8_LINUX_BAUD"))
        rows = log.rows("dot8 AXI/serial runtime",r"dot8 AXI/serial runtime boot=(\d+) harts=(\d+) cache=(\d+) counters=50 retained_RAM=65536; real C, ROM-write/running-RAM denial, all read-only strobes/offsets, stable replies")
        require(rows == ([(b,harts,cache) for b in (1,2)] if enabled else []),"wrong Linux functional boots")
        log.exact(f"dot8 Linux bridge enabled={enabled} harts={harts} cache={cache} all AXI byte offsets/strobes, exact live banks, custom/atomic/fetch denial, final STOPPED")
        require(log.lines[-1].startswith("PASS: dot8 Linux bridge "),"early Linux closeout")
        require(re.findall(r"-GBAUD=(\d+)\b",raw) == [str(baud)],"missing/wrong actual UART baud model build")
    elif target == "dot8-bench":
        start = raw.find("ASTERBOOT 1\n"); require(start >= 0,"missing benchmark framing")
        require(not re.search(r"(?m)^(ASTER|DOT8_OBS)",raw[:start]),"unrecognized benchmark preamble")
        serial,_observations,_stops = bench.simulation_log(raw[start:],2,3)
        expected = dict(name=s["DOT8_WORKLOAD"],k=value("DOT8_K"),alignment=s["DOT8_ALIGNMENT"],harts=1,jobs=3,base_seed=0xc0ffee,
                        l1=1,sync_memory=0,memory_wait=7,line_words=2,line_count=2)
        for stream in serial:
            require(all(all(bench.typed_equal(r[k],v) for k,v in expected.items()) for r in bench.parse_stream(stream)),"wrong sensitivity shape/timing/seed/geometry")
        log.exact("dot8 benchmark boots=2 records=12; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows")
    else: raise ValueError("unimplemented regression gate")
    log.finish(len(log.lines))
    return dict(id=entry["id"],passing_scenarios=len(log.lines),host_tests=host_count)


def audit(path,*,clean=True):
    require(not path.is_symlink(),"symlink regression manifest"); m = bench.json_record(path.read_text())
    fields = {"schema","status","revision","dirty","source_files","source_sha256","toolchain","python","host_compiler",
              "platform","plan","parallelism","build_directory","results","started_utc","finished_utc"}
    require(type(m) is dict and set(m) == fields and m["schema"] == SCHEMA and m["status"] == "complete" and m["dirty"] is False,
            "incomplete dot8 regression manifest")
    require(type(m["revision"]) is str and re.fullmatch(r"[0-9a-f]{40}",m["revision"]),"invalid source revision")
    require(type(m["source_files"]) is dict and m["source_files"],"missing source inventory")
    for name,value in m["source_files"].items():
        require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts,"unsafe source path"); digest(value)
    digest(m["source_sha256"])
    require(hashlib.sha256(json.dumps(m["source_files"],sort_keys=True).encode()).hexdigest() == m["source_sha256"],"source hash differs")
    if clean: source_at_revision(m)
    validate_toolchain(m["toolchain"])
    for key in ("python","host_compiler"):
        item = m[key]
        require(type(item) is dict and set(item) == {"path","sha256","version"} and type(item["path"]) is str and Path(item["path"]).is_absolute() and
                type(item["version"]) is str and item["version"],"incomplete host tool identity"); digest(item["sha256"])
    require(type(m["platform"]) is str and m["platform"] and type(m["build_directory"]) is str and Path(m["build_directory"]).is_absolute(),"invalid build identity")
    dates = []
    for key in ("started_utc","finished_utc"):
        require(type(m[key]) is str,"invalid regression date"); date = datetime.fromisoformat(m[key])
        require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0,"non-UTC regression date"); dates.append(date)
    specs = plan()
    require(type(m["parallelism"]) is int and m["parallelism"] == PARALLELISM,"wrong isolated-case concurrency")
    require(dates[1] > dates[0] and bench.typed_equal(m["plan"],specs) and type(m["results"]) is list and len(m["results"]) == len(specs),"incomplete/reordered fixed plan")
    inventory = {path.name}; summary = []; elapsed = 0; longest = 0
    for index,(spec,entry) in enumerate(zip(specs,m["results"]),1):
        name = f"{index:02d}-{spec['id']}.log"; inventory.add(name)
        require(type(entry) is dict and set(entry) == {"id","command","exit_code","elapsed_seconds","log","log_bytes","log_sha256"} and
                entry["id"] == spec["id"] and entry["log"] == name and type(entry["exit_code"]) is int and entry["exit_code"] == 0,"failed/reordered case")
        require(bench.typed_equal(entry["command"],invocation(spec,m["build_directory"])),"recorded configuration/command changed")
        seconds = entry["elapsed_seconds"]
        require(type(seconds) in (int,float) and math.isfinite(seconds) and seconds > 0,"invalid elapsed time"); elapsed += seconds; longest = max(longest,seconds)
        digest(entry["log_sha256"]); bench.integer(entry["log_bytes"],1)
        file = path.parent/name; require(file.is_file() and not file.is_symlink(),"missing/symlink log"); raw = file.read_bytes()
        require(len(raw) == entry["log_bytes"] and hashlib.sha256(raw).hexdigest() == entry["log_sha256"],"raw log changed")
        try: summary.append(validate_log(spec,raw.decode("utf-8")))
        except ValueError as error: raise ValueError(f"{spec['id']}: {error}") from error
    duration = (dates[1]-dates[0]).total_seconds()
    require(longest <= duration+1 and elapsed <= PARALLELISM*duration+1,"child duration exceeds actual concurrency/total")
    require({p.name for p in path.parent.iterdir()} in (inventory,inventory|{"build"}),"unlisted regression artifact")
    if (path.parent/"build").exists(): require((path.parent/"build").is_dir() and not (path.parent/"build").is_symlink(),"unsafe build tree")
    return dict(revision=m["revision"],cases=summary,passing_scenarios=sum(s["passing_scenarios"] for s in summary),host_tests=summary[0]["host_tests"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("manifest",type=Path); args = parser.parse_args()
    try:
        print(json.dumps(audit(args.manifest),indent=2,sort_keys=True))
        print("PASS: all fixed dot8 regression scenarios, raw hashes and complete Git provenance")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
