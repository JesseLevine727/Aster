#!/usr/bin/env python3
"""Read-only outer audit for the Phase 15 SKY130 closeout.

Inventories the bundle, re-checks the signoff metrics, the DRC/LVS/antenna and
timing reports, the SDF gate-level log and the clean-check log, and binds the
manifest to the committed source snapshot.

    python3 scripts/audit_phase15.py docs/results/phase15/closeout-<rev> --current
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

SCHEMA = "aster.phase15.closeout.v1"
SOURCE_SCHEMA = "aster.phase15.source.v1"
TOP_DIRS = {"spec", "physical", "verification", "source"}
TOP_FILES = {"README.md", "analysis.md"}
REQUIREMENTS = {
    "01-contract": ["spec/phase15.md"],
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
    require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 15 closeout directory")
    names = {path.name for path in directory.iterdir()}
    require(TOP_DIRS | TOP_FILES <= names <= TOP_DIRS | TOP_FILES | {"manifest.json"},
            "missing or extra Phase 15 top-level package")
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
            "missing Phase 15 requirement evidence")
    return files


def audit_metrics(directory):
    values = {}
    with (directory / "metrics.csv").open() as stream:
        for key, value in csv.reader(stream):
            values[key] = value
    setup = float(values["timing__setup__ws"])
    hold = float(values["timing__hold__ws"])
    require(setup > 0, f"worst setup slack is negative ({setup} ns)")
    require(hold > 0, f"worst hold slack is negative ({hold} ns)")
    require(float(values["timing__setup__ws__corner:nom_tt_025C_1v80"]) > 0,
            "nom_tt setup constraint is not met")
    require(int(values["design__violations"]) == 0, "design violations are non-zero")
    require(int(values["flow__errors__count"]) == 0, "flow errors are non-zero")
    return {
        "setup_ws_worst": setup,
        "hold_ws_worst": hold,
        "magic_drc": int(values["magic__drc_error__count"]),
        "klayout_drc": int(values["klayout__drc_error__count"]),
        "lvs_errors": int(values["design__lvs_error__count"]),
        "antenna_nets": int(values["antenna__violating__nets"]),
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
    wns = (directory / "timing" / "max_ss_100C_1v60" / "wns.max.rpt").read_text()
    match = re.search(r"max_ss_100C_1v60:\s*([-+]?\d+\.\d+)", wns)
    require(match, "worst-corner WNS report is empty")
    # "Worst Negative Slack" is 0.0 when the corner has no violating endpoints.
    require(float(match.group(1)) >= 0, "worst-corner WNS report shows negative slack")
    return {"worst_corner_wns": float(match.group(1))}


def audit_verification(directory):
    gl = (directory / "gate-level-sim.log").read_text()
    require('reproduced "Hello from Aster' in gl, "gate-level simulation did not reproduce the oracle")
    require("PASS:" in gl, "gate-level simulation has no PASS line")
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
        require(typed_equal(source, source_state()), "current source differs from the Phase 15 snapshot")
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
            "incomplete Phase 15 manifest")
    require(typed_equal(manifest["requirements"], REQUIREMENTS), "manifest requirements differ")
    require(typed_equal(manifest["files"], files), "manifest file inventory differs from disk")
    summary = evaluate(directory, current=current)
    require(typed_equal(manifest["summary"], summary), "manifest summary differs from re-evaluation")
    require(manifest["revision"] == summary["source_revision"], "manifest revision differs from source snapshot")
    return summary


def contract_gates(directory):
    """Evaluate every Phase 15 contract gate from the retained evidence (P17-C)."""
    physical = directory / "physical"
    metrics = asic_contract.load_metrics(physical / "metrics.csv")
    check = (directory / "verification" / "make-check.log").read_text()
    gl = (directory / "verification" / "gate-level-sim.log").read_text()
    unmatched = len(re.findall(r"Unable to match ModPath", gl))
    gates = [
        asic_contract.make_check_gate(check),
        asic_contract.synthesis_gate(metrics),
        asic_contract.drc_lvs_gate(metrics),
        asic_contract.antenna_gate(metrics),
        # The Phase 15 contract requires setup at nom_tt and hold at nom_ss.
        asic_contract.slack_gate("setup slack >= 0 at nom_tt", metrics, "setup",
                                 ("nom_tt_025C_1v80",)),
        asic_contract.slack_gate("hold slack >= 0 at nom_ss", metrics, "hold",
                                 ("nom_ss_100C_1v60",)),
        asic_contract.gate("gate-level simulation with back-annotated SDF reproduces the oracle",
                           'reproduced "Hello from Aster' in gl and "Annotating SDF" in gl
                           and unmatched == 0,
                           {"oracle_reproduced": 'reproduced "Hello from Aster' in gl,
                            "sdf_annotation_logged": "Annotating SDF" in gl,
                            "unmatched_sdf_paths": unmatched}),
        asic_contract.artifacts_gate(read(physical / "artifacts.json")),
    ]
    info = {
        "all_corner_setup": asic_contract.slack_gate("", metrics, "setup", asic_contract.CORNERS)["evidence"],
        "all_corner_hold": asic_contract.slack_gate("", metrics, "hold", asic_contract.CORNERS)["evidence"],
        "route_drc_errors": asic_contract.count(metrics, "route__drc_errors"),
        "electrical_violations_not_gated_by_this_contract": asic_contract.electrical(metrics),
        "power_total_is_corner": "max_ff_n40C_1v95",
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
    print("PASS: Phase 15 bundle integrity (inventory, manifest, source snapshot)")
    gates, info = contract_gates(args.directory)
    complete, text = asic_contract.report("Phase 15 contract gates:", gates, info)
    print(text)
    if not complete:
        raise SystemExit("FAIL: Phase 15 is INCOMPLETE: its contract gates are not all met")
    print("PASS: Phase 15 contract complete")


if __name__ == "__main__":
    main()
