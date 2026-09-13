#!/usr/bin/env python3
"""Run/audit the entire fixed v4 study on the PYNQ with one cache-mode switch."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import asterbench_coherent as bench
import coherent_overlay
import coherent_physical as physical
import coherent_results
import coherent_study as study
from bench_results import ROOT, sha
import run_pynq_coherent as collector

SCHEMA = "aster.coherent.physical-study.v1"
DRIVER_FILES = physical.COLLECTOR_FILES | {"coherent_study.py", "pynq_coherent_study.py"}


def schedule():
    # Finish all c1 cases, including the independent firmware rebuild, then c0.
    # Each capture still contains two warm boots with no intervening download.
    return [entry for caches in (1, 0) for entry in study.plan() if entry["configuration"]["l1"] == caches]


def preflight(reference, overlays, *, clean):
    source = study.audit(reference, clean=clean)
    hardware = {key: coherent_overlay.audit(path, clean=clean) for key, path in overlays.items()}
    bench.require(set(overlays) == {0, 1} and hardware[0]["caches"] is False and hardware[1]["caches"] is True,
                  "physical study needs both matching cache-off/on overlays")
    bench.require(hardware[0]["revision"] == hardware[1]["revision"] and
                  hardware[0]["source_files"] == hardware[1]["source_files"], "mixed FPGA source versions")
    for entry in source["entries"]:
        captured = bench.json_record((reference.parent/entry["file"]).read_text())
        physical.compatible(captured, hardware[entry["configuration"]["l1"]])
    return source


def summary(reports, reference):
    # Only the audited physical records provide measurements. Configuration and
    # compiler/source identity come from the already audited matching firmware.
    measured = {}
    for entry in study.plan():
        identifier = entry["id"]
        firmware = bench.json_record((reference.parent/(identifier+".json")).read_text())
        measured[identifier] = dict(configuration=firmware["configuration"], metadata=firmware["metadata"],
            toolchain=firmware["toolchain"], records=[boot["records"] for boot in reports[identifier]["boots"]])
    result = study.summarize(measured)
    result["interpretation"] = "Physical PYNQ UART/RAM/counter measurements. Ratio below 1 is a slowdown. No cross-workload aggregate speedup."
    result["reference_counter_match"] = all(boot["reference_comparison"]["exact_counter_match"] for report in reports.values() for boot in report["boots"])
    return result


def audit(path, reference, overlays, *, clean=True):
    preflight(reference, overlays, clean=clean)
    bench.require(not path.is_symlink(), "symlink physical-study manifest")
    m = bench.json_record(path.read_text())
    fields = {"schema", "plan", "status", "reference_sha256", "overlay_sha256", "overlay_paths", "initial_bitstream",
              "collector_revision", "driver_files", "entries", "summary"}
    bench.require(type(m) is dict and set(m) == fields and m["schema"] == SCHEMA and m["plan"] == study.PLAN and
                  m["status"] == "complete", "incomplete/unknown physical study")
    bench.require(m["reference_sha256"] == sha(reference) and m["overlay_sha256"] == {str(c): sha(p) for c, p in overlays.items()},
                  "physical study source package changed")
    bench.require(type(m["overlay_paths"]) is dict and set(m["overlay_paths"]) == {"0", "1"}, "missing physical overlay paths")
    for value in m["overlay_paths"].values():
        bench.require(type(value) is str and Path(value).is_absolute() and Path(value).name == "overlay.json", "invalid physical overlay path")
    bench.require(len(set(m["overlay_paths"].values())) == 2 and type(m["initial_bitstream"]) is str and
                  Path(m["initial_bitstream"]).is_absolute(), "invalid initial overlay identity")
    revision = m["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}", revision), "invalid study collector revision")
    bench.require(type(m["driver_files"]) is dict and set(m["driver_files"]) == DRIVER_FILES, "incomplete physical-study driver provenance")
    for name, value in m["driver_files"].items():
        coherent_results.digest(value)
        if clean:
            raw = subprocess.check_output(["git", "show", f"{revision}:scripts/{name}"], cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == value, "physical-study driver differs from Git")
    expected = schedule(); entries = m["entries"]
    bench.require(type(entries) is list and len(entries) == len(expected), "incomplete physical workload coverage")
    prior = m["initial_bitstream"]; reports = {}
    for actual, spec in zip(entries, expected):
        identifier = spec["id"]; caches = spec["configuration"]["l1"]
        bench.require(type(actual) is dict and set(actual) == {"id", "file", "sha256"} and actual["id"] == identifier and
                      actual["file"] == identifier+"/physical.json", "missing/reordered/unsafe physical study case")
        target = path.parent/actual["file"]
        bench.require(not target.parent.is_symlink(), "symlink physical capture directory")
        coherent_results.digest(actual["sha256"])
        bench.require(sha(target) == actual["sha256"], "physical report changed")
        report = physical.audit(target, reference.parent/(identifier+".json"), overlays[caches], clean=clean)
        loaded = str(Path(m["overlay_paths"][str(caches)]).parent/"aster_linux.bit")
        bench.require(report["collector_revision"] == revision and report["collector_files"] ==
                      {name: value for name, value in m["driver_files"].items() if name in physical.COLLECTOR_FILES}, "mixed physical collectors")
        bench.require(report["previous_bitstream"] == prior and report["loaded_bitstream"] == loaded and
                      report["downloaded"] is (loaded != prior), "unexpected PCAP/restart sequence")
        prior = loaded; reports[identifier] = report
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {entry["id"] for entry in expected}, "unlisted physical study artifacts")
    bench.require(coherent_results.typed_equal(summary(reports, reference), m["summary"]), "physical study aggregate changed")
    return m


def run(args):
    output = args.output.resolve(); reference = args.reference.resolve()
    overlays = {0: args.overlay_off.resolve(), 1: args.overlay_on.resolve()}
    bench.require(not output.exists(), "physical study output must be a new directory")
    physical.finite(args.host_pause, 0, 10); physical.finite(args.timeout, 1, 120)
    bench.require(args.host_pause < args.timeout and re.fullmatch(r"[0-9a-f]{40}", args.collector_revision) and
                  Path(args.expected_loaded).is_absolute(), "invalid physical study options")
    preflight(reference, overlays, clean=False)  # All 57 references/overlays before any PYNQ import/write.
    source = {name: sha(Path(__file__).parent/name) for name in sorted(DRIVER_FILES)}
    manifest = dict(schema=SCHEMA, plan=study.PLAN, status="running", reference_sha256=sha(reference),
        overlay_sha256={str(c): sha(path) for c, path in overlays.items()}, overlay_paths={str(c): str(path) for c, path in overlays.items()},
        initial_bitstream=args.expected_loaded, collector_revision=args.collector_revision, driver_files=source, entries=[], summary=None)
    output.mkdir(parents=True); path = output/"physical-study.json"
    def save(): path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    save(); reports = {}; prior = args.expected_loaded
    try:
        for index, entry in enumerate(schedule(), 1):
            identifier = entry["id"]; selected = overlays[entry["configuration"]["l1"]]
            loaded = str(selected.parent/"aster_linux.bit")
            print(f"PHYSICAL_STUDY {index}/57 {identifier}", flush=True)
            options = SimpleNamespace(output=output/identifier, reference=reference.parent/(identifier+".json"), overlay=selected,
                collector_revision=args.collector_revision, expected_loaded=prior, download=loaded != prior,
                host_pause=args.host_pause, timeout=args.timeout)
            report = collector.run(options)  # This collector always stops an identified bridge, even on failure.
            target = options.output/"physical.json"
            reports[identifier] = report; prior = loaded
            manifest["entries"].append(dict(id=identifier, file=identifier+"/physical.json", sha256=sha(target))); save()
        bench.require({name: sha(Path(__file__).parent/name) for name in DRIVER_FILES} == source, "study driver changed during capture")
        manifest.update(status="complete", summary=summary(reports, reference)); save()
        audit(path, reference, overlays, clean=False)
    except BaseException as error:
        manifest.update(status="failed", error=str(error)); save(); raise
    print(f"PASS: complete physical coherent study, 57 captures / 114 warm boots / 342 jobs, {path}", flush=True)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__); commands = p.add_subparsers(dest="action", required=True)
    for name in ("capture", "audit"):
        sub = commands.add_parser(name)
        for key in ("reference", "overlay-off", "overlay-on"): sub.add_argument("--"+key, type=Path, required=True)
        if name == "capture":
            sub.add_argument("--output", type=Path, required=True); sub.add_argument("--collector-revision", required=True)
            sub.add_argument("--expected-loaded", required=True); sub.add_argument("--host-pause", type=float, default=0.2)
            sub.add_argument("--timeout", type=float, default=120)
        else: sub.add_argument("manifest", type=Path)
    args = p.parse_args()
    try:
        if args.action == "capture": run(args)
        else: audit(args.manifest, args.reference, {0: args.overlay_off, 1: args.overlay_on}); print("PASS: full physical coherent study and Git audit")
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error: p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__": main()
