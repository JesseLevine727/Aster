#!/usr/bin/env python3
"""Read-only outer audit for the Phase 16 SKY130 closeout.

Inventories the bundle, re-checks the signoff metrics, the DRC/LVS/antenna and
timing reports, the SDF gate-level log and the clean-check log, and binds the
manifest to the committed source snapshot.

    python3 scripts/audit_phase16.py docs/results/phase16/closeout-<rev> --current
"""
import asic_contract
import argparse
import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = "aster.phase16.closeout.v1"
SOURCE_SCHEMA = "aster.phase16.source.v1"
TOP_DIRS = {"spec", "physical", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
REQUIREMENTS = {
    "01-contract": ["spec/phase16.md"],
    "02-signoff-metrics": ["physical/metrics.csv", "physical/artifacts.json"],
    "03-drc-lvs-antenna": ["physical/drc.magic.rpt", "physical/drc.klayout.json",
                           "physical/lvs.netgen.rpt", "physical/antenna_summary.rpt"],
    "04-timing": ["physical/timing/max_ss_100C_1v60/wns.max.rpt"],
    "05-gate-level": ["verification/gate-level-sim.log"],
    "06-frozen-clean-check": ["verification/make-check.log"],
    "07-frozen-source": ["source/source-state.json"],
    "08-analysis": ["analysis.md"],
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


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def source_state():
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], text=False
    ).decode().split("\0")
    files = {}
    for name in sorted(set(names)):
        if name == "Makefile" or name.startswith(("rtl/", "software/", "vendor/", "scripts/",
                                                  "verification/", "fpga/")):
            files[name] = sha(Path(name))
    revision = git("log", "-1", "--format=%H", "--", "Makefile", "rtl/", "software/",
                   "vendor/", "verification/", "fpga/")
    require(revision, "implementation source revision is unavailable")
    return {"schema": SOURCE_SCHEMA, "revision": revision, "files": files}


def read(path):
    return json.loads(path.read_text())


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 16 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra Phase 16 top-level package")
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
            "missing Phase 16 requirement evidence")
    return files


def audit_metrics(directory):
    values = {}
    with (directory / "metrics.csv").open() as stream:
        for key, value in csv.reader(stream):
            values[key] = value
    # Phase 16 signoff gate: setup closes at the nominal (TT) and slow (SS)
    # corners; the extreme worst-RC corner (max_ss) is recorded as a residual.
    setup_tt = float(values["timing__setup__ws__corner:nom_tt_025C_1v80"])
    setup_ss = float(values["timing__setup__ws__corner:nom_ss_100C_1v60"])
    setup_max_ss = float(values["timing__setup__ws__corner:max_ss_100C_1v60"])
    hold = float(values["timing__hold__ws"])
    require(setup_tt > 0, f"nom_tt setup constraint is not met ({setup_tt} ns)")
    require(setup_ss > 0, f"nom_ss setup constraint is not met ({setup_ss} ns)")
    require(int(values["magic__drc_error__count"]) == 0, "Magic DRC is not clean")
    require(int(values["klayout__drc_error__count"]) == 0, "KLayout DRC is not clean")
    require(int(values["antenna__violating__nets"]) == 0, "antenna is not clean")
    require(int(values["design__violations"]) == 0, "design violations are non-zero")
    require(int(values["flow__errors__count"]) == 0, "flow errors are non-zero")
    return {
        "setup_ws_nom_tt": setup_tt,
        "setup_ws_nom_ss": setup_ss,
        "setup_ws_max_ss": setup_max_ss,
        "hold_ws_worst": hold,
        "magic_drc": int(values["magic__drc_error__count"]),
        "klayout_drc": int(values["klayout__drc_error__count"]),
        "lvs_errors": int(values["design__lvs_error__count"]),
        "antenna_nets": int(values["antenna__violating__nets"]),
        "route_drc": int(values["route__drc_errors"]),
        "design_violations": int(values["design__violations"]),
    }


def audit_physical(directory):
    # Integrity only. DRC, LVS and antenna are contract gates evaluated from
    # counts in contract_gates(); the former substring checks could not fail.
    for name in ("drc.magic.rpt", "lvs.netgen.rpt", "antenna_summary.rpt"):
        require((directory / name).is_file(), f"{name} is missing")
    klayout = read(directory / "drc.klayout.json")
    require(not klayout or all(not value for value in klayout.values()),
            "KLayout DRC report is not clean")
    artifacts = read(directory / "artifacts.json")
    require({"gds", "def", "sdf", "nl"} <= set(artifacts), "artifacts.json is missing signoff files")
    for name, record in artifacts.items():
        require(record["bytes"] > 0 and len(record["sha256"]) == 64,
                f"artifact {name} is not hash-bound")
    return {"artifacts": len(artifacts)}


def audit_timing(directory):
    # Signoff corners: nominal (TT) and slow (SS) must close. The extreme
    # worst-RC corner (max_ss) is recorded as the documented residual.
    wns_ss = (directory / "timing" / "nom_ss_100C_1v60" / "wns.max.rpt").read_text()
    match = re.search(r"nom_ss_100C_1v60:\s*([-+]?\d+\.\d+)", wns_ss)
    require(match, "nom_ss WNS report is empty")
    require(float(match.group(1)) >= 0, "nom_ss WNS report shows negative slack")
    wns_max = (directory / "timing" / "max_ss_100C_1v60" / "wns.max.rpt").read_text()
    match_max = re.search(r"max_ss_100C_1v60:\s*([-+]?\d+\.\d+)", wns_max)
    return {"nom_ss_wns": float(match.group(1)),
            "max_ss_wns": float(match_max.group(1)) if match_max else None}


def audit_verification(directory):
    check = (directory / "make-check.log").read_text()
    require((directory / "gate-level-sim.log").is_file(), "gate-level-sim.log is missing")
    check = (directory / "make-check.log").read_text()
    require(not re.search(r"(?m)^FAIL:", check), "make-check.log contains FAIL")
    require(not re.search(r"(?m)make(\[\d+\])?: \*\*\* .*Error", check), "make-check.log contains a make error")
    require("PASS: 23 frozen v1.0 interfaces match the RTL" in check,
            "frozen interface guard did not pass")
    counts = re.findall(r"(?m)^PASS:", check)
    require(len(counts) == 201, f"expected 201 check passes, found {len(counts)}")
    return {"check_passes": len(counts)}


def evaluate(directory, *, current=False):
    source = read(directory / "source" / "source-state.json")
    require(source.get("schema") == SOURCE_SCHEMA, "closeout source snapshot has the wrong schema")
    if current:
        require(typed_equal(source, source_state()), "current source differs from the Phase 16 snapshot")
    return {
        "source_revision": source["revision"],
        "metrics": audit_metrics(directory / "physical"),
        "physical": audit_physical(directory / "physical"),
        "timing": audit_timing(directory / "physical"),
        "verification": audit_verification(directory / "verification"),
    }


def audit(directory, *, current=False):
    files = inventory(directory)
    manifest = read(directory / "manifest.json")
    require(set(manifest) == {"schema", "status", "revision", "run", "files", "requirements", "summary"} and
            manifest["schema"] == SCHEMA and manifest["status"] == "complete",
            "incomplete Phase 16 manifest")
    require(typed_equal(manifest["requirements"], REQUIREMENTS), "manifest requirements differ")
    require(typed_equal(manifest["files"], files), "manifest file inventory differs from disk")
    summary = evaluate(directory, current=current)
    require(typed_equal(manifest["summary"], summary), "manifest summary differs from re-evaluation")
    require(manifest["revision"] == summary["source_revision"], "manifest revision differs from source snapshot")
    return summary


# Phase 16 contract (docs/phase16.md "Verification and acceptance gates" and
# the frozen 64 KiB ROM + 64 KiB RAM memory decision).
TIER2_MANDATORY = ("reduce_parallel", "coremark", "fft", "conv2d_dot8", "conv2d_npu",
                   "streaming_ecg", "cifar_cnn", "phase11-infer")
FROZEN_MACROS = 64  # 64 KiB ROM + 64 KiB RAM in 2 KiB macros


def contract_gates(directory):
    """Evaluate every Phase 16 contract gate from the retained evidence (P17-C)."""
    physical = directory / "physical"
    metrics = asic_contract.load_metrics(physical / "metrics.csv")
    check = (directory / "verification" / "make-check.log").read_text()
    gl = (directory / "verification" / "gate-level-sim.log").read_text()
    records = re.findall(r"(?m)^ASTERBENCH,version=10,.*$", check)
    asic_records = [record for record in records if ",sync_memory=1," in record]
    passed_gl = {name for name in re.findall(r"(?m)^=== (\S+) ===$", gl)
                 if re.search(rf"(?s)=== {re.escape(name)} ===\s*PASS:", gl)}
    macros = asic_contract.macros_from_area(metrics)
    gates = [
        asic_contract.make_check_gate(check),
        asic_contract.synthesis_gate(metrics),
        asic_contract.drc_lvs_gate(metrics),
        asic_contract.antenna_gate(metrics),
        asic_contract.slack_gate("setup slack >= 0 at every RC corner", metrics, "setup",
                                 asic_contract.CORNERS),
        asic_contract.slack_gate("hold slack >= 0 at every RC corner", metrics, "hold",
                                 asic_contract.CORNERS),
        asic_contract.gate("Tier 1: AsterBench catalog at RTL on the ASIC configuration",
                           bool(asic_records),
                           {"v10_records": len(records), "sync_memory_1_records": len(asic_records)}),
        asic_contract.gate("Tier 2: SDF-annotated gate-level oracle for every mandatory workload",
                           set(TIER2_MANDATORY) <= passed_gl and "Annotating SDF" in gl,
                           {"passed": sorted(passed_gl),
                            "missing": sorted(set(TIER2_MANDATORY) - passed_gl),
                            "sdf_annotation_logged": "Annotating SDF" in gl}),
        asic_contract.gate("area, Fmax and power recorded",
                           all(key in metrics for key in ("design__instance__area", "power__total",
                                                          "timing__setup__ws")),
                           {"instance_area_um2": metrics.get("design__instance__area"),
                            "power_total_w": metrics.get("power__total")}),
        asic_contract.artifacts_gate(read(physical / "artifacts.json")),
        asic_contract.gate("frozen memory map: 64 KiB ROM + 64 KiB RAM",
                           macros >= FROZEN_MACROS,
                           {"sram_macros": macros, "required": FROZEN_MACROS}),
    ]
    info = {
        "route_drc_errors": asic_contract.count(metrics, "route__drc_errors"),
        "electrical_violations_not_gated_by_this_contract": asic_contract.electrical(metrics),
        "power_total_is_corner": "max_ff_n40C_1v95 (see docs/phase16.md Results)",
    }
    return gates, info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    try:
        audit(args.directory, current=args.current)
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}")
    print("PASS: Phase 16 bundle integrity (inventory, manifest, source snapshot)")
    gates, info = contract_gates(args.directory)
    complete, text = asic_contract.report("Phase 16 contract gates:", gates, info)
    print(text)
    if not complete:
        raise SystemExit("FAIL: Phase 16 is INCOMPLETE: its contract gates are not all met")
    print("PASS: Phase 16 contract complete")


if __name__ == "__main__":
    main()
