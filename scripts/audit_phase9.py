#!/usr/bin/env python3
"""Read-only outer audit for the Phase 9 NPU closeout.

This audit never connects to the board and never executes evidence commands.
It inventories every copied artifact, reruns the independent AsterBench v7 and
physical-package auditors, validates both routed HWH handoffs, checks the
retained regression logs, and binds the manifest to the current implementation
source snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .asterbench_v7_results import audit_study
    from .pynq_handoff import validate_handoff
    from .audit_pynq_npu import audit as audit_physical
except ImportError:
    from asterbench_v7_results import audit_study
    from pynq_handoff import validate_handoff
    from audit_pynq_npu import audit as audit_physical


SCHEMA = "aster.phase9.closeout.v1"
TOP = {"spec", "simulation", "fpga", "physical", "source", "verification"}
REQUIREMENTS = {
    "01-contract-and-architecture": ["spec/phase9.md"],
    "02-verified-RTL-and-register-boundary": ["verification/make-check.log"],
    "03-real-core-RAM-backed-runtime-and-coverage": ["verification/npu-runtime-matrix.log", "verification/npu-stop.log"],
    "04-AsterBench-v7-independent-study-and-repeats": ["simulation/asterbench-v7/study.json", "simulation/asterbench-v7/mutation.log"],
    "05-routed-reset-HWH-resource-and-bitstream-gates": [
        "fpga/cache-on/aster_linux.bit", "fpga/cache-on/aster_linux.hwh", "fpga/cache-on/timing_summary.rpt",
        "fpga/cache-off/aster_linux.bit", "fpga/cache-off/aster_linux.hwh", "fpga/cache-off/timing_summary.rpt",
    ],
    "06-PYNQ-Linux-physical-acceptance": ["physical/cache-on/physical.json", "physical/cache-off/physical.json"],
    "07-preserved-Phase1-through-8-and-clean-source": ["source/source-state.json", "verification/make-check.log"],
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
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return {"schema": "aster.phase9.source.v1", "revision": revision, "files": files, "sha256": fingerprint}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 9 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra Phase 9 top-level package")
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
            "missing Phase 9 requirement evidence")
    return files


def audit_fpga(directory, cache):
    folder = directory / ("cache-on" if cache else "cache-off")
    bitstream = folder / "aster_linux.bit"
    hwh = folder / "aster_linux.hwh"
    require(bitstream.is_file() and hwh.is_file(), "missing routed NPU overlay pair")
    handoff = validate_handoff(hwh, 2, expected_coherent=True, expected_cache=cache,
                               expected_dma=True, expected_npu=True)
    timing = (folder / "timing_summary.rpt").read_text()
    route = (folder / "route_status.rpt").read_text()
    drc = (folder / "drc.rpt").read_text()
    methodology = (folder / "methodology.rpt").read_text()
    reset = (folder / "reset_sim" / "xsim.log").read_text()
    require("Design State : Routed" in timing and "All user specified timing constraints are met." in timing and
            "31.250" in timing and re.search(r"\n\s+[0-9]+\.[0-9]+\s+0\.000\s+0\s+\d+\s+[0-9]+\.[0-9]+\s+0\.000", timing),
            "timing signoff is incomplete")
    require("fully routed nets" in route and "routing errors" in route and
            re.search(r"# of nets with routing errors.*:\s+0\s+:?\s*$", route, re.MULTILINE),
            "route signoff is incomplete")
    require("Design State : Fully Routed" in drc and "Checks found:" in drc and
            "Design State : Fully Routed" in methodology and "Checks found: 0" in methodology,
            "DRC or methodology signoff is incomplete")
    require("PASS: generated PYNQ reset netlist, five assert/release scenarios" in reset,
            "generated reset-netlist proof is missing")
    return {
        "cache": cache,
        "handoff": handoff,
        "bitstream_sha256": sha(bitstream),
        "hwh_sha256": sha(hwh),
        "timing": list(re.search(r"\n\s+([0-9]+\.[0-9]+)\s+0\.000\s+0\s+\d+\s+([0-9]+\.[0-9]+)\s+0\.000\s+0\s+\d+\s+([0-9]+\.[0-9]+)", timing).groups()),
    }


def audit_logs(directory):
    check = (directory / "verification" / "make-check.log").read_text()
    matrix = (directory / "verification" / "npu-runtime-matrix.log").read_text()
    stop = (directory / "verification" / "npu-stop.log").read_text()
    require(not re.search(r"(?m)^FAIL:", check + matrix + stop), "a retained verification log contains FAIL")
    count = re.findall(r"(?m)^Ran (\d+) tests in [\d.]+s$", check)
    require(count == ["216"], "full host test count is not retained")
    require(len(re.findall(r"^PASS: Phase 9 actual-core NPU runtime ", matrix, re.MULTILINE)) == 16,
            "actual-core Phase 9 matrix is incomplete")
    require(stop.startswith("PASS: Phase 9 actual-core NPU runtime "), "NPU STOP evidence is missing")
    for phrase in (
        "PASS: INT8 PE reset", "PASS: INT8 4x4 array", "PASS: RAM-backed INT8 GEMM engine",
        "PASS: NPU ABI/control registers", "PASS: Phase 9 actual-core NPU runtime",
        '"status": "PASS"',
    ):
        require(phrase in check, f"missing verification evidence: {phrase}")
    return {"host_tests": int(count[0]), "npu_runtime_matrix": 16, "npu_stop": True}


def evaluate(directory, *, current=False):
    study = audit_study(directory / "simulation" / "asterbench-v7" / "study.json")
    mutation = (directory / "simulation" / "asterbench-v7" / "mutation.log").read_text()
    require('"status": "PASS"' in mutation and "output_nibble" in mutation, "AsterBench mutation audit missing")
    physical = {}
    for cache in (False, True):
        name = "cache-on" if cache else "cache-off"
        physical[name] = audit_physical(directory / "physical" / name / "physical.json",
                                         directory / "fpga" / name / "aster_linux.bit", cache=cache)
    fpga = {"cache-on": audit_fpga(directory / "fpga", True), "cache-off": audit_fpga(directory / "fpga", False)}
    logs = audit_logs(directory)
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == "aster.phase9.source.v1" and typed_equal(source, source_state()),
            "closeout source snapshot differs from the committed implementation")
    if current:
        require(typed_equal(source, source_state()), "current source differs from closeout source snapshot")
    return {
        "source_revision": source["revision"],
        "source_sha256": source["sha256"],
        "verification": logs,
        "asterbench": {
            "primary_captures": study["summary"]["primary_captures"],
            "fresh_repeats": study["summary"]["fresh_repeats"],
            "records": study["summary"]["records"],
            "study_sha256": sha(directory / "simulation" / "asterbench-v7" / "study.json"),
            "ratio_min": min(row["end_to_end_scalar_over_npu"] for row in study["summary"]["rows"]),
            "ratio_max": max(row["end_to_end_scalar_over_npu"] for row in study["summary"]["rows"]),
        },
        "physical": {name: {"cache": report["cache"], "boots": len(report["boots"]),
                             "clock_mhz": report["clock_mhz"], "status": report["status"]}
                     for name, report in physical.items()},
        "fpga": fpga,
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete", "incomplete Phase 9 manifest")
    require(typed_equal(manifest["requirements"], REQUIREMENTS) and typed_equal(manifest["files"], files),
            "changed or omitted Phase 9 requirement/artifact")
    actual = evaluate(directory, current=current)
    require(typed_equal(manifest["summary"], actual), "invented or stale Phase 9 closeout summary")
    return manifest


def manifest(directory):
    path = directory / "manifest.json"
    require(not path.exists(), "closeout manifest already exists; evidence is never overwritten")
    source_path = directory / "source" / "source-state.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = source_state()
    if source_path.exists():
        require(read(source_path) == snapshot, "source snapshot already exists and differs")
    else:
        source_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    files = inventory(directory)
    summary = evaluate(directory, current=True)
    path.open("x").write(json.dumps({"schema": SCHEMA, "status": "complete", "files": files,
                                      "requirements": REQUIREMENTS, "summary": summary},
                                     indent=2, sort_keys=True) + "\n")
    return audit(directory, current=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("audit", "manifest"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    try:
        result = manifest(args.directory) if args.action == "manifest" else audit(args.directory, current=args.current)
        print(json.dumps(result["summary"], indent=2, sort_keys=True))
        print("PASS: all Phase 9 requirements, immutable evidence, independent audits and physical STOPPED packages")
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        parser.exit(1, f"FAIL: {error}\n")
