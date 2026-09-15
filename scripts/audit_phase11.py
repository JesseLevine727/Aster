#!/usr/bin/env python3
"""Read-only outer audit for the Phase 11 quantized-inference closeout.

Inventories the bundle, re-runs the independent integer reference and the v9
oracle on the retained study and physical records, recomputes the four-path
ratios, checks the routed FPGA reports and the full make-check log, and binds
the manifest to the committed source snapshot. It never touches the board.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .asterbench_v9 import load_model, validate_line, require
    from .phase11_reference import reference
    from .phase11_study import summarize, SCHEMA as STUDY_SCHEMA
    from .pynq_handoff import validate_handoff
except ImportError:
    from asterbench_v9 import load_model, validate_line, require
    from phase11_reference import reference
    from phase11_study import summarize, SCHEMA as STUDY_SCHEMA
    from pynq_handoff import validate_handoff


SCHEMA = "aster.phase11.closeout.v1"
SOURCE_SCHEMA = "aster.phase11.source.v1"
REVISION = "ff56683277fd648dd22185ce134960cb586ff22f"
ROOT = Path(__file__).resolve().parents[1]
METHODS = ("scalar", "multicore", "dot8", "npu")
TOP = {"spec", "model", "simulation", "fpga", "physical", "source", "verification"}
REQUIREMENTS = {
    "01-contract-and-model": ["spec/phase11.md", "model/model.json", "model/reference.json", "model/export.json"],
    "02-full-regressions-and-host-tests": ["verification/make-check.log"],
    "03-four-path-study-and-fresh-repeats": ["simulation/study.json"],
    "04-routed-fpga-overlay": [
        "fpga/aster_linux.bit", "fpga/aster_linux.hwh", "fpga/timing_summary.rpt",
        "fpga/drc.rpt", "fpga/methodology.rpt", "fpga/route_status.rpt",
        "fpga/utilization_routed.rpt",
    ],
    "05-physical-pynq-acceptance": [f"physical/{name}/physical.json" for name in METHODS],
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
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 11 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP | {"README.md"} <= names <= TOP | {"README.md", "manifest.json"},
            "missing or extra Phase 11 top-level package")
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
            "missing Phase 11 requirement evidence")
    return files


def audit_model(directory):
    model = load_model(directory / "model.json")
    result = reference(directory / "model.json")
    require(result["model_hash"] == model["hash"], "reference model hash disagrees")
    manifest = read(directory / "export.json")
    require(manifest["model_hash"] == model["hash"], "export manifest model hash disagrees")
    for name, digest in manifest["headers"].items():
        require(sha(ROOT / "software" / "benchmarks" / name) == digest,
                f"exported header {name} differs from the manifest")
    return {"model_hash": model["hash"], "images": result["images"], "correct": result["correct"]}


def audit_study(directory, model):
    study = read(directory / "study.json")
    require(study.get("schema") == STUDY_SCHEMA, "study schema is not Phase 11")
    require(study["model_hash"] == model["hash"], "study model hash disagrees")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    require(study["repeats"], "study has no repeats")
    for repeat in study["repeats"]:
        require(set(repeat["methods"]) == set(METHODS), "repeat is missing a method")
        for method, records in repeat["methods"].items():
            for line in records:
                validate_line(line, model, method=method)
        require(summarize(repeat["methods"], model) == repeat["summary"], "retained study summary disagrees")
    require(study["summary"] == study["repeats"][0]["summary"], "top-level study summary disagrees")
    ratios = {m: study["summary"][m]["scalar_over_method"] for m in METHODS}
    return {"repeats": len(study["repeats"]), "methods": len(METHODS),
            "images": len(model["test"]["labels"]), "ratios": ratios}


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


def audit_physical(directory, model):
    packages = 0
    boots = 0
    ratios = {}
    means = {}
    for method in METHODS:
        report = read(directory / method / "physical.json")
        require(report.get("schema") == "aster.phase11.physical-mem.v1" and report["status"] == "complete",
                f"{method} physical package incomplete")
        require(report["method"] == method and report["model_hash"] == model["hash"],
                f"{method} physical identity differs")
        require(abs(report["clock_mhz"] - 31.25) < 1e-6 and report["programmed"] is True,
                f"{method} physical clock/programming differs")
        require(len(report["boots"]) == 2, f"{method} did not run two warm boots")
        cycles = []
        for boot in report["boots"]:
            records = [line for line in boot["uart_output"].splitlines(keepends=True)
                       if line.startswith("ASTERBENCH,")]
            require(len(records) == len(model["test"]["labels"]), f"{method} record count differs")
            parsed = [validate_line(line, model, method=method) for line in records]
            cycles.append(sum(p["h0_cycles"] for p in parsed) / len(parsed))
            require(boot["before_stop"]["control"] == 1 and boot["before_stop"]["stop_status"] == 0,
                    f"{method} was not running before STOP")
            require(boot["after_stop"]["control"] == 0 and boot["after_stop"]["stop_status"] == 1
                    and boot["after_stop"]["fifo_count"] == 0, f"{method} did not stop cleanly")
            boots += 1
        means[method] = cycles[0]
        packages += 1
    scalar = means["scalar"]
    for method in METHODS:
        ratios[method] = scalar / means[method]
    return {"packages": packages, "boots": boots, "ratios": ratios}


def audit_logs(directory):
    log = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", log), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log), "make-check.log contains a make error")
    for phrase in ("PASS: INT8 4x4 array", "PASS: Phase 9 actual-core NPU runtime",
                   "MNIST INFER PASS images=32 class_correct=32", "PASS: parallel jobs"):
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
    model = load_model(directory / "model" / "model.json")
    return {
        "source_revision": source["revision"], "source_sha256": source["sha256"],
        "model": audit_model(directory / "model"),
        "simulation": audit_study(directory / "simulation", model),
        "fpga": audit_fpga(directory / "fpga"),
        "physical": audit_physical(directory / "physical", model),
        "verification": audit_logs(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 11 manifest")
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
    print("PASS: all Phase 11 requirements, independent model/study/physical audits and clean source")


if __name__ == "__main__":
    main()
