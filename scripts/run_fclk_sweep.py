#!/usr/bin/env python3
"""Frequency measurement: sweep the Pynq-Z1 FCLK0 above the 31.25 MHz baseline.

Programs the overlay once, then for each target frequency sets FCLK0, runs a
workload with the CPU restarted cleanly, and records whether the independent
oracle checksum still matches. The highest passing frequency is the measured
operating ceiling. It must run as root.
"""
import argparse
import json
import mmap
import os
import platform
import re
import struct
import time
from pathlib import Path

from pynq_handoff import validate_handoff
import asterbench_v10 as bench
import workload_reference as reference
from run_xe_mem import Mmio, XeBridge, program_pl, read_firmware, digest

# (label, divisor0, divisor1) -> FCLK0 = 1000 / (d0*d1) MHz
FREQUENCIES = [
    ("31.25", 4, 8), ("35.71", 4, 7), ("38.46", 2, 13), ("40.00", 5, 5),
    ("41.67", 4, 6), ("43.48", 1, 23), ("45.45", 2, 11), ("47.62", 3, 7),
    ("50.00", 4, 5), ("52.63", 1, 19), ("55.56", 3, 6), ("62.50", 4, 4),
    ("71.43", 2, 7), ("83.33", 3, 4), ("100.0", 2, 5),
]


def set_fclk0(divisor0, divisor1):
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mapping = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0xF8000000)
    old = struct.unpack_from("<I", mapping, 0x170)[0]
    value = (old & ~((0x3F << 8) | (0x3F << 20))) | (divisor0 << 8) | (divisor1 << 20)
    mapping[0x170:0x174] = struct.pack("<I", value)
    time.sleep(0.05)
    readback = struct.unpack_from("<I", mapping, 0x170)[0]
    os.close(fd)
    return 1000.0 / (((readback >> 8) & 0x3F) * ((readback >> 20) & 0x3F))


def run_once(bridge, args, timeout):
    started = time.monotonic()
    payload = bytearray()
    bridge.start()
    time.sleep(args.host_pause)
    while time.monotonic() - started < timeout:
        count = bridge.mmio.read(0x0C)
        if not 0 <= count <= 512:
            raise RuntimeError("invalid receive FIFO count")
        for _ in range(count):
            word = bridge.mmio.read(8)
            if word & 0xFFFFFF00 != 0x80000000:
                raise RuntimeError("serial FIFO count/pop disagreement")
            payload.extend(bytes([word & 0xFF]))
        if bridge.mmio.read(4) & 0x1A:
            raise RuntimeError("trap, receive overflow or framing error")
        if b"ASTERBENCH," in payload and payload.endswith(b"\n"):
            record = payload.decode("ascii").strip()
            parsed = bench.validate_line(record if record.endswith("\n") else record + "\n", name=args.name)
            reference.verify(parsed, args.name)
            bridge.stop()
            return parsed, time.monotonic() - started
        if count == 0:
            time.sleep(0.0001)
    bridge.stop()
    raise TimeoutError("frequency sweep capture timed out")


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("output already exists; evidence is never overwritten")
    bitstream = args.bitstream.resolve(strict=True)
    handoff = validate_handoff(bitstream.with_suffix(".hwh"), 2, expected_coherent=True,
                               expected_cache=True, expected_dma=True, expected_dot8=True,
                               expected_npu=True)
    words = read_firmware(args.firmware.resolve(strict=True))
    output.mkdir(parents=True)
    program_pl(bitstream)
    report = {"schema": "aster.frequency.sweep.v1", "status": "running", "board": platform.node(),
              "kernel": platform.release(), "bitstream_sha256": digest(bitstream),
              "firmware_sha256": digest(args.firmware.resolve()), "name": args.name,
              "handoff_preflight": handoff, "results": [], "ceiling_mhz": None}
    report_path = output / "sweep.json"
    bridge = XeBridge(Mmio(0x40000000, 0x40000), harts=2, caches=True)
    try:
        for label, d0, d1 in FREQUENCIES:
            actual = set_fclk0(d0, d1)
            bridge.load_words(words)
            try:
                parsed, elapsed = run_once(bridge, args, args.timeout)
                entry = {"label": label, "fclk_mhz": round(actual, 2), "status": "PASS",
                         "cycles": parsed["cycles"], "checksum": f"0x{parsed['checksum']:08x}",
                         "elapsed_seconds": round(elapsed, 4)}
            except (RuntimeError, TimeoutError) as error:
                entry = {"label": label, "fclk_mhz": round(actual, 2), "status": "FAIL",
                         "error": str(error)}
            report["results"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"FCLK {actual:6.2f} MHz  {entry['status']}", flush=True)
            if entry["status"] == "FAIL" and report["ceiling_mhz"] is None:
                report["ceiling_mhz"] = report["results"][-2]["fclk_mhz"] if len(report["results"]) > 1 else 0.0
                break
        if report["ceiling_mhz"] is None and report["results"]:
            report["ceiling_mhz"] = report["results"][-1]["fclk_mhz"]
        report["status"] = "complete"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    finally:
        set_fclk0(4, 8)  # restore the 31.25 MHz baseline
        bridge.stop()
    print(f"PASS: measured FCLK0 ceiling {report['ceiling_mhz']} MHz -> {report_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--host-pause", type=float, default=0.02)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
