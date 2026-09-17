#!/usr/bin/env python3
"""Read-only outer audit for the Phase 12.6 interrupt closeout.

Inventories the bundle, re-checks the retained simulation study, the routed FPGA
reports, the physical records and the full make-check log, and binds the
manifest to the committed source snapshot.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

try:
    from .interrupt_study import audit_study, parse_unit
    from .pynq_handoff import validate_handoff
except ImportError:
    from interrupt_study import audit_study, parse_unit
    from pynq_handoff import validate_handoff


SCHEMA = "aster.phase12.6.closeout.v1"
SOURCE_SCHEMA = "aster.phase12.6.source.v1"
REVISION = "c24d2bfec2c5ec878ffd528601851b927fc38636"
TOP = {"spec", "input", "simulation", "fpga", "physical", "source", "verification"}
REQUIREMENTS = {
    "01-contract-and-firmware": ["spec/interrupts.md", "input/timer_interrupt.c", "input/timer_interrupt.hex"],
    "02-unit-and-firmware-scoreboards": ["verification/make-check.log"],
    "03-simulation-study": ["simulation/study.json"],
    "04-routed-fpga-overlay": [
        "fpga/aster_linux.bit", "fpga/aster_linux.hwh", "fpga/timing_summary.rpt",
        "fpga/drc.rpt", "fpga/methodology.rpt", "fpga/route_status.rpt",
        "fpga/utilization_routed.rpt",
    ],
    "05-physical-pynq-acceptance": ["physical/physical.json"],
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
         "verification/", "fpga/"], text=True).strip()
    require(revision, "implementation source revision is unavailable")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files, "sha256": fingerprint}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 12.6 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra Phase 12.6 top-level package")
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
            "missing Phase 12.6 requirement evidence")
    return files


def audit_input(directory):
    source = (directory / "timer_interrupt.c").read_text()
    require("ASTER_IRQ_PENDING" in source and "TIMER IRQ PASS" in source,
            "firmware source is not the interrupt test")
    words = [line for line in (directory / "timer_interrupt.hex").read_text().splitlines() if line]
    require(len(words) == 16384 and all(re.fullmatch(r"[0-9a-fA-F]{8}", word) for word in words),
            "interrupt ROM image is not a canonical 16384-word firmware")
    return {"firmware_words": len(words)}


def audit_simulation(directory):
    return audit_study(read(directory / "study.json"))


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
    require(report.get("schema") == "aster.phase12.6.physical-mem.v1" and report["status"] == "complete",
            "physical package is incomplete")
    require(report["source_revision"] == REVISION and abs(report["clock_mhz"] - 31.25) < 1e-6
            and report["programmed"] is True, "physical identity/revision/clock/programming differs")
    require(len(report["boots"]) == 2, "physical package did not run two warm boots")
    latencies = []
    for boot in report["boots"]:
        require("TIMER IRQ PASS" in boot["uart_output"], "physical boot did not report TIMER IRQ PASS")
        latency = boot["interrupt_latency_cycles"]
        require(0 < latency < 8192, "physical interrupt latency is out of bounds")
        require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                "was not running before STOP")
        require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                and boot["after_stop"]["fifo_count"] == 0, "did not stop cleanly")
        latencies.append(latency)
    require(all(latency == latencies[0] for latency in latencies), "physical latency is not reproducible")
    return {"boots": len(report["boots"]), "interrupt_latency_cycles": latencies[0]}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    for phrase in ("PASS: interrupt controller edge capture, W1C, RAISE, per-hart masks, reset",
                   "PASS: coherent SoC interrupt harts=2 caches=1"):
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
        "simulation": audit_simulation(directory / "simulation"),
        "fpga": audit_fpga(directory / "fpga"),
        "physical": audit_physical(directory / "physical"),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 12.6 manifest")
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
    print("PASS: all Phase 12.6 requirements, independent study/physical audits and clean source")


if __name__ == "__main__":
    main()
