#!/usr/bin/env python3
"""Read-only outer audit for the Phase 10 cross-engine closeout.

Inventories every copied artifact, re-runs the independent AsterBench v8 oracle
on the retained study and physical records, recomputes the study ratios, checks
the routed FPGA reports and the full make-check log, and (with --current) binds
the retained source snapshot to the working tree. It never touches the board.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .asterbench_v8 import study_plan, validate_line, require as _require
    from .pynq_handoff import validate_handoff
    from .xe_study import summarize
except ImportError:
    from asterbench_v8 import study_plan, validate_line, require as _require
    from pynq_handoff import validate_handoff
    from xe_study import summarize


SCHEMA = "aster.phase10.closeout.v1"
SOURCE_SCHEMA = "aster.phase10.source.v1"
REVISION = "8371c3d66bca995db95147a3eac81a072d5b6283"
TOP = {"spec", "simulation", "fpga", "physical", "source", "verification"}
PHYSICAL = ("gemm_scalar", "gemm_multicore", "gemm_dot8", "gemm_npu", "dot_npu", "fir_multicore")
REQUIREMENTS = {
    "01-contract-and-cross-engine-scope": ["spec/phase10.md"],
    "02-full-regressions-and-host-tests": ["verification/make-check.log"],
    "03-cross-engine-study-and-fresh-repeats": [
        "simulation/primary/study.json", "simulation/repeat1/study.json",
        "simulation/repeat2/study.json", "simulation/compare.log",
    ],
    "04-fpga-resource-and-routed-timing": [
        "fpga/aster_linux.bit", "fpga/aster_linux.hwh", "fpga/timing_summary.rpt",
        "fpga/drc.rpt", "fpga/methodology.rpt", "fpga/route_status.rpt",
        "fpga/utilization_routed.rpt", "fpga/utilization_synth_base.rpt",
        "fpga/utilization_synth_dma.rpt", "fpga/utilization_synth_dot8.rpt",
        "fpga/utilization_synth_npu.rpt",
    ],
    "05-physical-pynq-acceptance": [f"physical/{name}/physical.json" for name in PHYSICAL],
    "06-clean-source-and-immutable-mapping": ["source/source-state.json"],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def typed_equal(left, right):
    return type(left) is type(right) and left == right


def source_state():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], text=False
    ).decode().split("\0")
    files = {}
    for name in sorted(set(names)):
        path = Path(name)
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/")):
            files[name] = sha(path)
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    revision = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/", "vendor/",
         "verification/", "fpga/"],
        text=True,
    ).strip()
    require(revision, "implementation source revision is unavailable")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files, "sha256": fingerprint}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 10 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra Phase 10 top-level package")
    files = {}
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "symlink evidence is not immutable evidence")
        if path.is_dir():
            continue
        require(path.is_file(), "non-file evidence artifact")
        name = path.relative_to(directory).as_posix()
        if name in {"README.md", "manifest.json"}:
            continue
        files[name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    require(all(path in files for paths in REQUIREMENTS.values() for path in paths),
            "missing Phase 10 requirement evidence")
    return files


def audit_study(path):
    study = read(path)
    require(study.get("schema") == "aster.phase10.study.v1", "study schema is not Phase 10")
    require(study["plan"] == study_plan(), "study plan does not match the frozen Phase 10 plan")
    require(len(study["captures"]) == len(study_plan()), "study capture count is incomplete")
    for capture, planned in zip(study["captures"], study_plan()):
        for key in ("capture", "kernel", "method", "m", "n", "k", "taps", "placement", "l1"):
            require(capture[key] == planned[key], f"capture {planned['capture']} disagrees on {key}")
        for line in capture["records"]:
            validate_line(line, method=str(planned["method"]), kernel=str(planned["kernel"]),
                          jobs=int(study["provenance"]["jobs"]))
    require(summarize(study["captures"]) == study["summary"], "retained study summary disagrees with recomputation")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(study["provenance"]["revision"] == REVISION, "study revision differs from the closeout revision")
    return study["summary"]


def audit_simulation(directory):
    primary = audit_study(directory / "primary" / "study.json")
    repeat1 = audit_study(directory / "repeat1" / "study.json")
    repeat2 = audit_study(directory / "repeat2" / "study.json")
    require(primary == repeat1 == repeat2, "fresh repeats do not reproduce the primary study")
    compare = (directory / "compare.log").read_text()
    require(compare.count("PASS: Phase 10 fresh repeat reproduces") == 2, "repeat comparison log is incomplete")
    return {"captures": len(study_plan()), "ratios": len(primary["ratios"]),
            "rows": len(primary["rows"])}


def audit_fpga(directory):
    handoff = validate_handoff(directory / "aster_linux.hwh", 2, expected_coherent=True,
                               expected_cache=True, expected_dma=True, expected_dot8=True,
                               expected_npu=True)
    require(handoff.get("bridge_version") == 0x00090001 and handoff["dot8"] and handoff["npu"],
            "combined DOT8+NPU handoff identity is wrong")
    timing = (directory / "timing_summary.rpt").read_text()
    require(re.search(r"\n\s+[0-9]+\.[0-9]+\s+0\.000\s+0\s+\d+\s+[0-9]+\.[0-9]+\s+0\.000", timing),
            "timing summary has failing setup/hold endpoints")
    drc = (directory / "drc.rpt").read_text()
    methodology = (directory / "methodology.rpt").read_text()
    route = (directory / "route_status.rpt").read_text()
    require("Design State : Fully Routed" in drc and "Checks found:" in drc, "DRC signoff is incomplete")
    require(not re.search(r"\|\s*\S+\s*\|\s*(?:Error|Critical)\s*\|", drc), "DRC contains error/critical findings")
    require("Design State : Fully Routed" in methodology and "Checks found: 0" in methodology,
            "methodology is not clean")
    require(re.search(r"# of nets with routing errors.*:\s+0\s+:?\s*$", route, re.MULTILINE),
            "routing errors present")
    routed = (directory / "utilization_routed.rpt").read_text()
    require("Slice LUTs" in routed and "DSPs" in routed, "routed utilization report is incomplete")
    return {"bridge_version": hex(handoff["bridge_version"])}


def audit_physical(directory):
    packages = 0
    boots = 0
    for name in PHYSICAL:
        report = read(directory / name / "physical.json")
        require(report.get("schema") == "aster.xe.physical-mem.v1" and report["status"] == "complete",
                f"{name} physical package is incomplete")
        require(report["source_revision"] == REVISION, f"{name} physical revision differs from the closeout revision")
        require(abs(report["clock_mhz"] - 31.25) < 1e-6, f"{name} physical clock is not 31.25 MHz")
        require(report["programmed"] is True, f"{name} did not program the overlay")
        require(len(report["boots"]) == 2, f"{name} did not run two warm boots")
        for boot in report["boots"]:
            records = [line for line in boot["uart_output"].splitlines(keepends=True)
                       if line.startswith("ASTERBENCH,")]
            require(records, f"{name} boot {boot['boot']} emitted no records")
            for line in records:
                validate_line(line, method=report["method"], kernel=report["kernel"], jobs=report["jobs"])
            require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                    f"{name} boot {boot['boot']} was not running before STOP")
            require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                    and boot["after_stop"]["fifo_count"] == 0,
                    f"{name} boot {boot['boot']} did not stop cleanly")
            boots += 1
        packages += 1
    return {"packages": packages, "boots": boots}


def audit_logs(directory):
    log = (directory / "verification" / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    for phrase in ("PASS: INT8 4x4 array", "PASS: Phase 9 actual-core NPU runtime",
                   "PASS: parallel jobs", "PASS: v8 gemm/scalar"):
        require(phrase in log, f"missing verification evidence: {phrase}")
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_test_runs": len(counts), "host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    if current:
        require(typed_equal(source, source_state()), "current source differs from closeout source snapshot")
    simulation = audit_simulation(directory / "simulation")
    fpga = audit_fpga(directory / "fpga")
    physical = audit_physical(directory / "physical")
    logs = audit_logs(directory)
    return {"source_revision": source["revision"], "source_sha256": source["sha256"],
            "simulation": simulation, "fpga": fpga, "physical": physical, "verification": logs}


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 10 manifest")
    require(typed_equal(manifest["requirements"], REQUIREMENTS), "manifest requirements differ")
    require(typed_equal(manifest["files"], files), "manifest file inventory differs from disk")
    summary = evaluate(directory, current=current)
    require(typed_equal(manifest["summary"], summary), "manifest summary differs from re-evaluation")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(args.directory, current=args.current)
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("PASS: all Phase 10 requirements, independent study/physical audits and clean source")


if __name__ == "__main__":
    main()
