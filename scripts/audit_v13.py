#!/usr/bin/env python3
"""Read-only outer audit for the v1.3 50 MHz slow-corner closeout.

Inventories the bundle, re-checks the routed overlay's timing/route reports,
the physical acceptance capture and the clean-check log, and binds the manifest
to the committed source snapshot.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


SCHEMA = "aster.v1.3.closeout.v1"
SOURCE_SCHEMA = "aster.v1.3.source.v1"
REVISION = "b3954ce52f2f08088693c1282f28d5016e0e5712"
TOP_DIRS = {"spec", "fpga", "physical", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
REQUIREMENTS = {
    "01-contract": ["spec/v1.3.md"],
    "02-routed-overlay": ["fpga/timing_summary.rpt", "fpga/utilization_routed.rpt",
                          "fpga/route_status.rpt", "fpga/aster_linux.bit", "fpga/aster_linux.hwh"],
    "03-analysis": ["analysis.md"],
    "04-frozen-clean-check": ["verification/make-check.log"],
    "05-frozen-source": ["source/source-state.json"],
    "06-physical-acceptance": ["physical/physical.json"],
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


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def source_state():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], text=False
    ).decode().split("\0")
    files = {}
    for name in sorted(set(names)):
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/")):
            files[name] = sha(Path(name))
    revision = _git("log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/", "vendor/",
                    "verification/", "fpga/")
    require(revision, "implementation source revision is unavailable")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid v1.3 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra v1.3 top-level package")
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
            "missing v1.3 requirement evidence")
    return files


def audit_fpga(directory):
    timing = (directory / "timing_summary.rpt").read_text().splitlines()
    for index, line in enumerate(timing):
        if "WNS(ns)" in line and "TNS(ns)" in line:
            row = timing[index + 2].split()
            wns, failing = float(row[0]), int(row[2])
            break
    else:
        raise ValueError("timing summary has no WNS row")
    require(wns > 0, f"routed overlay has negative slack ({wns} ns)")
    require(failing == 0, "routed overlay has failing endpoints")
    route = (directory / "route_status.rpt").read_text()
    match = re.search(r"routing errors[.\s]*:\s*(\d+)", route)
    require(match, "route status has no routing-error count")
    require(int(match.group(1)) == 0, "routed overlay has routing errors")
    return {"wns_ns": wns, "failing_endpoints": failing}


def audit_analysis(directory):
    analysis = (directory / "analysis.md").read_text()
    for phrase in ("## Timing", "## Correctness", "## Frequency", "## Compatibility"):
        require(phrase in analysis, f"analysis is missing section: {phrase}")
    for phrase in ("+0.191", "0 of 56 320", "48.17%"):
        require(phrase in analysis, f"analysis is missing a measured result: {phrase}")
    return {"sections": 4}


def audit_physical(directory):
    report = read(directory / "physical.json")
    require(report.get("schema") == "aster.v1.1.physical-mem.v1" and report["status"] == "complete",
            "physical package is incomplete")
    require(report["source_revision"] == REVISION and abs(report["clock_mhz"] - 50.0) < 1e-6
            and report["programmed"] is True, "physical identity/revision/clock/programming differs")
    require(report["handoff_preflight"]["clock_hz"] == 50_000_000, "handoff clock is not 50 MHz")
    require(report["name"] == "reduce_parallel", "physical workload differs")
    require(len(report["boots"]) == 2, "physical package did not run two warm boots")
    for boot in report["boots"]:
        require(boot["checksum"] == "0x5c808000", "physical checksum does not match the oracle")
        require("clock_hz=50000000" in boot["uart_output"], "physical record is not at 50 MHz")
        require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                "was not running before STOP")
        require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                and boot["after_stop"]["fifo_count"] == 0, "did not stop cleanly")
    return {"boots": len(report["boots"]), "checksum": report["boots"][0]["checksum"],
            "clock_mhz": report["clock_mhz"]}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    require("PASS: 23 frozen v1.0 interfaces match the RTL" in log, "frozen interface guard did not pass")
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    if current:
        require(typed_equal(source, source_state()), "current source differs from the v1.3 snapshot")
    return {
        "source_revision": source["revision"],
        "fpga": audit_fpga(directory / "fpga"),
        "physical": audit_physical(directory / "physical"),
        "analysis": audit_analysis(directory),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete v1.3 manifest")
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
    print("PASS: v1.3 routed overlay, physical acceptance and frozen source")


if __name__ == "__main__":
    main()
