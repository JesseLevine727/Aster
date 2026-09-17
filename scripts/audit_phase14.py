#!/usr/bin/env python3
"""Read-only outer audit for the Phase 14 design-space closeout.

Inventories the bundle, re-validates every retained study against its
independent oracle, checks the analysis and the frozen clean-check log, and
binds the manifest to the committed source snapshot.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

try:
    from .dse_study import audit_sweep
except ImportError:
    from dse_study import audit_sweep


SCHEMA = "aster.phase14.closeout.v1"
SOURCE_SCHEMA = "aster.phase14.source.v1"
REVISION = "b050a81e6586f586a8a14e9bb642710cc2d19ebc"
TOP_DIRS = {"spec", "studies", "area", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
SWEEPS = ["memory-latency", "cache-geometry", "core-scaling", "compute-placement"]
REQUIREMENTS = {
    "01-contract": ["spec/phase14.md"],
    "02-memory-latency": ["studies/memory-latency/study.json"],
    "03-cache-geometry": ["studies/cache-geometry/study.json"],
    "04-core-scaling": ["studies/core-scaling/study.json"],
    "05-compute-placement": ["studies/compute-placement/study.json"],
    "06-analysis": ["analysis.md"],
    "07-frozen-clean-check": ["verification/make-check.log"],
    "08-frozen-source": ["source/source-state.json"],
    "09-routed-area-timing": ["area/area.json", "area/README.md"],
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
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 14 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra Phase 14 top-level package")
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
            "missing Phase 14 requirement evidence")
    return files


def audit_studies(directory):
    result = {}
    for sweep in SWEEPS:
        study = read(directory / sweep / "study.json")
        require(study.get("sweep") == sweep, f"{sweep} study names a different sweep")
        result[sweep] = audit_sweep(study)
    return result


def audit_analysis(directory):
    analysis = (directory / "analysis.md").read_text()
    for phrase in ("## 1. Memory latency", "## 2. L1 cache geometry",
                   "## 3. Core scaling", "## 4. Compute placement",
                   "## 5. Research questions not measured"):
        require(phrase in analysis, f"analysis is missing section: {phrase}")
    for phrase in ("9.34×", "1.30×", "0.59×"):
        require(phrase in analysis, f"analysis is missing a measured result: {phrase}")
    return {"sections": 5}


def audit_area(directory):
    area = read(directory / "area.json")
    require(area.get("schema") == "aster.phase14.area.v1", "area schema is wrong")
    configs = area["configs"]
    require(set(configs) == {"h1", "baseline", "l2"}, "area configurations differ")
    for name, config in configs.items():
        require(config["wns_ns"] > 0, f"{name} has failing timing")
        require(config["luts"] > 0 and config["bram"] > 0, f"{name} area is incomplete")
    require(configs["h1"]["luts"] < configs["baseline"]["luts"] < configs["l2"]["luts"],
            "area ordering is inconsistent")
    readme = (directory / "README.md").read_text()
    for phrase in ("+5 765", "+4 330", "36.7 MHz"):
        require(phrase in readme, f"area analysis is missing: {phrase}")
    return {"configs": len(configs)}


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
        require(typed_equal(source, source_state()), "current source differs from the frozen v1.0 snapshot")
    return {
        "source_revision": source["revision"],
        "studies": audit_studies(directory / "studies"),
        "area": audit_area(directory / "area"),
        "analysis": audit_analysis(directory),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 14 manifest")
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
    print("PASS: all Phase 14 studies, analysis and frozen source")


if __name__ == "__main__":
    main()
