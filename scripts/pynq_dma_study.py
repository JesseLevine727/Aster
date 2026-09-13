#!/usr/bin/env python3
"""Run/audit all 144 predeclared CPU/DMA captures physically, cache-off then on."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import asterbench_dma as bench
import dma_overlay
import dma_physical as physical
import dma_results
import dma_study as study
from bench_results import ROOT, sha
import run_pynq_dma as collector

SCHEMA = "aster.dma.physical-study.v1"
DRIVER_FILES = physical.COLLECTOR_FILES | {"dma_study.py", "pynq_dma_study.py"}


def schedule():
    # Include the three independently rebuilt references in each cache series.
    # Warm boots within a capture never reprogram the PL.
    return [entry for caches in (0,1) for entry in study.plan() if entry["configuration"]["l1"] == caches]


def preflight(reference, overlays, *, clean):
    bench.require(set(overlays) == {0,1}, "both DMA cache overlays required")
    source = study.audit(reference,clean=clean)
    hardware = {c:dma_overlay.audit(path,clean=clean) for c,path in overlays.items()}
    bench.require(hardware[0]["caches"] is False and hardware[1]["caches"] is True and
                  hardware[0]["revision"] == hardware[1]["revision"] and hardware[0]["source_files"] == hardware[1]["source_files"],
                  "mixed/wrong FPGA sources or cache choices")
    for entry in source["entries"]:
        captured = bench.json_record((reference.parent/entry["file"]).read_text())
        physical.compatible(captured,hardware[entry["configuration"]["l1"]])
    return source,hardware


def summary(reports):
    specs = study.plan(); bench.require(set(reports) == {e["id"] for e in specs}, "incomplete physical experiment")
    statistics,series,repeats = {},{},[]
    for entry in specs:
        identifier = entry["id"]; report = reports[identifier]; pairs = []
        for boot in report["boots"]:
            pairs.extend(dict(boot=boot["boot"],**pair) for pair in bench.paired_summary(boot["records"]))
        sums = {method:{event:sum(row[event] for boot in report["boots"] for row in boot["records"] if row["method"] == method)
                        for event in sorted(bench.COUNTERS)} for method in ("cpu","dma")}
        ratios = [p["cpu_over_dma"] for p in pairs]
        point = dict(size=entry["configuration"]["size"],id=identifier,min_cpu_over_dma=min(ratios),max_cpu_over_dma=max(ratios),
                     summed_cpu_over_dma=sums["cpu"]["h0_cycles"]/sums["dma"]["h0_cycles"])
        statistics[identifier] = dict(pairs=pairs,summed_counters=sums,**point)
        if entry["fresh_repeat_of"]:
            base = reports[entry["fresh_repeat_of"]]
            repeats.append(dict(baseline=entry["fresh_repeat_of"],repeat=identifier,
                identical_records=bench.typed_equal([b["records"] for b in base["boots"]],[b["records"] for b in report["boots"]])))
        else:
            c = entry["configuration"]; series.setdefault(f"a{c['alignment']}-c{c['l1']}",[]).append(point)
    return dict(captures=len(specs),boots=sum(len(r["boots"]) for r in reports.values()),
        paired_jobs=sum(len(s["pairs"]) for s in statistics.values()),method_records=2*sum(len(s["pairs"]) for s in statistics.values()),
        statistics=statistics,series={key:study.series_summary(points) for key,points in series.items()},repeats=repeats,
        reference_counter_match=all(b["reference_comparison"]["exact_counter_match"] for r in reports.values() for b in r["boots"]),
        interpretation="Physical PYNQ UART/RAM/CPU/DMA-counter measurements; ratio below one is a DMA slowdown. Crossover is sampled, not universal or interpolated.",
        window="setup_copy_complete includes dispatch, descriptor setup, polling, coherent memory service and completion fences; excludes preparation/check/UART/stop",
        cache_policy="prepared_reinitialize at fixed source/destination bases; not cold-cache; balanced method order retained",
        cpu_usage="Polling occupies the CPU; no CPU-availability or overlap speedup claim.")


def audit(path,reference,overlays,*,clean=True):
    _source,hardware = preflight(reference,overlays,clean=clean)
    bench.require(not path.is_symlink(), "symlink physical-study manifest")
    m = bench.json_record(path.read_text())
    fields = {"schema","plan","status","reference_sha256","overlay_sha256","overlay_paths","initial_bitstream",
              "initial_bitstream_sha256","collector_revision","driver_files","entries","summary","started_utc","finished_utc"}
    bench.require(type(m) is dict and set(m) == fields and m["schema"] == SCHEMA and m["plan"] == study.PLAN and m["status"] == "complete",
                  "incomplete/unknown physical DMA study")
    bench.require(m["reference_sha256"] == sha(reference) and m["overlay_sha256"] == {str(c):sha(p) for c,p in overlays.items()},
                  "physical study inputs changed")
    for key in ("started_utc","finished_utc"):
        bench.require(type(m[key]) is str, "invalid study timestamp")
        instant = datetime.fromisoformat(m[key])
        bench.require(instant.tzinfo is not None and instant.utcoffset().total_seconds() == 0, "study timestamp must be UTC")
    bench.require(datetime.fromisoformat(m["finished_utc"]) >= datetime.fromisoformat(m["started_utc"]), "reversed physical timestamps")
    bench.require(type(m["overlay_paths"]) is dict and set(m["overlay_paths"]) == {"0","1"}, "missing physical overlay paths")
    for value in m["overlay_paths"].values():
        bench.require(type(value) is str and Path(value).is_absolute() and Path(value).name == "overlay.json", "invalid physical overlay path")
    bench.require(len(set(m["overlay_paths"].values())) == 2 and type(m["initial_bitstream"]) is str and Path(m["initial_bitstream"]).is_absolute(),
                  "invalid initial loaded overlay")
    dma_results.digest(m["initial_bitstream_sha256"])
    revision = m["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}",revision), "invalid collector revision")
    bench.require(type(m["driver_files"]) is dict and set(m["driver_files"]) == DRIVER_FILES, "incomplete batch driver provenance")
    for name,value in m["driver_files"].items():
        dma_results.digest(value)
        if clean:
            raw = subprocess.check_output(["git","show",f"{revision}:scripts/{name}"],cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == value, "physical-study driver differs from Git")
    expected = schedule(); entries = m["entries"]
    bench.require(type(entries) is list and len(entries) == len(expected), "incomplete physical coverage")
    prior,prior_sha = m["initial_bitstream"],m["initial_bitstream_sha256"]; reports = {}
    for actual,spec in zip(entries,expected):
        identifier = spec["id"]; caches = spec["configuration"]["l1"]
        bench.require(type(actual) is dict and set(actual) == {"id","file","sha256"} and actual["id"] == identifier and
                      actual["file"] == identifier+"/physical.json", "missing/reordered/unsafe physical study entry")
        target = path.parent/actual["file"]; bench.require(not target.parent.is_symlink(), "symlink physical capture directory")
        dma_results.digest(actual["sha256"]); bench.require(sha(target) == actual["sha256"], "physical report changed")
        report = physical.audit(target,reference.parent/(identifier+".json"),overlays[caches],clean=clean)
        loaded = str(Path(m["overlay_paths"][str(caches)]).parent/"aster_linux.bit")
        bench.require(report["collector_revision"] == revision and report["collector_files"] ==
                      {k:v for k,v in m["driver_files"].items() if k in physical.COLLECTOR_FILES}, "mixed physical collectors")
        bench.require(report["previous_bitstream"] == prior and report["previous_bitstream_sha256"] == prior_sha and
                      report["loaded_bitstream"] == loaded and report["downloaded"] is (loaded != prior), "unexpected PCAP/hash/restart sequence")
        prior,prior_sha = loaded,hardware[caches]["files"]["aster_linux.bit"]["sha256"]
        reports[identifier] = report
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {entry["id"] for entry in expected}, "unlisted study artifacts")
    actual_summary = summary(reports)
    bench.require(all(r["identical_records"] for r in actual_summary["repeats"]) and actual_summary["reference_counter_match"],
                  "physical repeat/reference measurement mismatch")
    bench.require(bench.typed_equal(actual_summary,m["summary"]), "physical crossover/aggregate changed")
    return m


def run(args):
    for path in (args.output,args.reference,args.overlay_off,args.overlay_on): bench.require(not path.is_symlink(), "symlink study input/output")
    output,reference = args.output.resolve(),args.reference.resolve()
    overlays = {0:args.overlay_off.resolve(),1:args.overlay_on.resolve()}
    bench.require(not output.exists(), "physical study output must be new")
    physical.finite(args.host_pause,0,10); physical.finite(args.timeout,1,120)
    bench.require(args.host_pause < args.timeout and type(args.collector_revision) is str and
                  re.fullmatch(r"[0-9a-f]{40}",args.collector_revision) and type(args.expected_loaded) is str and
                  Path(args.expected_loaded).is_absolute(), "invalid physical study options")
    dma_results.digest(args.expected_loaded_sha256)
    _source,hardware = preflight(reference,overlays,clean=False)  # All 144 cases before any PYNQ import/write.
    source = {name:sha(Path(__file__).parent/name) for name in sorted(DRIVER_FILES)}
    m = dict(schema=SCHEMA,plan=study.PLAN,status="running",reference_sha256=sha(reference),
        overlay_sha256={str(c):sha(p) for c,p in overlays.items()},overlay_paths={str(c):str(p) for c,p in overlays.items()},
        initial_bitstream=args.expected_loaded,initial_bitstream_sha256=args.expected_loaded_sha256,collector_revision=args.collector_revision,
        driver_files=source,entries=[],summary=None,started_utc=datetime.now(timezone.utc).isoformat(),finished_utc=None)
    output.mkdir(parents=True); path = output/"physical-study.json"
    def save(): path.write_text(json.dumps(m,indent=2,sort_keys=True)+"\n")
    save(); reports = {}; prior,prior_sha = args.expected_loaded,args.expected_loaded_sha256; specs = schedule()
    try:
        for index,entry in enumerate(specs,1):
            identifier = entry["id"]; caches = entry["configuration"]["l1"]; selected = overlays[caches]
            loaded = str(selected.parent/"aster_linux.bit")
            print(f"DMA_PHYSICAL_STUDY {index}/{len(specs)} {identifier}",flush=True)
            options = SimpleNamespace(output=output/identifier,reference=reference.parent/(identifier+".json"),overlay=selected,
                collector_revision=args.collector_revision,expected_loaded=prior,expected_loaded_sha256=prior_sha,download=loaded != prior,
                host_pause=args.host_pause,timeout=args.timeout)
            report = collector.run(options)
            target = options.output/"physical.json"; reports[identifier] = report
            prior,prior_sha = loaded,hardware[caches]["files"]["aster_linux.bit"]["sha256"]
            m["entries"].append(dict(id=identifier,file=identifier+"/physical.json",sha256=sha(target))); save()
        bench.require({name:sha(Path(__file__).parent/name) for name in DRIVER_FILES} == source, "physical study driver changed")
        m.update(status="complete",summary=summary(reports),finished_utc=datetime.now(timezone.utc).isoformat()); save()
        audit(path,reference,overlays,clean=False)
    except BaseException as error:
        m.update(status="failed",error=str(error),finished_utc=datetime.now(timezone.utc).isoformat()); save(); raise
    print(f"PASS: complete physical DMA study, 144 captures / 288 warm boots / 1152 paired jobs, {path}",flush=True)
    return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="action",required=True)
    for name in ("capture","audit"):
        sub = commands.add_parser(name)
        for key in ("reference","overlay-off","overlay-on"): sub.add_argument("--"+key,type=Path,required=True)
        if name == "capture":
            sub.add_argument("--output",type=Path,required=True); sub.add_argument("--collector-revision",required=True)
            sub.add_argument("--expected-loaded",required=True); sub.add_argument("--expected-loaded-sha256",required=True)
            sub.add_argument("--host-pause",type=float,default=0.2); sub.add_argument("--timeout",type=float,default=120)
        else: sub.add_argument("manifest",type=Path)
    args = parser.parse_args()
    try:
        if args.action == "capture": run(args)
        else:
            audit(args.manifest,args.reference,{0:args.overlay_off,1:args.overlay_on})
            print("PASS: complete physical DMA study and Git audit")
    except (ValueError,RuntimeError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
