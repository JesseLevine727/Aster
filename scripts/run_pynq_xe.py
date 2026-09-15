#!/usr/bin/env python3
"""Guarded Phase 10 cross-engine runtime on PYNQ Linux/PCAP.

Runs on the PYNQ ARM host. The ARM host loads only the boot ROM and controls
RUN/STOP; the RISC-V firmware owns every descriptor and shared-RAM payload. The
combined image enables two harts, coherent caches, DMA, DOT8 and the NPU.
"""
import argparse
import hashlib
import json
import platform
import re
import struct
import time
from pathlib import Path

from pynq_handoff import validate_handoff
import asterbench_v8 as bench

MAGIC = 0x41535452
VERSION = 0x00090001


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(path):
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}


def read_firmware(path):
    words = []
    for line in path.read_text().splitlines():
        if not re.fullmatch(r"[0-9a-fA-F]{8}", line):
            raise ValueError("firmware contains a noncanonical ROM word")
        words.append(int(line, 16))
    if len(words) != 16384:
        raise ValueError("firmware must contain exactly 16384 ROM words")
    return words


class XeBridge:
    def __init__(self, mmio, *, harts=2, caches=True, dot8=True, npu=True, clock_hz=31_250_000):
        if harts not in (1, 2) or clock_hz != 31_250_000:
            raise ValueError("invalid Phase 10 hart/clock configuration")
        feature = 1 | (int(caches) << 1) | (1 << 2) | (int(dot8) << 3) | (int(npu) << 4)
        expected = {0x18: MAGIC, 0x1C: VERSION, 0x20: clock_hz, 0x24: harts,
                    0x44: feature, 0x80: 1, 0x9C: 5}
        for address, value in expected.items():
            actual = mmio.read(address)
            if type(actual) is not int or actual != value:
                raise RuntimeError(f"wrong Phase 10 bridge identity at 0x{address:x}: {actual!r} != {value:#x}")
        self.mmio = mmio
        self.harts = harts
        self.caches = caches

    def require_stopped(self):
        if self.mmio.read(0) != 0 or self.mmio.read(0x40) != 1 or self.mmio.read(0x28) != 0:
            raise RuntimeError("operation requires acknowledged STOPPED, not just RUN=0")

    def stop(self, timeout=5):
        deadline = time.monotonic() + timeout
        self.mmio.write(0, 0)
        while time.monotonic() < deadline:
            state = self.mmio.read(0x40)
            if state == 1:
                self.require_stopped()
                if self.mmio.read(4) != 0:
                    raise RuntimeError("serial/CPU reset incomplete after STOPPED")
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 10 drain/flush did not acknowledge STOPPED")

    def start(self, timeout=5):
        deadline = time.monotonic() + timeout
        self.require_stopped()
        self.mmio.write(0, 1)
        while time.monotonic() < deadline:
            if self.mmio.read(4) & 0x1A:
                raise RuntimeError("trap/serial error during startup")
            if self.mmio.read(0x28) & 1:
                return
            time.sleep(0.0001)
        raise TimeoutError("Phase 10 primary did not start")

    def load_words(self, words, timeout=5):
        words = list(words)
        if len(words) != 16384 or any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF for word in words):
            raise ValueError("firmware must contain exactly 16384 unsigned 32-bit words")
        self.stop(timeout)
        for index, word in enumerate(words):
            self.mmio.write(0x10000 + index * 4, word)
        self.require_stopped()

    def read_ram(self):
        self.require_stopped()
        words = [self.mmio.read(0x20000 + index * 4) for index in range(16384)]
        self.require_stopped()
        return struct.pack("<16384I", *words)

    def state(self):
        return {"control": self.mmio.read(0), "status": self.mmio.read(4),
                "hart_status": self.mmio.read(0x28), "stop_status": self.mmio.read(0x40),
                "fifo_count": self.mmio.read(0x0C)}


def capture_boot(bridge, output, index, args):
    uart_path = output / f"boot{index}.uart"
    started = time.monotonic()
    payload = bytearray()
    bridge.start()
    time.sleep(args.host_pause)
    complete = re.compile(rb"(?:ASTERBENCH,[^\n]*\n){" + str(args.jobs).encode() + rb"}$")
    with uart_path.open("xb") as raw:
        while time.monotonic() - started < args.timeout:
            count = bridge.mmio.read(0x0C)
            if type(count) is not int or not 0 <= count <= 512:
                raise RuntimeError("invalid receive FIFO count")
            for _ in range(count):
                word = bridge.mmio.read(8)
                if type(word) is not int or word & 0xFFFFFF00 != 0x80000000:
                    raise RuntimeError("serial FIFO count/pop disagreement")
                value = bytes([word & 0xFF])
                raw.write(value)
                payload.extend(value)
            raw.flush()
            if b"XE BENCH" in payload:
                raise RuntimeError("Phase 10 firmware reported failure")
            if bridge.mmio.read(4) & 0x1A:
                raise RuntimeError("trap, receive overflow or framing error")
            if complete.search(payload):
                break
            if count == 0:
                time.sleep(0.0001)
        else:
            raise TimeoutError("physical Phase 10 capture timed out")
    time.sleep(0.02)
    records = [line for line in payload.decode("ascii").splitlines(keepends=True)
               if line.startswith("ASTERBENCH,")]
    if len(records) != args.jobs:
        raise RuntimeError("physical capture did not emit the expected record count")
    for line in records:
        bench.validate_line(line, method=args.method, kernel=args.kernel, jobs=args.jobs)
    before = bridge.state()
    bridge.stop()
    ram_path = output / f"boot{index}.ram"
    ram = bridge.read_ram()
    with ram_path.open("xb") as stream:
        stream.write(ram)
    after = bridge.state()
    return {"boot": index, "status": "PASS", "method": args.method, "kernel": args.kernel,
            "uart": artifact(uart_path), "ram": artifact(ram_path),
            "before_stop": before, "after_stop": after,
            "uart_output": payload.decode("ascii"),
            "elapsed_seconds": time.monotonic() - started}


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("physical output already exists; evidence is never overwritten")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("revision must be a committed 40-character Git revision")
    bitstream = args.bitstream.resolve(strict=True)
    hwh = bitstream.with_suffix(".hwh")
    hwh.resolve(strict=True)
    handoff = validate_handoff(hwh, args.harts, expected_coherent=True,
                               expected_cache=not args.no_cache, expected_dma=True,
                               expected_dot8=True, expected_npu=True)
    firmware = args.firmware.resolve(strict=True)
    words = read_firmware(firmware)
    print("PYNQ10: overlay/handoff/firmware preflight passed", flush=True)

    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    if pynq.Device.active_device.name != "Pynq-Z1":
        raise RuntimeError("physical Phase 10 target is not Pynq-Z1")
    previous = PL.bitfile_name
    if previous != args.expected_loaded:
        raise RuntimeError(f"loaded overlay changed; expected {args.expected_loaded}, observed {previous}")
    overlay = Overlay(str(bitstream), download=False)
    if not args.no_download:
        print("PYNQ10: downloading verified Phase 10 overlay through Linux/PCAP", flush=True)
        overlay.download()
    elif previous != str(bitstream):
        raise RuntimeError("--no-download requires the exact Phase 10 overlay to be loaded")
    if PL.bitfile_name != str(bitstream):
        raise RuntimeError("PYNQ loaded-overlay identity differs")
    Clocks.fclk0_mhz = 31.25
    if abs(Clocks.fclk0_mhz - 31.25) > 0.001:
        raise RuntimeError("physical FCLK0 is not 31.25 MHz")

    output.mkdir(parents=True)
    report = {
        "schema": "aster.xe.physical.v1", "status": "running",
        "board": pynq.Device.active_device.name, "pynq_version": pynq.__version__,
        "kernel": platform.release(), "source_revision": args.revision,
        "handoff_preflight": handoff,
        "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
        "external_pmod_loopback": False, "previous_bitstream": previous,
        "loaded_bitstream": str(bitstream), "downloaded": not args.no_download,
        "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
        "firmware_sha256": digest(firmware), "clock_mhz": float(Clocks.fclk0_mhz),
        "hart_count": args.harts, "cache": not args.no_cache,
        "kernel": args.kernel, "method": args.method, "jobs": args.jobs,
        "boots": [], "final_state": None,
    }
    report_path = output / "physical.json"
    bridge = XeBridge(MMIO(0x40000000, 0x40000), harts=args.harts, caches=not args.no_cache)
    try:
        bridge.load_words(words)
        for index in range(1, args.boots + 1):
            entry = capture_boot(bridge, output, index, args)
            report["boots"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"PASS: physical Pynq-Z1 Phase 10 boot={index} serial_bytes={entry['uart']['bytes']}", flush=True)
        bridge.stop()
        report["final_state"] = bridge.state()
        report["status"] = "complete"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except BaseException as error:
        try:
            bridge.stop()
            report["final_state"] = bridge.state()
        except BaseException as stop_error:
            report["stop_error"] = str(stop_error)
        report["status"] = "failed"
        report["error"] = str(error)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        raise
    finally:
        del overlay
    print(f"PASS: physical Phase 10 package safely STOPPED, {report_path}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-loaded", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--harts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--boots", type=int, default=1)
    parser.add_argument("--host-pause", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
