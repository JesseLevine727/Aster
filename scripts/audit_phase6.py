#!/usr/bin/env python3
"""Audit the self-contained full-A/coherence closeout; never contacts the board.

The outer manifest prevents omitted/substituted packages. Inner auditors check
raw semantics, actual Git blobs, ELF/ROM/RAM/UART and routed FPGA reports. No
saved executable path is executed. README prose is intentionally not hashed.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess

import asterbench_coherent as bench
import audit_phase6_regressions as regressions
import coherent_overlay as overlay
from coherent_results import typed_equal
import functional_physical as functional
import pynq_coherent_study as study
from bench_results import sha, source_state

SCHEMA = "aster.coherent.closeout.v1"
REG = {name: f"regressions/{i:02d}-{name}.log" for i, name in enumerate(regressions.TARGETS, 1)}
FUNCTIONAL = tuple(f"{kind}-c{c}" for c in (0, 1) for kind in ("runtime", "lifecycle"))
REQUIREMENTS = {
    "01-full-A-core-integration": [REG[n] for n in ("check", "atomic-runtime-matrix", "coherent-runtime-matrix", "atomic-faults-matrix", "riscv-reference-matrix")],
    "02-coherence-data-authority-and-reset": [REG[n] for n in ("coherent-cache-matrix", "coherent-soc-matrix", "linux-coherent-matrix")],
    "03-real-core-ordering-progress-and-C-runtime": [REG["coherent-litmus-matrix"], REG["coherent-litmus-boundaries"]] +
        [f"reference/functional-c{c}/functional.json" for c in (0, 1)] + [f"physical/{name}/functional-physical.json" for name in FUNCTIONAL],
    "04-preserved-phase1-through-phase5": [REG[n] for n in regressions.TARGETS[:10]],
    "05-asterbench-v4-measurement-and-reproducibility": [REG[n] for n in ("coherent-bench-matrix", "coherent-bench-boundaries", "coherent-bench-sizes")] +
        ["reference/study/study.json", "physical/study/physical-study.json"],
    "06-routed-FPGA-and-physical-Linux-acceptance": ["fpga/c0/overlay.json", "fpga/c1/overlay.json", "physical/study/physical-study.json", "physical/final_state.json"] +
        [f"physical/{name}/functional-physical.json" for name in FUNCTIONAL],
    "07-clean-source-regressions-and-fresh-checkout": ["regressions/manifest.json", "verification/manifest.json", "verification/01-check.log"],
}
TOP = {"regressions", "fpga", "reference", "physical", "verification"}


def read(path): return bench.json_record(path.read_text())


def inventory(directory):
    bench.require(directory.is_dir() and not directory.is_symlink(), "invalid closeout directory")
    names = {p.name for p in directory.iterdir()}
    bench.require(TOP <= names <= TOP | {"manifest.json", "README.md"}, "incomplete/extra closeout packages")
    files = {}
    for path in sorted(directory.rglob("*")):
        bench.require(not path.is_symlink(), "symlink closeout evidence")
        if path.is_dir(): continue
        bench.require(path.is_file(), "non-file closeout artifact")
        name = path.relative_to(directory).as_posix()
        if name in ("manifest.json", "README.md"): continue
        files[name] = dict(sha256=sha(path), bytes=path.stat().st_size)
    bench.require(files, "empty closeout evidence")
    for paths in REQUIREMENTS.values():
        bench.require(all(path in files for path in paths), "missing requirement evidence")
    return files


def final_state(report, loaded):
    fields = {"schema", "observed_utc", "loaded_bitstream", "fclk0_mhz", "registers"}
    bench.require(type(report) is dict and set(report) == fields and report["schema"] == "aster.coherent.final-state.v1" and
                  report["loaded_bitstream"] == loaded and typed_equal(report["fclk0_mhz"], 31.25), "invalid final physical identity")
    expected = dict(abi=0x60001, clock_hz=31250000, control=0, features=3, fifo_count=0,
                    hart_status=0, harts=2, magic=0x41535452, status=0, stop_status=1)
    bench.require(typed_equal(report["registers"], expected), "final cluster not fully/safely STOPPED")
    bench.require(type(report["observed_utc"]) is str, "missing final observation time")
    date = datetime.fromisoformat(report["observed_utc"])
    bench.require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0, "invalid final UTC timestamp")


def stable_implementation(files):
    # Later host capture/audit helpers legitimately advance. The actual RTL,
    # firmware, vendor, hardware verification and build inputs may not drift.
    return {name: value for name, value in files.items() if not name.startswith(("scripts/", "verification/host/"))}


def evaluate(directory, *, current=False):
    reg = regressions.audit(directory/"regressions/manifest.json")
    reg_manifest = read(directory/"regressions/manifest.json")
    hardware = {c: overlay.audit(directory/f"fpga/c{c}/overlay.json") for c in (0, 1)}
    for c, hw in hardware.items():
        bench.require(hw["caches"] is bool(c) and hw["revision"] == reg["revision"] and
                      hw["source_files"] == reg_manifest["source_files"], "FPGA differs from complete regression sources")
    paths = {c: directory/f"fpga/c{c}/overlay.json" for c in (0, 1)}
    measured = study.audit(directory/"physical/study/physical-study.json", directory/"reference/study/study.json", paths)
    source = read(directory/"reference/study/study.json")
    bench.require(measured["summary"]["reference_counter_match"] is True, "physical study counters not reference-verified")
    reports = {}; functional_revisions = set(); previous = str(Path(measured["overlay_paths"]["0"]).parent/"aster_linux.bit")
    for c in (0, 1):
        for kind in ("runtime", "lifecycle"):
            name = f"{kind}-c{c}"; reference = directory/f"reference/functional-c{c}/functional.json"
            report = functional.audit(directory/f"physical/{name}/functional-physical.json", reference, paths[c])
            selected = str(Path(measured["overlay_paths"][str(c)]).parent/"aster_linux.bit")
            bench.require(report["kind"] == kind and report["previous_bitstream"] == previous and report["loaded_bitstream"] == selected and
                          report["downloaded"] is (selected != previous), "functional program/cache/PCAP sequence changed")
            previous = selected; reports[name] = report
            for program in read(reference)["programs"].values():
                functional_revisions.add(program["metadata"]["revision"])
                bench.require(stable_implementation(program["metadata"]["source_files"]) == stable_implementation(reg_manifest["source_files"]), "functional implementation drifted after regression")
    bench.require(len(functional_revisions) == 1 and len({r["collector_revision"] for r in reports.values()}) == 1, "mixed functional reference/collector sources")
    final_state(read(directory/"physical/final_state.json"), previous)
    fresh = regressions.audit(directory/"verification/manifest.json", check_only=True)
    fresh_manifest = read(directory/"verification/manifest.json")
    bench.require(stable_implementation(fresh_manifest["source_files"]) == stable_implementation(reg_manifest["source_files"]) and
                  fresh_manifest["toolchain"] == reg_manifest["toolchain"], "fresh checkout implementation/toolchain mismatch")
    counts = re.findall(r"(?m)^Ran (\d+) tests in [\d.]+s$", (directory/"verification/01-check.log").read_text())
    bench.require(len(counts) == 1 and int(counts[0]) >= 80, "missing final host/auditor mutation suite")
    if current:
        bench.require(source_state() == (fresh_manifest["source_files"], fresh_manifest["source_sha256"]), "current tracked build/audit sources differ from fresh verification")
    # Package layout is intentionally closed. Inner package auditors reject
    # extra files; these container checks reject extra packages/loose records.
    expected = {"regressions": {"manifest.json"} | {Path(p).name for p in REG.values()},
        "fpga": {"c0", "c1"}, "reference": {"study", "functional-c0", "functional-c1"},
        "physical": {"study", "final_state.json"} | set(FUNCTIONAL), "verification": {"manifest.json", "01-check.log"}}
    for name, children in expected.items():
        bench.require({p.name for p in (directory/name).iterdir()} == children, "unlisted closeout package: "+name)
    return dict(source_revisions=dict(implementation=reg["revision"], benchmark_reference=source["revision"],
        benchmark_collector=measured["collector_revision"], functional_reference=next(iter(functional_revisions)),
        functional_collector=reports[FUNCTIONAL[0]]["collector_revision"], fresh_verification=fresh["revision"]),
        summary=dict(regression_targets=22, regression_passing_scenarios=reg["passing_scenarios"],
            fresh_check_passing_scenarios=fresh["passing_scenarios"], fresh_host_tests=int(counts[0]),
            physical_benchmark_captures=57, physical_benchmark_boots=114, physical_benchmark_jobs=342,
            physical_functional_boots=8, physical_runtime_jobs=12, physical_lifecycle_selective_resets=32,
            controlled_physical_comparisons=61, exact_physical_reference_counters=True,
            final_safely_stopped=True, fpga_signoff={str(c): hw["signoff"] for c, hw in hardware.items()}))


def audit(directory, *, current=False):
    files = inventory(directory); m = read(directory/"manifest.json")
    bench.require(type(m) is dict and set(m) == {"schema", "status", "files", "requirements", "source_revisions", "summary"} and
                  m["schema"] == SCHEMA and m["status"] == "complete", "incomplete closeout manifest")
    bench.require(typed_equal(m["requirements"], REQUIREMENTS) and typed_equal(m["files"], files), "omitted/changed requirement/raw evidence")
    actual = evaluate(directory, current=current)
    bench.require(typed_equal(m["source_revisions"], actual["source_revisions"]) and typed_equal(m["summary"], actual["summary"]), "invented closeout provenance/summary")
    return m


def manifest(directory):
    path = directory/"manifest.json"
    bench.require(not path.exists(), "closeout manifest already exists; never overwrite evidence")
    files = inventory(directory); actual = evaluate(directory, current=True)
    value = dict(schema=SCHEMA, status="complete", files=files, requirements=REQUIREMENTS, **actual)
    with path.open("x") as stream: stream.write(json.dumps(value, indent=2, sort_keys=True)+"\n")
    audit(directory, current=True)
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("audit", "manifest")); parser.add_argument("directory", type=Path)
    parser.add_argument("--current", action="store_true", help="also require current build/audit sources to match fresh-checkout proof")
    args = parser.parse_args()
    try:
        result = manifest(args.directory) if args.action == "manifest" else audit(args.directory, current=args.current)
        print(json.dumps(dict(source_revisions=result["source_revisions"], summary=result["summary"]), indent=2, sort_keys=True))
        print("PASS: all seven Phase 6/full-A requirements, raw evidence, Git provenance and final physical STOPPED")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: parser.exit(1, f"FAIL: {error}\n")
