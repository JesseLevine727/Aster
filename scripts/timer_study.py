#!/usr/bin/env python3
"""Phase 12.5 machine-timer study: capture and audit.

Runs the timer unit scoreboard once and the coherent timer firmware several
fresh times, retains the raw outputs, and checks that the programmed interval
and the bounded match latency reproduce exactly.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase12.5.study.v1"
LATENCY_LIMIT = 512
UNIT_SIM = ROOT / "build" / "aster_timer_sim"
FIRMWARE_HEX = ROOT / "build" / "software" / "timer_interval.hex"
SOC_SIM = ROOT / "build" / "coherent_soc_h2_l11_sync0_wait0_w4_n16" / "aster_coherent_soc_sim"
UNIT_PASS = "PASS: timer free-run, compare match, clear/enable, byte merge, 64-bit wrap and reset"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()


def provenance():
    return {"revision": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))}


def run_unit():
    result = subprocess.run([str(UNIT_SIM)], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"timer unit scoreboard failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def run_firmware():
    result = subprocess.run(
        [str(SOC_SIM), f"+rom={FIRMWARE_HEX}", "+ram_fill=a5a5a5a5", "--timer"],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"timer firmware scoreboard failed:\n{result.stdout}\n{result.stderr}")
    return (result.stdout + result.stderr).strip()


def parse_unit(output):
    require(output.splitlines()[-1] == UNIT_PASS, "timer unit scoreboard did not report the expected PASS")
    return UNIT_PASS


def parse_firmware(output):
    latency = None
    for line in output.splitlines():
        match = re.fullmatch(r"timer MATCH_LATENCY=(\d+)", line)
        if match:
            latency = int(match.group(1))
    require(latency is not None, "timer firmware did not report a match latency")
    require(0 < latency < LATENCY_LIMIT, "timer match latency is out of bounds")
    require(re.search(r"PASS: coherent SoC timer harts=2 caches=1", output),
            "timer firmware did not report the expected PASS")
    return latency


def capture(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    study_path = output / "study.json"
    if study_path.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing study {study_path}")
    unit = run_unit()
    parse_unit(unit)
    repeats = []
    for _ in range(args.repeats):
        record = run_firmware()
        latency = parse_firmware(record)
        repeats.append(record)
        print(f"repeat {len(repeats)}/{args.repeats}: match_latency={latency}", flush=True)
    study = {"schema": SCHEMA, "provenance": provenance(), "unit": unit, "firmware": repeats}
    study_path.write_text(json.dumps(study, indent=2, sort_keys=True) + "\n")
    print(f"wrote {study_path} ({len(repeats)} firmware repeats)")
    return 0


def audit_study(study):
    require(study.get("schema") == SCHEMA, "study is not a Phase 12.5 study")
    require(study["provenance"]["dirty"] is False, "study was captured from a dirty tree")
    parse_unit(study["unit"])
    require(len(study["firmware"]) >= 2, "study has too few firmware repeats")
    latencies = [parse_firmware(record) for record in study["firmware"]]
    require(all(record == study["firmware"][0] for record in study["firmware"]),
            "fresh firmware repeats do not reproduce the record")
    require(all(latency == latencies[0] for latency in latencies), "match latency is not deterministic")
    return {"repeats": len(latencies), "match_latency_cycles": latencies[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--output", required=True)
    cap.add_argument("--repeats", type=int, default=3)
    cap.add_argument("--force", action="store_true")
    aud = sub.add_parser("audit")
    aud.add_argument("study")
    args = parser.parse_args()
    if args.action == "capture":
        return capture(args)
    result = audit_study(json.loads(Path(args.study).read_text()))
    print(f"PASS: Phase 12.5 study audit ({result['repeats']} repeats, "
          f"match_latency={result['match_latency_cycles']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
