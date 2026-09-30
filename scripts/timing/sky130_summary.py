#!/usr/bin/env python3
"""Summarize a SKY130 block run (Phase 18 timing baseline) from LibreLane metrics.

Reads <run>/final/metrics.json after OpenROAD.STAPostPNR and prints, per signoff
corner, the worst register-to-register setup and hold slack against the SDC
period, the period that setup slack implies (period - slack), and the max
slew/capacitance violation counts; then standard-cell area and count. Only
register-to-register paths set the implied period: an out-of-context block's
port budgets are arbitrary. The slow corner that the v2 SKY130 target is judged
at is max_ss_100C_1v60.

    sky130_summary.py asic/sky130/runs/p18-picorv32 [--json out.json]

The period is the run's own CLOCK_PERIOD (resolved.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORNERS = ("nom_tt_025C_1v80", "nom_ss_100C_1v60", "max_ss_100C_1v60", "nom_ff_n40C_1v95", "max_ff_n40C_1v95")
SLOW = "max_ss_100C_1v60"


def summarize(run: Path) -> dict:
    metrics = json.loads((run / "final" / "metrics.json").read_text())
    period = float(json.loads((run / "resolved.json").read_text())["CLOCK_PERIOD"])

    def corner(key: str, name: str):
        value = metrics.get(f"{key}__corner:{name}")
        if value is None:
            raise KeyError(f"{run}: metrics.json has no {key} for corner {name}; did STAPostPNR run?")
        return value

    corners = {}
    for name in CORNERS:
        setup = float(corner("timing__setup_r2r__ws", name))
        corners[name] = {
            "setup_r2r_ws_ns": round(setup, 4),
            "hold_r2r_ws_ns": round(float(corner("timing__hold_r2r__ws", name)), 4),
            "implied_period_ns": round(period - setup, 3),
            "implied_fmax_mhz": round(1000.0 / (period - setup), 1),
            "max_slew_violations": int(corner("design__max_slew_violation__count", name)),
            "max_cap_violations": int(corner("design__max_cap_violation__count", name)),
        }
    return {
        "run": run.name,
        "period_ns": period,
        "corners": corners,
        "stdcell_area_um2": round(float(metrics["design__instance__area__stdcell"]), 1),
        "stdcell_count": int(metrics["design__instance__count__stdcell"]),
        "sequential_cells": int(metrics["design__instance__count__class:sequential_cell"]),
        "utilization": round(float(metrics["design__instance__utilization"]), 4),
        "route_drc_errors": int(metrics["route__drc_errors"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--json", type=Path, help="also write the summary as JSON")
    args = parser.parse_args()
    try:
        summary = summarize(args.run)
    except (OSError, KeyError, ValueError) as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for name, c in summary["corners"].items():
        print(f"{name:18s} r2r setup {c['setup_r2r_ws_ns']:+8.3f} ns  hold {c['hold_r2r_ws_ns']:+7.3f} ns  "
              f"implied {c['implied_period_ns']:6.2f} ns ({c['implied_fmax_mhz']:6.1f} MHz)  "
              f"slew/cap violations {c['max_slew_violations']}/{c['max_cap_violations']}")
    slow = summary["corners"][SLOW]
    print(f"SUMMARY run={summary['run']} period_ns={summary['period_ns']} "
          f"slow_setup_r2r_ns={slow['setup_r2r_ws_ns']} slow_fmax_mhz={slow['implied_fmax_mhz']} "
          f"tt_setup_r2r_ns={summary['corners']['nom_tt_025C_1v80']['setup_r2r_ws_ns']} "
          f"stdcell_um2={summary['stdcell_area_um2']} cells={summary['stdcell_count']} "
          f"flops={summary['sequential_cells']} route_drc={summary['route_drc_errors']}")
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
