#!/usr/bin/env python3
"""Read-only outer audit for the v1.2 NPU-geometry closeout.

Inventories the bundle, re-validates the geometry study against its independent
oracle, checks the area/timing and analysis evidence and the clean-check log,
and binds the manifest to the committed source snapshot.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

try:
    from .npu_geometry_study import audit_study
except ImportError:
    from npu_geometry_study import audit_study


SCHEMA = "aster.v1.2.closeout.v1"
SOURCE_SCHEMA = "aster.v1.2.source.v1"
REVISION = "114f9c9d2456cc5559dc7b3c3da5b19471adc76f"
TOP_DIRS = {"spec", "studies", "area", "physical", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
REQUIREMENTS = {
    "01-contract": ["spec/npu-geometry.md"],
    "02-geometry-study": ["studies/geometry/study.json"],
    "03-area-timing": ["area/area.json", "area/README.md"],
    "04-analysis": ["analysis.md"],
    "05-frozen-clean-check": ["verification/make-check.log"],
    "06-frozen-source": ["source/source-state.json"],
    "07-physical-acceptance": ["physical/physical.json"],
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
    require(directory.is_dir() and not directory.is_symlink(), "invalid v1.2 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra v1.2 top-level package")
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
            "missing v1.2 requirement evidence")
    return files


def audit_studies(directory):
    study = read(directory / "geometry" / "study.json")
    return audit_study(study)


def audit_area(directory):
    area = read(directory / "area.json")
    require(area.get("schema") == "aster.v1.2.area.v1", "area schema is wrong")
    configs = area["configs"]
    require(set(configs) == {"2x2", "4x4", "8x8"}, "area geometries differ")
    for name, config in configs.items():
        require(config["wns_ns"] > 0, f"{name} has failing timing")
        require(config["luts"] > 0 and config["bram"] > 0, f"{name} area is incomplete")
    require(configs["2x2"]["luts"] < configs["4x4"]["luts"] < configs["8x8"]["luts"],
            "area ordering is inconsistent")
    require(configs["2x2"]["engine_reads"] > configs["4x4"]["engine_reads"] > configs["8x8"]["engine_reads"],
            "engine read ordering is inconsistent")
    readme = (directory / "README.md").read_text()
    for phrase in ("+1 741", "+6 919", "37.3 MHz"):
        require(phrase in readme, f"area analysis is missing: {phrase}")
    return {"geometries": len(configs)}


def audit_analysis(directory):
    analysis = (directory / "analysis.md").read_text()
    for phrase in ("## Correctness", "## Performance", "## Area and frequency",
                   "## Software", "## Compatibility"):
        require(phrase in analysis, f"analysis is missing section: {phrase}")
    for phrase in ("0x07df8000", "40%", "2.7%"):
        require(phrase in analysis, f"analysis is missing a measured result: {phrase}")
    return {"sections": 5}


def audit_physical(directory):
    report = read(directory / "physical.json")
    require(report.get("schema") == "aster.v1.2.physical-mem.v1" and report["status"] == "complete",
            "physical package is incomplete")
    require(report["source_revision"] == REVISION and abs(report["clock_mhz"] - 31.25) < 1e-6
            and report["programmed"] is True, "physical identity/revision/clock/programming differs")
    require(report["name"] == "conv2d_npu", "physical workload differs")
    require(len(report["boots"]) == 2, "physical package did not run two warm boots")
    for boot in report["boots"]:
        require(boot["checksum"] == "0x07df8000", "physical checksum does not match the oracle")
        require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                "was not running before STOP")
        require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                and boot["after_stop"]["fifo_count"] == 0, "did not stop cleanly")
    return {"boots": len(report["boots"]), "checksum": report["boots"][0]["checksum"]}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    require("PASS: 23 frozen v1.0 interfaces match the RTL" in log, "frozen interface guard did not pass")
    require("PASS: INT8 4x4 array tiles=252" in log, "4x4 array regression missing")
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    if current:
        require(typed_equal(source, source_state()), "current source differs from the v1.2 snapshot")
    return {
        "source_revision": source["revision"],
        "studies": audit_studies(directory / "studies"),
        "area": audit_area(directory / "area"),
        "physical": audit_physical(directory / "physical"),
        "analysis": audit_analysis(directory),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete v1.2 manifest")
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
    print("PASS: v1.2 NPU-geometry study, area and frozen source")


if __name__ == "__main__":
    main()
