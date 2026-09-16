#!/usr/bin/env python3
"""Read-only outer audit for the Phase 12 streaming-ECG closeout.

Inventories the bundle, re-runs the v10 validator and the independent pipeline
oracle on the retained study and physical records, checks the routed FPGA
reports and the full make-check log, and binds the manifest to the committed
source snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .asterbench_v10 import validate_line, require
    from .workload_reference import verify as verify_workload
    from .pynq_handoff import validate_handoff
except ImportError:
    from asterbench_v10 import validate_line, require
    from workload_reference import verify as verify_workload
    from pynq_handoff import validate_handoff


SCHEMA = "aster.phase12.closeout.v1"
SOURCE_SCHEMA = "aster.phase12.source.v1"
REVISION = "f1f62e2b327d34b2212045396585c8e86a84a6f3"
NAME = "streaming_ecg"
TOP = {"spec", "input", "simulation", "fpga", "physical", "source", "verification"}
REQUIREMENTS = {
    "01-contract-and-sample-provenance": ["spec/phase12.md", "input/ecg_segment.json"],
    "02-full-regressions-and-host-tests": ["verification/make-check.log"],
    "03-streaming-study-and-fresh-repeats": ["simulation/study.json"],
    "04-routed-fpga-overlay": [
        "fpga/aster_linux.bit", "fpga/aster_linux.hwh", "fpga/timing_summary.rpt",
        "fpga/drc.rpt", "fpga/methodology.rpt", "fpga/route_status.rpt",
        "fpga/utilization_routed.rpt",
    ],
    "05-physical-pynq-acceptance": ["physical/physical.json"],
    "06-clean-source-and-immutable-mapping": ["source/source-state.json"],
}


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
         "verification/", "fpga/"], text=True).strip()
    require(revision, "implementation source revision is unavailable")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files, "sha256": fingerprint}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 12 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra Phase 12 top-level package")
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
            "missing Phase 12 requirement evidence")
    return files


def audit_input(directory):
    segment = read(directory / "ecg_segment.json")
    require(segment.get("schema") == "aster.ecg.segment.v1", "ECG segment schema is wrong")
    require(segment["record"] == "100" and segment["count"] == 1024, "ECG segment provenance differs")
    require(len(segment["samples"]) == segment["count"], "ECG segment length differs")
    samples = bytes((value & 0xFF) for value in segment["samples"])
    require(hashlib.sha256(samples).hexdigest() == segment["sha256"], "ECG sample hash differs")
    return {"record": segment["record"], "count": segment["count"]}


def audit_study(directory):
    study = read(directory / "study.json")
    require(study.get("schema") == "aster.phase12.study.v1" and study["name"] == NAME,
            "study schema is not Phase 12")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(len(study["repeats"]) >= 2, "study has too few repeats")
    parsed = [validate_line(record, name=NAME) for record in study["repeats"]]
    for record in parsed:
        verify_workload(record, NAME)
    require(all(item == parsed[0] for item in parsed), "fresh repeats do not reproduce the record")
    return {"repeats": len(parsed), "cycles": parsed[0]["cycles"], "checksum": parsed[0]["checksum"]}


def audit_fpga(directory):
    handoff = validate_handoff(directory / "aster_linux.hwh", 2, expected_coherent=True,
                               expected_cache=True, expected_dma=True, expected_dot8=True,
                               expected_npu=True)
    require(handoff.get("bridge_version") == 0x00090001, "combined handoff identity is wrong")
    timing = (directory / "timing_summary.rpt").read_text()
    require(re.search(r"\n\s+[0-9]+\.[0-9]+\s+0\.000\s+0\s+\d+\s+[0-9]+\.[0-9]+\s+0\.000", timing),
            "timing summary has failing setup/hold endpoints")
    drc = (directory / "drc.rpt").read_text()
    methodology = (directory / "methodology.rpt").read_text()
    route = (directory / "route_status.rpt").read_text()
    require("Design State : Fully Routed" in drc and "Checks found:" in drc, "DRC signoff incomplete")
    require(not re.search(r"\|\s*\S+\s*\|\s*(?:Error|Critical)\s*\|", drc), "DRC error/critical findings")
    require("Checks found: 0" in methodology, "methodology is not clean")
    require(re.search(r"# of nets with routing errors.*:\s+0\s+:?\s*$", route, re.MULTILINE),
            "routing errors present")
    routed = (directory / "utilization_routed.rpt").read_text()
    require("Slice LUTs" in routed and "DSPs" in routed, "routed utilization report incomplete")
    return {"bridge_version": hex(handoff["bridge_version"])}


def audit_physical(directory):
    report = read(directory / "physical.json")
    require(report.get("schema") == "aster.phase12.physical-mem.v1" and report["status"] == "complete",
            "physical package is incomplete")
    require(report["name"] == NAME and abs(report["clock_mhz"] - 31.25) < 1e-6 and report["programmed"] is True,
            "physical identity/clock/programming differs")
    require(len(report["boots"]) == 2, "physical package did not run two warm boots")
    for boot in report["boots"]:
        record = boot["uart_output"].strip()
        parsed = validate_line(record if record.endswith("\n") else record + "\n", name=NAME)
        verify_workload(parsed, NAME)
        require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                "was not running before STOP")
        require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                and boot["after_stop"]["fifo_count"] == 0, "did not stop cleanly")
    return {"boots": len(report["boots"])}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    for phrase in ("PASS: v10 streaming_ecg", "PASS: v10 cifar_cnn", "PASS: INT8 4x4 array",
                   "PASS: parallel jobs"):
        require(phrase in log, f"missing verification evidence: {phrase}")
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    if current:
        require(typed_equal(source, source_state()), "current source differs from closeout source snapshot")
    return {
        "source_revision": source["revision"], "source_sha256": source["sha256"],
        "input": audit_input(directory / "input"),
        "simulation": audit_study(directory / "simulation"),
        "fpga": audit_fpga(directory / "fpga"),
        "physical": audit_physical(directory / "physical"),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 12 manifest")
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
    print("PASS: all Phase 12 requirements, independent study/physical audits and clean source")


if __name__ == "__main__":
    main()
