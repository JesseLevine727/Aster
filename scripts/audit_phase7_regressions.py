#!/usr/bin/env python3
"""Read-only scenario/provenance audit of the 14-target DMA supplement.

The unchanged 22-target legacy audit remains separately required. Configuration
selectors absent from older text output are bound to the exact Git Makefile and
fixed target invocation; they are not invented independent observations.
"""
import argparse
from datetime import datetime
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import re
import subprocess

import asterbench_dma as bench
from audit_phase6_regressions import Log, same
from dma_results import digest, source_at_revision, validate_toolchain
from run_phase7_regressions import SCHEMA, TARGETS

require = bench.require
SEEDS = (1,0xa57e7,0xc0ffee)
TIMINGS = ((0,0),(0,7),(1,1),(1,7))
ENGINE_SUFFIX = ("; full RAM/guards, 16 alignments, 23 sizes, permissions/wrap/overlap, register lanes, exactly-once events, "
                 "held replies, pause/drain, abort priority, restart/POR")
ARBITER_SUFFIX = ("; indivisible AMO/response/gap, fault-only groups, round-robin bound, ownership, held replies, "
                  "admission pause and exact serialized memory history")
CACHE_SUFFIX = ("; latest-byte oracle, M/S/I snoops, whole-line dirty neighbor preservation, no device allocation, exact backing "
                "history/events, RAM-code visibility, selective/global flush")
RUNTIME_SUFFIX = "; full RAM, same-value LR/SC, peer publication, driver errors/abort, exact counters"
AXI_CLOSE = "coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; snapshots="
DIRECT_CLOSE = "DMA benchmark boots=2 records={records}; exact CPU/DMA payload ownership, full output/guards/RAM, actual kernel and 42-counter windows"
LINUX_CLOSE = "DMA Linux paired windows, queued freeze-time observations, actual kernel/payload ownership, full output/guards/RAM; CPU stores="


def benchmark_plan(target):
    configs = []
    def add(size,alignment,caches,harts=2,jobs=4,sync=1,wait=1,words=4,lines=16,seed=0x13570000):
        configs.append(dict(size=size,alignment=alignment,l1=caches,harts=harts,jobs=jobs,sync_memory=sync,memory_wait=wait,
                            line_words=words,line_count=lines,base_seed=seed,clock_hz=31250000))
    if target == "dma-bench-cases":
        for c,n,a in product((0,1),(0,1,3,4,63,64,8192),bench.ALIGNMENTS): add(n,a,c)
    elif target == "dma-bench-sensitivity":
        for n,a in product((0,127,8192),bench.ALIGNMENTS): add(n,a,1,1,3,0,7,2,2,0xc0ffee)
    elif target == "linux-dma-bench-cases":
        for c in (0,1):
            for n,a in ((0,"aligned"),(1,"different_offset"),(64,"aligned"),(127,"same_offset"),(8192,"different_offset")): add(n,a,c)
    elif target == "linux-dma-bench-baud":
        for c in (0,1): add(127,"same_offset",c)
    else: raise ValueError("unknown DMA benchmark target")
    return configs


def check_config(rows,config):
    require(all(all(bench.typed_equal(row[k],v) for k,v in config.items()) for row in rows), "wrong benchmark case/configuration/seed")


def direct_benchmarks(log,target):
    plan = benchmark_plan(target); jobs = plan[0]["jobs"]
    marker = DIRECT_CLOSE.format(records=jobs*4)
    log.exact(marker,len(plan)); chunks = []; chunk = []
    for line in log.raw.splitlines(keepends=True):
        chunk.append(line)
        if line == "PASS: "+marker+"\n": chunks.append("".join(chunk)); chunk = []
    require(not chunk and len(chunks) == len(plan), "missing/extra direct DMA captures")
    for raw,config in zip(chunks,plan):
        observed = bench.simulation_log(raw,2,jobs)
        for serial in observed["serial_boots"]: check_config(bench.parse_stream(serial.encode()),config)


def linux_benchmarks(log,target):
    plan = benchmark_plan(target); data = []; case = boot = 0; waiting_close = False
    for line in log.raw.splitlines(keepends=True):
        if line.startswith("ASTERBENCH"):
            require(not waiting_close and case < len(plan), "extra/reordered Linux DMA record")
            data.append(line)
        elif line.startswith("PASS: DMA AXI/serial benchmark"):
            require(case < len(plan) and not waiting_close, "extra Linux DMA boot")
            config = plan[case]; rows = bench.parse_stream("".join(data).encode()); check_config(rows,config)
            require(len(rows) == 8 and line == (f"PASS: DMA AXI/serial benchmark boot={boot+1} size={config['size']} "
                f"alignment={list(bench.ALIGNMENTS).index(config['alignment'])} cache={config['l1']} records=8 serial_bytes={sum(map(len,data))} retained_RAM=65536\n"),
                "wrong Linux DMA boot/serial/retained-RAM observation")
            data = []; boot += 1; waiting_close = boot == 2
        elif line.startswith("PASS: "+LINUX_CLOSE):
            require(waiting_close and not data and re.fullmatch("PASS: "+re.escape(LINUX_CLOSE)+r"([1-9]\d*) DMA stores=(\d+)\n",line),
                    "missing/early Linux DMA closeout")
            match = re.fullmatch("PASS: "+re.escape(LINUX_CLOSE)+r"([1-9]\d*) DMA stores=(\d+)\n",line)
            require(int(match[1]) > 2*8*108 and int(match[2]) == 8*bench.transactions(plan[case]["size"],plan[case]["alignment"]),
                    "wrong Linux DMA payload/store totals")
            case += 1; boot = 0; waiting_close = False
        elif line.startswith(("ASTER","DMA_OBS","ASTERSTOP","ASTERBOOT")) or data or waiting_close:
            raise ValueError("unexpected Linux DMA serial/observation sequence")
    require(case == len(plan) and not data and boot == 0, "missing Linux DMA benchmark cases")
    log.rows("DMA AXI/serial benchmark",r"DMA AXI/serial benchmark boot=(\d+) size=(\d+) alignment=(\d+) cache=(\d+) records=8 serial_bytes=(\d+) retained_RAM=65536")
    log.rows(LINUX_CLOSE,re.escape(LINUX_CLOSE)+r"([1-9]\d*) DMA stores=(\d+)")
    baud = 115200 if target.endswith("-baud") else 781250
    require(re.findall(r"-GBAUD=(\d+)\b",log.raw) == [str(baud)]*2, "missing/wrong physical-baud Linux model builds")


def runtime(log):
    configs = [(h,c,w) for h,c,(_s,w) in product((1,2),(0,1),TIMINGS)]
    expected_order = []
    for h,_c,_w in configs:
        expected_order += ["PASS: real DMA runtime "]*2+["PASS: real DMA warm stop "]*5
        if h == 2: expected_order += ["PASS: real DMA selective/global "]*4
        expected_order += ["PASS: real DMA runtime ","PASS: DMA SoC closeout "]
    require(len(log.lines) == len(expected_order) and all(line.startswith(prefix) for line,prefix in zip(log.lines,expected_order)),
            "reordered DMA runtime/reset/escalation/closeout sequence")
    rows = log.rows("real DMA runtime",r"real DMA runtime harts=(\d+) cache=(\d+) wait=(\d+) cycles=(\d+) bytes=(\d+) jobs=3 directed=320 selective=(\d+) retired=(\d+),(\d+)"+re.escape(RUNTIME_SUFFIX))
    same([r[:3] for r in rows],(config for config in configs for _ in range(3)),"DMA runtime topology/timing/three warm boots")
    for h,_c,_w,cycles,count,selective,r0,r1 in rows:
        baseline = 61244 if h == 2 else 53052
        require(baseline <= count < baseline+1024 and selective == (8 if h == 2 else 0) and cycles >= r0 > 1000000 and
                (cycles >= r1 > 1000 if h == 2 else r1 == 0), "missing actual two-hart/runtime/directed/abort coverage")
    points = log.rows("real DMA warm stop",r"real DMA warm stop point=(\d+) full acknowledged RAM retained; no destructive reset")
    same(points,((p,) for _ in configs for p in range(5)),"admitted-transfer stop points")
    points = log.rows("real DMA selective/global",r"real DMA selective/global escalation point=(\d+) transient STOP latched, separate flushes, complete retained RAM")
    same(points,((p,) for h,_c,_w in configs if h == 2 for p in range(4)),"selective/global transient-STOP escalations")
    rows = log.rows("DMA SoC closeout",r"DMA SoC closeout stops=(\d+) CPU stores=(\d+) DMA stores=(\d+) reads=(\d+) writes=(\d+) reservation-clears=18")
    require(len(rows) == 16, "missing DMA runtime closeouts")
    for row,(h,_c,_w) in zip(rows,configs):
        stops,cpu,device,reads,writes = row
        require(stops == (12 if h == 2 else 8) and cpu > 1000000 and device == writes > 100000 and reads >= writes,
                "DMA runtime did not close all resets/exact stores/reservation cases")


def linux_runtime(log):
    expected = []
    for h,c in product((1,2),(0,1)):
        r1 = r"[1-9]\d*" if h == 2 else "0"
        for kind,size in (("dma",97),("publication",14)):
            expected += [rf"coherent AXI/serial {kind} harts={h} cache={c} boot={b} bytes={size} retired=[1-9]\d*,{r1} retained_RAM=65536" for b in (0,1)]
            if kind == "dma":
                expected += [rf"DMA AXI in-flight stop phase={p} retained_RAM=65536 drained_bytes=[0-4]" for p in range(3)]
                expected.append("actual-core DMA MMIO atomic and instruction-fetch denial")
            expected.append(re.escape(AXI_CLOSE)+("11" if kind == "dma" else "6")+r" observed_stores=[1-9]\d*")
    require(len(log.lines) == len(expected) and all(re.fullmatch("PASS: "+pattern,line) for pattern,line in zip(expected,log.lines)),
            "Linux DMA program/cache/hart/serial/STOP/MMIO/AXI matrix differs")
    log.used = set(range(len(log.lines)))


def validate_log(target,raw):
    require(target in TARGETS,"unknown DMA regression target"); log = Log(raw); host_count = 0
    if target == "host-tests":
        reported = re.findall(r"(?m)^Ran ([1-9]\d*) tests in [\d.]+s$",raw)
        tests = re.findall(r"(?m)^(test_\w+ \([^\n()]+\)) \.\.\. ",raw)
        require(len(reported) == 1 and int(reported[0]) >= 130 and len(tests) == len(set(tests)) == int(reported[0]) and
                re.findall(r"(?m)^OK$",raw) == ["OK"] and not re.search(r"\bskipped\b|\bexpected failure\b",raw), "incomplete host mutation/driver suite")
        host_count = int(reported[0]); log.finish(0)
    elif target == "dma-engine":
        rows = log.rows("DMA engine",r"DMA engine seed=(\d+) delay=(-?\d+) cases=7489 jobs=3098 reads=(\d+) writes=(\d+) success=(\d+) aborted=(\d+) errors=20 rejected=(\d+) stalls=(\d+) paused=(\d+)"+re.escape(ENGINE_SUFFIX))
        same([r[:2] for r in rows],product(SEEDS,(0,7,-1)),"DMA engine seed/delay cross-product")
        require(all(r[2] >= r[3] > 200000 and r[4]+r[5] == 3028 and min(r[4:]) > 0 for r in rows), "engine descriptor/event/stall/abort coverage")
    elif target == "dma-arbiter":
        rows = log.rows("DMA arbiter",r"DMA arbiter seed=(\d+) delay=(-?\d+) cpu_groups=(\d+) device_groups=(\d+) operations=(\d+) stalls=(\d+) gaps=(\d+)"+re.escape(ARBITER_SUFFIX))
        same([r[:2] for r in rows],product(SEEDS,(0,1,31,-1)),"DMA arbiter seed/delay cross-product")
        require(all(min(r[2:]) > 1000 and r[4] >= r[2]+r[3] for r in rows), "atomic-group arbitration/progress coverage")
    elif target == "dma-counters":
        log.exact("DMA ABI 5 fourteen-counter increments, exact common-window command priority/exclusion, frozen reads, metadata, all offsets and 32/64-bit carry/rollover")
    elif target == "dma-warm-stop":
        log.exact("warm-stop sequencing cases=58; drain, response settlement, held flush, selective/global stop, escalation/restart")
    elif target in ("dma-cache-matrix","dma-cache-boundaries"):
        configs = list(product((0,1),(1,4,8),(1,4,16))) if target.endswith("-matrix") else [(1,1,1024),(1,1024,1)]
        rows = log.rows("DMA cache",r"DMA cache enabled=(\d+) geometry=(\d+)x(\d+) seed=(\d+) delay=(-?\d+) cpu=(\d+) device=(\d+) forward=(\d+) invalidated=(\d+) dirty_words=(\d+) commits=(\d+) flushes=136 stalls=(\d+)"+re.escape(CACHE_SUFFIX))
        same([r[:5] for r in rows],((*c,s,d) for c in configs for s,d in product(SEEDS,(0,1,17,-1))),"device cache geometry/seed/stall coverage")
        for c,words,_lines,_seed,delay,cpu,device,forward,invalidated,dirty,commits,stalls in rows:
            require(cpu > 1000 and device > 1000 and commits > 1000 and dirty % words == 0 and
                    (min(forward,invalidated,dirty) > 0 if c else forward == invalidated == dirty == 0) and
                    (stalls == 0 if delay == 0 else stalls > 0), "missing device coherence/dirty neighbor/stalled cache cases")
    elif target == "dma-atomic-fabric":
        rows = log.rows("RV32A DMA-enabled",r"RV32A DMA-enabled fabric seed=(\d+): (\d+) operations, (\d+) backing transfers, (\d+) committed stores, SC=(\d+)/(\d+) success/failure, (\d+) stalled cycles")
        same([r[0] for r in rows],SEEDS,"DMA-enabled full-A fabric seeds")
        require(all(r[1] > 60000 and r[2] > 60000 and r[3] > 30000 and min(r[4:]) > 0 for r in rows), "full-A/DMA fabric request/history/SC coverage")
    elif target == "dma-runtime-matrix": runtime(log)
    elif target == "linux-dma-matrix": linux_runtime(log)
    elif target.startswith("linux-"): linux_benchmarks(log,target)
    else: direct_benchmarks(log,target)
    log.finish(len(log.lines))
    return dict(target=target,passing_scenarios=len(log.lines),host_tests=host_count)


def audit(path,*,clean=True):
    require(not path.is_symlink(), "symlink DMA regression manifest"); m = bench.json_record(path.read_text())
    fields = {"schema","status","revision","dirty","source_files","source_sha256","toolchain","python","host_compiler",
              "platform","targets","build_directory","results","started_utc","finished_utc"}
    require(type(m) is dict and set(m) == fields and m["schema"] == SCHEMA and m["status"] == "complete" and m["dirty"] is False,
            "incomplete DMA regression manifest")
    require(type(m["revision"]) is str and re.fullmatch(r"[0-9a-f]{40}",m["revision"]), "invalid DMA regression revision")
    require(type(m["source_files"]) is dict and m["source_files"], "missing DMA regression sources")
    for name,value in m["source_files"].items():
        require(type(name) is str and not Path(name).is_absolute() and ".." not in Path(name).parts, "unsafe regression source path"); digest(value)
    digest(m["source_sha256"])
    require(hashlib.sha256(json.dumps(m["source_files"],sort_keys=True).encode()).hexdigest() == m["source_sha256"], "regression source hash differs")
    if clean: source_at_revision(m)
    validate_toolchain(m["toolchain"])
    for key in ("python","host_compiler"):
        item = m[key]
        require(type(item) is dict and set(item) == {"path","sha256","version"} and type(item["path"]) is str and
                Path(item["path"]).is_absolute() and type(item["version"]) is str and item["version"], "incomplete DMA host tool identity")
        digest(item["sha256"])
    require(type(m["platform"]) is str and m["platform"] and type(m["build_directory"]) is str and Path(m["build_directory"]).is_absolute(), "invalid DMA build identity")
    dates = []
    for key in ("started_utc","finished_utc"):
        require(type(m[key]) is str, "invalid DMA regression date"); date = datetime.fromisoformat(m[key])
        require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0, "non-UTC DMA regression date"); dates.append(date)
    require(dates[1] > dates[0] and m["targets"] == list(TARGETS) and type(m["results"]) is list and len(m["results"]) == len(TARGETS),
            "incomplete/reordered DMA regression plan")
    inventory = {path.name}; summary = []; elapsed = 0
    for index,(target,entry) in enumerate(zip(TARGETS,m["results"]),1):
        name = f"{index:02d}-{target}.log"; inventory.add(name)
        require(type(entry) is dict and set(entry) == {"target","command","exit_code","elapsed_seconds","log","log_bytes","log_sha256"} and
                entry["target"] == target and entry["log"] == name and type(entry["exit_code"]) is int and entry["exit_code"] == 0, "failed/reordered DMA target")
        require(entry["command"] == ["make","--no-print-directory","-j2","BUILD_DIR="+m["build_directory"],target], "DMA regression command changed")
        seconds = entry["elapsed_seconds"]
        require(type(seconds) in (int,float) and math.isfinite(seconds) and seconds > 0, "invalid DMA regression elapsed time"); elapsed += seconds
        digest(entry["log_sha256"]); bench.integer(entry["log_bytes"],1)
        file = path.parent/name; require(file.is_file() and not file.is_symlink(), "missing/symlink DMA regression log"); raw = file.read_bytes()
        require(len(raw) == entry["log_bytes"] and hashlib.sha256(raw).hexdigest() == entry["log_sha256"], "DMA regression log changed")
        try: summary.append(validate_log(target,raw.decode("utf-8")))
        except ValueError as error: raise ValueError(f"{target}: {error}") from error
    require(elapsed <= (dates[1]-dates[0]).total_seconds()+1, "DMA child durations exceed total")
    require({p.name for p in path.parent.iterdir()} in (inventory,inventory|{"build"}), "unlisted DMA regression artifact")
    if (path.parent/"build").exists(): require((path.parent/"build").is_dir() and not (path.parent/"build").is_symlink(), "unsafe generated DMA build tree")
    return dict(revision=m["revision"],targets=summary,passing_scenarios=sum(s["passing_scenarios"] for s in summary),host_tests=summary[0]["host_tests"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("manifest",type=Path); args = parser.parse_args()
    try: print(json.dumps(audit(args.manifest),indent=2,sort_keys=True)); print("PASS: all 14 DMA regression targets, scenario coverage, raw hashes and complete Git provenance")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
