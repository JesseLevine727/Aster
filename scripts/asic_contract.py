"""Evidence-based contract gates for the SKY130 closeout audits (Phase 17, P17-C).

The Phase 15/16 audits originally checked some gates with substring matches that
could not fail and exempted failing corners. These helpers evaluate each
contract gate from the retained reports and metrics, so a failed gate is
reported as a failure instead of a documented residual.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

CORNERS = tuple(f"{rc}_{pvt}" for rc in ("nom", "min", "max")
                for pvt in ("tt_025C_1v80", "ss_100C_1v60", "ff_n40C_1v95"))
# Area of one sky130_sram_2kbyte_1rw1r_32x512_8 macro (liberty `area`), in um^2.
SRAM_2KB_AREA_UM2 = 284538.474


def load_metrics(path: Path) -> dict[str, str]:
    with path.open() as stream:
        return {key: value for key, value in csv.reader(stream)}


def gate(name: str, passed: bool, evidence: object) -> dict[str, object]:
    return {"gate": name, "passed": bool(passed), "evidence": evidence}


def slack_gate(name: str, metrics: dict[str, str], kind: str, corners) -> dict[str, object]:
    slacks = {corner: float(metrics[f"timing__{kind}__ws__corner:{corner}"]) for corner in corners}
    failing = {corner: round(value, 3) for corner, value in slacks.items() if value < 0}
    worst = min(slacks, key=slacks.get)
    return gate(name, not failing,
                {"failing_corners": failing} if failing else
                {"worst_corner": worst, "worst_slack_ns": round(slacks[worst], 3)})


def count(metrics: dict[str, str], key: str) -> int:
    return int(float(metrics[key]))


def drc_lvs_gate(metrics: dict[str, str]) -> dict[str, object]:
    magic = count(metrics, "magic__drc_error__count")
    klayout = count(metrics, "klayout__drc_error__count")
    lvs = count(metrics, "design__lvs_error__count")
    return gate("zero Magic/KLayout DRC and a clean Netgen LVS",
                magic == 0 and klayout == 0 and lvs == 0,
                {"magic_drc": magic, "klayout_drc": klayout, "lvs_errors": lvs})


def antenna_gate(metrics: dict[str, str]) -> dict[str, object]:
    nets = count(metrics, "antenna__violating__nets")
    pins = count(metrics, "antenna__violating__pins")
    return gate("antenna checks pass", nets == 0 and pins == 0,
                {"violating_nets": nets, "violating_pins": pins})


def synthesis_gate(metrics: dict[str, str]) -> dict[str, object]:
    values = {key: count(metrics, key) for key in (
        "synthesis__check_error__count", "design__inferred_latch__count", "design__lint_error__count")}
    return gate("Yosys synthesis with no unmapped cells, latches, or lint errors",
                all(value == 0 for value in values.values()), values)


def make_check_gate(log: str) -> dict[str, object]:
    fails = len(re.findall(r"(?m)^FAIL:", log))
    errors = len(re.findall(r"(?m)make(\[\d+\])?: \*\*\* .*Error", log))
    frozen = "PASS: 23 frozen v1.0 interfaces match the RTL" in log
    return gate("`make check` green and frozen v1.0 interfaces unchanged",
                fails == 0 and errors == 0 and frozen,
                {"fail_lines": fails, "make_errors": errors, "freeze_interfaces": frozen,
                 "pass_lines": len(re.findall(r"(?m)^PASS:", log))})


def artifacts_gate(artifacts: dict) -> dict[str, object]:
    required = ("gds", "def", "spef", "sdf", "nl")
    missing = [name for name in required if name not in artifacts]
    unbound = [name for name, record in artifacts.items()
               if not (record.get("bytes", 0) > 0 and len(record.get("sha256", "")) == 64)]
    return gate("GDS, DEF, SPEF, SDF and netlist are hash-bound", not missing and not unbound,
                {"missing": missing, "unbound": unbound})


def electrical(metrics: dict[str, str]) -> dict[str, int]:
    return {kind: count(metrics, f"design__max_{kind}_violation__count")
            for kind in ("slew", "cap", "fanout")}


def macros_from_area(metrics: dict[str, str]) -> int:
    return round(float(metrics["design__instance__area__macros"]) / SRAM_2KB_AREA_UM2)


def report(title: str, gates: list[dict], info: dict) -> tuple[bool, str]:
    lines = [title]
    for item in gates:
        lines.append(f"  [{'PASS' if item['passed'] else 'FAIL'}] {item['gate']}: {item['evidence']}")
    for key, value in info.items():
        lines.append(f"  [info] {key}: {value}")
    failed = [item for item in gates if not item["passed"]]
    lines.append(f"{len(gates) - len(failed)}/{len(gates)} contract gates pass")
    return not failed, "\n".join(lines)
