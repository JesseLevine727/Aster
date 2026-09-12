#!/usr/bin/env python3
"""Audit retained physical Phase 2 records against images and RTL references.

This checks captured evidence; it does not contact a board or turn simulation
into a physical measurement. Use run_pynq.py to obtain new physical records.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from bench_results import validate_result
from pynq_handoff import validate_handoff
from run_pynq import validate_output


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_board_report(report, name, manifest, reference=None):
    kind = name if name in ("hello", "stress") else "bench"
    expected = {"schema": 1, "board": "Pynq-Z1", "kind": kind,
                "source_revision": manifest["source_revision"],
                "bitstream_sha256": manifest["bitstream_sha256"],
                "hwh_sha256": manifest["hwh_sha256"],
                "firmware_sha256": manifest["firmware_sha256"][name],
                "clock_mhz": 31.25, "baud": 115200, "host_pause_seconds": 0.2,
                "external_pmod_loopback": False,
                "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
                "handoff_preflight": manifest["handoff_preflight"]}
    for key, value in expected.items():
        require(type(report.get(key)) is type(value) and
                json.dumps(report[key], sort_keys=True) == json.dumps(value, sort_keys=True),
                f"{name}: wrong/missing {key}")
    for key in ("pynq_version", "kernel"):
        require(isinstance(report.get(key), str) and report[key], f"{name}: missing {key}")
    if reference is not None:
        expected_record = validate_result(reference)
        metadata = reference["metadata"]
        require(not metadata["dirty"] and metadata["revision"] == manifest["source_revision"],
                f"{name}: unclean or wrong reference revision")
        require(metadata["firmware_sha256"] == report["firmware_sha256"], f"{name}: reference image mismatch")
        require(json.dumps(report.get("host_build_provenance"), sort_keys=True) ==
                json.dumps(metadata, sort_keys=True), f"{name}: reference provenance mismatch")
    else:
        expected_record = None
        require(report.get("host_build_provenance") is None, f"{name}: unexpected benchmark metadata")
    boots = report.get("boots")
    require(isinstance(boots, list) and len(boots) == 2, f"{name}: require two captured warm boots")
    for index, boot in enumerate(boots):
        require(type(boot.get("boot")) is int and boot["boot"] == index, f"{name}: boot order")
        require(boot.get("status") == "PASS" and type(boot.get("bridge_status")) is int
                and boot["bridge_status"] == 1, f"{name}: trap/serial error or incomplete run")
        require(isinstance(boot.get("uart_output"), str), f"{name}: missing UART bytes")
        payload = boot["uart_output"].encode("ascii")
        parsed = validate_output(payload, kind)
        for key in ("tx_bytes", "rx_bytes"):
            require(type(boot.get(key)) is int and boot[key] == len(payload), f"{name}: wrong {key}")
        require(json.dumps(boot.get("counter_record"), sort_keys=True) == json.dumps(parsed, sort_keys=True),
                f"{name}: parsed/raw UART disagreement")
        require(parsed == expected_record, f"{name}: physical/Verilator field disagreement")
        elapsed = boot.get("elapsed_seconds")
        require(type(elapsed) in (float, int) and math.isfinite(elapsed) and elapsed >= 0.2,
                f"{name}: invalid host observation interval")
    return expected_record


def audit(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest.get("schema") == 1 and re.fullmatch("[0-9a-f]{40}", manifest["source_revision"]),
            "invalid phase evidence manifest")
    for name, expected in manifest["files"].items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "invalid artifact path")
        require(hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected,
                f"artifact hash mismatch: {name}")

    def read(name):
        require(name in manifest["files"], f"unfingerprinted artifact: {name}")
        return (directory / name).read_text()

    require("fpga/aster_linux.hwh" in manifest["files"], "missing hardware handoff")
    require(validate_handoff(directory / "fpga/aster_linux.hwh") == manifest["handoff_preflight"],
            "hardware handoff mismatch")
    require(manifest["files"]["fpga/aster_linux.hwh"] == manifest["hwh_sha256"], "HWH image hash mismatch")
    for name in ("hello", "stress", "memcpy", "walk_sequential", "walk_random"):
        report = json.loads(read(f"{name}.json"))
        reference = json.loads(read(f"reference_{name}.json")) if name not in ("hello", "stress") else None
        record = validate_board_report(report, name, manifest, reference)
        counters = f", cycles={record['cycles']}, retired={record['retired']}" if record else ""
        print(f"PASS: physical {name}, 2 boots, {report['boots'][0]['rx_bytes']} serial bytes{counters}")
    require("FINAL_CONTROL 0 FINAL_STATUS 0" in read("board/final_state.log"), "CPU not stopped")
    require("Fatal: initial release with auxiliary tied low" in read("reset-before.log"), "missing failure reproduction")
    require("PASS: generated PYNQ reset netlist, five assert/release scenarios" in read("reset-after.log"), "reset regression missing")
    for name in ("drc", "methodology"):
        require(re.search(r"Checks found:\s+0\b", read(f"fpga/{name}.rpt")), f"{name} findings")
    require(re.search(r"nets with routing errors\.+\s*:\s*0\s*:", read("fpga/route_status.rpt")), "routing errors")
    timing = read("fpga/timing_summary.rpt")
    require("All user specified timing constraints are met." in timing and
            "checking unconstrained_internal_endpoints (0)" in timing, "timing signoff missing")
    log = read("fpga/build.log")
    require("ASTER_LINUX_BUILD complete:" in log and "CRITICAL WARNING:" not in log, "FPGA build did not pass")
    require("PASS: exact RVFI retirement sequence" in read("check.log"), "missing full regression evidence")
    print("PASS: physical Phase 2 evidence, image hashes, reset regression and FPGA signoff")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("docs/results/phase2"))
    audit(parser.parse_args().directory)
