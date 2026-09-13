#!/usr/bin/env python3
"""Audit retained Phase 5 FPGA, real serial, reference and regression evidence.

No board access and no synthetic conversion of simulator output into physical
measurements. New measurements must come from run_pynq.py on the actual board.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from parallel_results import load, validate_result
from pynq_handoff import validate_handoff
from run_pynq import compare_parallel_reference, validate_output

NAMES = [f"{case}_{workers}" for case in ("default", "odd", "long", "large") for workers in (1, 2)]


def require(ok, why):
    if not ok:
        raise ValueError(why)


def same(actual, expected, why):
    require(type(actual) is type(expected) and
            json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), why)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_board_report(report, name, manifest, reference=None):
    kind = "runtime" if name == "runtime" else "parallel"
    expected = dict(schema=2, board="Pynq-Z1", kind=kind, hart_count=2, bridge_version=0x50001,
                    source_revision=manifest["source_revision"], bitstream_sha256=manifest["bitstream_sha256"],
                    hwh_sha256=manifest["hwh_sha256"], firmware_sha256=manifest["firmware_sha256"][name],
                    clock_mhz=31.25, baud=115200, host_pause_seconds=0.2, external_pmod_loopback=False,
                    transport="PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
                    handoff_preflight=manifest["handoff_preflight"])
    for key, value in expected.items():
        same(report.get(key), value, f"{name}: wrong/missing {key}")
    for key in ("pynq_version", "kernel", "previous_bitstream"):
        require(isinstance(report.get(key), str) and report[key], f"{name}: missing {key}")
    if kind == "parallel":
        require(reference is not None, f"{name}: missing host reference")
        records = validate_result(reference)[0]
        metadata = reference["metadata"]
        require(not metadata["dirty"] and metadata["revision"] == manifest["source_revision"] and
                metadata["source_sha256"] == manifest["source_sha256"], f"{name}: wrong/dirty reference sources")
        same(metadata["firmware_sha256"], report["firmware_sha256"], f"{name}: wrong reference firmware")
        same(report.get("host_build_provenance"), metadata, f"{name}: altered compiler/source provenance")
        same(report.get("host_reference_sha256"), manifest["files"][f"reference_{name}.json"],
             f"{name}: reference hash mismatch")
        workers = int(name[-1])
        words, rounds, seed = {"default": (64, 4, 0x13570000), "odd": (7, 4, 0xffffffff),
                               "long": (129, 16, 1), "large": (1024, 4, 0xa57e)}[name.rsplit("_", 1)[0]]
        for row in records:
            for key, value in dict(harts=2, workers=workers, l1=1, sync_memory=1, memory_wait=1,
                                   line_words=4, line_count=16, clock_hz=31250000, jobs=3,
                                   bytes=words*4, rounds=rounds, base_seed=seed).items():
                same(row[key], value, f"{name}: wrong execution configuration {key}")
    else:
        require(reference is None, "runtime has no benchmark reference")
        same(report.get("host_build_provenance"), None, "unexpected runtime benchmark metadata")
        same(report.get("host_reference_sha256"), None, "unexpected runtime reference")
        workers = 2
    boots = report.get("boots")
    require(isinstance(boots, list) and len(boots) == 2, f"{name}: need two warm boots")
    for index, boot in enumerate(boots):
        for key, value in dict(boot=index, status="PASS", bridge_status=1, hart_status=1).items():
            same(boot.get(key), value, f"{name}: wrong boot/bridge/hart state {key}")
        payload = boot.get("uart_output")
        require(isinstance(payload, str), f"{name}: missing raw serial bytes")
        payload = payload.encode("ascii")
        parsed = validate_output(payload, kind)
        same(boot.get("counter_record"), parsed, f"{name}: parsed/raw serial disagreement")
        for key in ("tx_bytes", "rx_bytes"):
            same(boot.get(key), len(payload), f"{name}: lost/duplicate serial bytes")
        if kind == "parallel":
            compare_parallel_reference(parsed, reference)
        lifetime = boot.get("lifetime_retired")
        require(isinstance(lifetime, list) and len(lifetime) == 2 and
                all(type(x) is int and 0 <= x < 1 << 64 for x in lifetime), f"{name}: invalid retirement observations")
        for hart in (0, 1):
            if hart < workers:
                minimum = sum(row[f"h{hart}_retired"] for row in parsed) if parsed else 1
                require(lifetime[hart] >= minimum > 0, f"{name}: missing independent hardware execution")
            else:
                require(lifetime[hart] == 0, f"{name}: inactive worker executed")
        elapsed = boot.get("elapsed_seconds")
        require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0.2,
                f"{name}: invalid host observation interval")
    return boots


def audit(directory, bitstream=None):
    manifest = json.loads((directory / "manifest.json").read_text())
    same(manifest.get("schema"), 1, "invalid evidence manifest schema")
    for key, width in (("source_revision", 40), ("source_sha256", 64), ("bitstream_sha256", 64), ("hwh_sha256", 64)):
        require(isinstance(manifest.get(key), str) and re.fullmatch(f"[0-9a-f]{{{width}}}", manifest[key]),
                f"invalid {key}")
    for name, fingerprint in manifest["files"].items():
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts, "invalid artifact path")
        same(digest(directory / path), fingerprint, f"artifact hash mismatch: {name}")
    if bitstream:
        same(digest(bitstream), manifest["bitstream_sha256"], "bitstream hash mismatch")

    def read(name):
        require(name in manifest["files"], f"unfingerprinted artifact: {name}")
        return (directory / name).read_text()

    read("fpga/aster_linux.hwh")
    same(validate_handoff(directory / "fpga/aster_linux.hwh", 2), manifest["handoff_preflight"], "wrong hardware handoff")
    same(manifest["files"]["fpga/aster_linux.hwh"], manifest["hwh_sha256"], "wrong HWH hash")
    reports = {}
    for name in ["runtime"] + NAMES:
        reference = None
        if name != "runtime":
            read(f"reference_{name}.json"); read(f"reference_{name}.log")
            reference = load(directory / f"reference_{name}.json")
        report = json.loads(read(f"{name}.json"))
        validate_board_report(report, name, manifest, reference)
        log = read(f"board/board_{name}.log")
        require(log.count("PASS: physical Pynq-Z1") == 2 and "Traceback" not in log, f"{name}: incomplete physical log")
        reports[name] = report
        print(f"PASS: physical {name}, 2 warm boots, real per-hart retirement and complete serial")

    for case in ("default", "odd", "long", "large"):
        for boot in (0, 1):
            a, b = [reports[f"{case}_{w}"]["boots"][boot]["counter_record"] for w in (1, 2)]
            for x, y in zip(a, b):
                for key in ("bytes", "rounds", "jobs", "job", "base_seed", "seed", "checksum"):
                    same(x[key], y[key], f"{case}: unmatched one-/two-worker comparison")
            cycles = [sum(row["cycles"] for row in rows) for rows in (a, b)]
            print(f"MEASURED: {case} boot={boot} job_cycles={cycles} speedup={cycles[0]/cycles[1]:.6f}")

    require("FINAL_CONTROL 0 FINAL_STATUS 0 FINAL_HART_STATUS 0" in read("board/final_state.log"), "cluster not stopped")
    require("PASS: generated PYNQ reset netlist, five assert/release scenarios" in read("fpga/reset.log"), "reset gate missing")
    for name in ("drc", "methodology"):
        require(re.search(r"Checks found:\s+0\b", read(f"fpga/{name}.rpt")), f"{name} findings")
    require(re.search(r"nets with routing errors\.+\s*:\s*0\s*:", read("fpga/route_status.rpt")), "routing errors")
    timing = read("fpga/timing_summary.rpt")
    require("All user specified timing constraints are met." in timing and
            "checking unconstrained_internal_endpoints (0)" in timing, "timing signoff missing")
    build = read("fpga/build.log")
    require("ASTER_LINUX_BUILD complete:" in build and "CRITICAL WARNING:" not in build and "ERROR:" not in build,
            "FPGA build did not pass")
    checks = {
        "check": {"PASS: exact RVFI retirement, no fault side effects": 1, "PASS: dual Linux AXI/serial": 6},
        "phase1-matrix": {"PASS: RV32IM PASS": 4, "PASS: TRAP ARMED": 48},
        "cache-matrix": {"PASS: cache scoreboard": 72},
        "cache-boundaries": {"PASS: cache scoreboard": 36},
        "phase4-soc-matrix": {"PASS: RV32IM PASS": 24, "PASS: strict AsterBench v2 record": 24},
        "fabric-matrix": {"PASS: shared fabric": 24},
        "multicore-runtime-matrix": {"PASS: runtime boot=": 32},
        "multicore-adversarial-matrix": {"PASS: real-core faults": 16, "PASS: real-core reset": 18},
        "parallel-matrix": {"PASS: parallel jobs, independent kernel retirement": 24},
        "parallel-workloads": {"PASS: parallel jobs, independent kernel retirement": 10},
    }
    for name, markers in checks.items():
        log = read(f"regression/{name}.log")
        require(not re.search(r"(?m)^(FAIL:|ERROR:|FAILED \(|make.*\*\*\*)", log), f"failed regression: {name}")
        for marker, count in markers.items():
            same(sum(line.startswith(marker) for line in log.splitlines()), count, f"incomplete regression: {name}, {marker}")
        print(f"PASS: retained {name}")
    host = read("regression/closeout-host.log")
    require("Ran 34 tests" in host and re.search(r"(?m)^OK$", host) and "FAILED" not in host,
            "closeout host/auditor mutation tests missing")
    print("PASS: Phase 5 physical evidence, clean-source references, signoff and regression audit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--bitstream", type=Path, help="also verify the locally retained binary bitstream")
    args = parser.parse_args()
    audit(args.directory, args.bitstream)
