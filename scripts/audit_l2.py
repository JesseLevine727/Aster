#!/usr/bin/env python3
"""Read-only outer audit for the v1.1 shared-L2 closeout.

Inventories the bundle, re-validates both retained studies against their
independent oracle, checks the analysis and the clean-check log, and binds the
manifest to the committed source snapshot.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

try:
    from .dse_study import audit_sweep
    from .pynq_handoff import validate_handoff
except ImportError:
    from dse_study import audit_sweep
    from pynq_handoff import validate_handoff


SCHEMA = "aster.v1.1.closeout.v1"
SOURCE_SCHEMA = "aster.v1.1.source.v1"
REVISION = "bb881fc8885163d7b1e5ee835b24e1ec55b44ac3"
TOP_DIRS = {"spec", "studies", "fpga", "physical", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
SWEEPS = ["l2-cache", "l2-size"]
REQUIREMENTS = {
    "01-contract": ["spec/l2.md"],
    "02-l2-cache-crossover": ["studies/l2-cache/study.json"],
    "03-l2-size": ["studies/l2-size/study.json"],
    "04-analysis": ["analysis.md"],
    "05-frozen-clean-check": ["verification/make-check.log"],
    "06-frozen-source": ["source/source-state.json"],
    "07-routed-fpga-overlay": [
        "fpga/aster_linux.bit", "fpga/aster_linux.hwh", "fpga/timing_summary.rpt",
        "fpga/drc.rpt", "fpga/methodology.rpt", "fpga/route_status.rpt",
        "fpga/utilization_routed.rpt",
    ],
    "08-physical-acceptance": ["physical/physical.json"],
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
    require(directory.is_dir() and not directory.is_symlink(), "invalid v1.1 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra v1.1 top-level package")
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
            "missing v1.1 requirement evidence")
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
    for phrase in ("## The L2 is a latency-hiding structure", "## L2 size matters",
                   "## Answers to the research questions"):
        require(phrase in analysis, f"analysis is missing section: {phrase}")
    for phrase in ("0.94×", "1.98×", "1.79×"):
        require(phrase in analysis, f"analysis is missing a measured result: {phrase}")
    return {"sections": 3}


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
    require(not re.search(r"\|\s*\S+\s*\|\s*(?:Error|Critical)\s*\|", methodology),
            "methodology error/critical findings")
    require(re.search(r"# of nets with routing errors.*:\s+0\s+:?\s*$", route, re.MULTILINE),
            "routing errors present")
    routed = (directory / "utilization_routed.rpt").read_text()
    require("Slice LUTs" in routed and "DSPs" in routed, "routed utilization report incomplete")
    return {"bridge_version": hex(handoff["bridge_version"])}


def audit_physical(directory):
    report = read(directory / "physical.json")
    require(report.get("schema") == "aster.v1.1.physical-mem.v1" and report["status"] == "complete",
            "physical package is incomplete")
    require(report["source_revision"] == REVISION and abs(report["clock_mhz"] - 31.25) < 1e-6
            and report["programmed"] is True, "physical identity/revision/clock/programming differs")
    require(report["name"] == "reduce_parallel", "physical workload differs")
    require(len(report["boots"]) == 2, "physical package did not run two warm boots")
    for boot in report["boots"]:
        require(boot["checksum"] == "0x5c808000", "physical checksum does not match the oracle")
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
    counts = re.findall(r"(?m)^Ran (\d+) tests in ", log)
    require(counts, "make-check.log has no host test summary")
    return {"host_tests_last": int(counts[-1])}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    require(source["revision"] == REVISION, "closeout source revision is wrong")
    if current:
        require(typed_equal(source, source_state()), "current source differs from the v1.1 snapshot")
    return {
        "source_revision": source["revision"],
        "studies": audit_studies(directory / "studies"),
        "analysis": audit_analysis(directory),
        "fpga": audit_fpga(directory / "fpga"),
        "physical": audit_physical(directory / "physical"),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete v1.1 manifest")
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
    print("PASS: v1.1 shared-L2 studies, analysis and frozen source")


if __name__ == "__main__":
    main()
