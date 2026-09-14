#!/usr/bin/env python3
"""Read-only auditor for one guarded Phase 9 PYNQ Linux NPU package.

The auditor never imports PYNQ, opens MMIO, downloads a bitstream, or executes
saved board commands.  It verifies the immutable report, the copied UART/RAM
artifacts, the overlay hashes and the exported HWH handoff independently.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

try:
    from .pynq_handoff import validate_handoff
except ImportError:
    from pynq_handoff import validate_handoff


SCHEMA = "aster.npu.physical.v1"
PASS = re.compile(rb"NPU RUNTIME PASS jobs=9 checks=([0-9]+) summary=0x([0-9a-fA-F]+)\n\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(report, directory, key):
    value = report[key]
    require(type(value) is dict and set(value) == {"file", "bytes", "sha256"}, f"invalid {key} artifact")
    path = directory / value["file"]
    require(path.is_file() and not path.is_symlink(), f"missing {key} artifact")
    require(type(value["bytes"]) is int and value["bytes"] == path.stat().st_size, f"{key} size changed")
    require(type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) and
            value["sha256"] == sha(path), f"{key} hash changed")
    return path


def lifecycle(value, *, stopped):
    required = {"control", "status", "hart_status", "stop_status", "tx_bytes", "rx_bytes",
                "fifo_count", "lifetime_retired", "faults"}
    require(type(value) is dict and set(value) == required, "invalid physical lifecycle observation")
    require(all(type(value[key]) is int and value[key] >= 0 for key in
                ("control", "status", "hart_status", "stop_status", "tx_bytes", "rx_bytes", "fifo_count")),
            "invalid physical lifecycle counter")
    require(type(value["lifetime_retired"]) is list and len(value["lifetime_retired"]) == 2 and
            all(type(item) is int and item > 0 for item in value["lifetime_retired"]),
            "both physical harts must retire instructions")
    require(type(value["faults"]) is list and len(value["faults"]) == 2, "missing physical fault observations")
    for hart, fault in enumerate(value["faults"]):
        require(type(fault) is dict and set(fault) == {"hart", "trapped", "atomic_fault_valid", "cause",
                                                     "address", "instruction", "pc"},
                "invalid physical fault observation")
        require(fault["hart"] == hart and fault["trapped"] is False and
                fault["atomic_fault_valid"] is False and fault["cause"] == 0,
                "physical NPU fault/trap observed")
    expected = (0, 0, 0, 1, 0) if stopped else (1, 1, 1, 0, 0)
    require((value["control"], value["status"], value["hart_status"], value["stop_status"],
             value["fifo_count"]) == expected, "physical lifecycle state differs")


def audit(report_path, bitstream, *, harts=2, cache=True):
    report_path = Path(report_path).resolve(strict=True)
    directory = report_path.parent
    report = json.loads(report_path.read_text())
    fields = {"schema", "status", "board", "pynq_version", "kernel", "source_revision",
              "handoff_preflight", "transport", "external_pmod_loopback", "previous_bitstream",
              "loaded_bitstream", "downloaded", "bitstream_sha256", "hwh_sha256", "firmware_sha256",
              "clock_mhz", "hart_count", "cache", "boots", "final_state"}
    require(type(report) is dict and set(report) == fields, "physical report schema changed")
    require(report["schema"] == SCHEMA and report["status"] == "complete" and report["board"] == "Pynq-Z1",
            "incomplete or wrong-board physical report")
    require(type(report["source_revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", report["source_revision"]),
            "physical report is not tied to a committed source revision")
    require(report["transport"] == "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback" and
            report["external_pmod_loopback"] is False, "physical transport is not the guarded Linux path")
    require(report["clock_mhz"] == 31.25 and report["hart_count"] == harts and report["cache"] is cache and
            report["downloaded"] is True, "physical configuration differs from requested overlay")

    bitstream = Path(bitstream).resolve(strict=True)
    require(bitstream.suffix == ".bit", "physical evidence requires a .bit overlay")
    hwh = bitstream.with_suffix(".hwh").resolve(strict=True)
    handoff = validate_handoff(hwh, harts, expected_coherent=True, expected_cache=cache,
                               expected_dma=True, expected_npu=True)
    require(report["handoff_preflight"] == handoff, "recorded HWH handoff differs from independent validation")
    require(report["bitstream_sha256"] == sha(bitstream) and report["hwh_sha256"] == sha(hwh),
            "physical overlay hash chain differs")
    firmware = directory / "npu_runtime.hex"
    require(firmware.is_file() and report["firmware_sha256"] == sha(firmware), "physical firmware hash chain differs")

    require(type(report["boots"]) is list and len(report["boots"]) == 2, "physical warm-boot pair is incomplete")
    for index, boot in enumerate(report["boots"], 1):
        required = {"boot", "status", "uart", "ram", "before_stop", "after_stop", "uart_output",
                    "summary_address", "elapsed_seconds"}
        require(type(boot) is dict and set(boot) == required and boot["boot"] == index and boot["status"] == "PASS",
                "physical boot record is incomplete or reordered")
        uart = artifact(boot, directory, "uart")
        ram_path = artifact(boot, directory, "ram")
        require(boot["elapsed_seconds"] > 0, "physical boot elapsed time missing")
        uart_bytes = uart.read_bytes()
        require(uart_bytes == boot["uart_output"].encode("ascii") and not b"FAIL" in uart_bytes,
                "UART artifact differs from report or contains FAIL")
        match = PASS.fullmatch(uart_bytes)
        require(match is not None, "UART artifact does not contain the exact NPU PASS record")
        checks, summary = int(match.group(1)), int(match.group(2), 16)
        require(boot["summary_address"] == summary and 0x10000000 <= summary < 0x10008000 and summary % 4 == 0,
                "summary address escaped shared RAM")
        ram = ram_path.read_bytes()
        require(len(ram) == 65536, "stopped RAM image is incomplete")
        offset = summary - 0x10000000
        require(struct.unpack_from("<III", ram, offset) == (0x4E505539, checks, 9),
                "stopped RAM summary is not retained")
        lifecycle(boot["before_stop"], stopped=False)
        lifecycle(boot["after_stop"], stopped=True)
        require(boot["before_stop"]["tx_bytes"] == boot["before_stop"]["rx_bytes"] == len(uart_bytes) and
                boot["after_stop"]["tx_bytes"] == boot["after_stop"]["rx_bytes"] == len(uart_bytes),
                "physical serial accounting differs")

    require(report["final_state"] == report["boots"][-1]["after_stop"], "final physical observation is not the last stopped state")
    lifecycle(report["final_state"], stopped=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--harts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    try:
        report = audit(args.report, args.bitstream, harts=args.harts, cache=not args.no_cache)
        print(json.dumps({"board": report["board"], "boots": len(report["boots"]),
                          "cache": report["cache"], "clock_mhz": report["clock_mhz"],
                          "status": report["status"]}, sort_keys=True))
        print("PASS: read-only physical Phase 9 NPU package audit")
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
