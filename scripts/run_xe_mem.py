#!/usr/bin/env python3
"""Phase 10 physical runner without PYNQ: fpga_manager + /dev/mem.

The board's PYNQ 3.1.1 install cannot enumerate this Zynq under the 6.6.10
Xilinx kernel, so this runner programs the PL through the standard Linux
fpga_manager interface and performs MMIO through /dev/mem. It must run as root.
It preserves the same guarantees: only the boot ROM is written by the ARM host,
the RISC-V firmware owns every descriptor and payload, and each emitted record
is checked by the independent v8 oracle before it is accepted.
"""
import argparse
import hashlib
import json
import mmap
import os
import platform
import re
import struct
import time
from pathlib import Path

from pynq_handoff import validate_handoff
import asterbench_v8 as bench

MAGIC = 0x41535452
VERSION = 0x00090001


class Mmio:
    def __init__(self, base, size):
        self.base = base
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.map = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

    def read(self, offset):
        return struct.unpack_from("<I", self.map, offset)[0]

    def write(self, offset, value):
        # On this Zynq kernel, ctypes scalar stores and struct.pack_into fault
        # with a bus error; mmap slice assignment issues a plain, accepted store.
        self.map[offset:offset + 4] = struct.pack("<I", value & 0xFFFFFFFF)


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


def program_pl(bitstream):
    payload = bitstream.read_bytes()
    index = payload.find(b"\xaa\x99\x55\x66")
    if index < 0:
        raise ValueError("bitstream has no configuration sync word")
    body = payload[index:]
    if len(body) % 4:
        raise ValueError("bitstream payload is not word aligned")
    swapped = b"".join(body[i:i + 4][::-1] for i in range(0, len(body), 4))
    firmware = Path("/lib/firmware/aster_phase10.bin")
    firmware.write_bytes(swapped)
    Path("/sys/class/fpga_manager/fpga0/firmware").write_text("aster_phase10.bin")
    state = Path("/sys/class/fpga_manager/fpga0/state").read_text().strip()
    if state != "operating":
        raise RuntimeError(f"fpga_manager state is {state!r}, not operating")


def set_fclk0_mhz(target=31.25):
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mapping = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=0xF8000000)
    offset = 0x170
    old = struct.unpack_from("<I", mapping, offset)[0]
    for divisor0, divisor1 in ((4, 8), (5, 8), (4, 10), (8, 4), (4, 5), (5, 4)):
        value = (old & ~((0x3F << 8) | (0x3F << 20))) | (divisor0 << 8) | (divisor1 << 20)
        mapping[offset:offset + 4] = struct.pack("<I", value)
        time.sleep(0.05)
        readback = struct.unpack_from("<I", mapping, offset)[0]
        actual = 1000.0 / (((readback >> 8) & 0x3F) * ((readback >> 20) & 0x3F))
        if abs(actual - target) < 0.01:
            return actual
    raise RuntimeError(f"could not set FCLK0 to {target} MHz")


class XeBridge:
    def __init__(self, mmio, *, harts=2, caches=True, dot8=True, npu=True, clock_hz=31_250_000):
        feature = 1 | (int(caches) << 1) | (1 << 2) | (int(dot8) << 3) | (int(npu) << 4)
        expected = {0x18: MAGIC, 0x1C: VERSION, 0x20: clock_hz, 0x24: harts, 0x44: feature,
                    0x80: 1, 0x9C: 5}
        for address, value in expected.items():
            actual = mmio.read(address)
            if actual != value:
                raise RuntimeError(f"wrong Phase 10 bridge identity at 0x{address:x}: {actual:#x} != {value:#x}")
        self.mmio = mmio

    def require_stopped(self):
        if self.mmio.read(0) != 0 or self.mmio.read(0x40) != 1 or self.mmio.read(0x28) != 0:
            raise RuntimeError("operation requires acknowledged STOPPED")

    def stop(self, timeout=5):
        deadline = time.monotonic() + timeout
        self.mmio.write(0, 0)
        while time.monotonic() < deadline:
            if self.mmio.read(0x40) == 1:
                self.require_stopped()
                if self.mmio.read(4) != 0:
                    raise RuntimeError("serial/CPU reset incomplete after STOPPED")
                return
            time.sleep(0.0001)
        raise TimeoutError("drain/flush did not acknowledge STOPPED")

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
        raise TimeoutError("primary did not start")

    def load_words(self, words, timeout=5):
        if len(words) != 16384:
            raise ValueError("firmware must contain exactly 16384 words")
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
            if not 0 <= count <= 512:
                raise RuntimeError("invalid receive FIFO count")
            for _ in range(count):
                word = bridge.mmio.read(8)
                if word & 0xFFFFFF00 != 0x80000000:
                    raise RuntimeError("serial FIFO count/pop disagreement")
                value = bytes([word & 0xFF])
                raw.write(value)
                payload.extend(value)
            raw.flush()
            if b"XE BENCH" in payload:
                raise RuntimeError("firmware reported failure")
            if bridge.mmio.read(4) & 0x1A:
                raise RuntimeError("trap, receive overflow or framing error")
            if complete.search(payload):
                break
            if count == 0:
                time.sleep(0.0001)
        else:
            raise TimeoutError("physical capture timed out")
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
            "uart_output": payload.decode("ascii"), "elapsed_seconds": time.monotonic() - started}


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("physical output already exists; evidence is never overwritten")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("revision must be a committed 40-character Git revision")
    bitstream = args.bitstream.resolve(strict=True)
    hwh = bitstream.with_suffix(".hwh")
    handoff = validate_handoff(hwh, args.harts, expected_coherent=True,
                               expected_cache=not args.no_cache, expected_dma=True,
                               expected_dot8=True, expected_npu=True)
    words = read_firmware(args.firmware.resolve(strict=True))
    output.mkdir(parents=True)
    report = {"schema": "aster.xe.physical-mem.v1", "status": "running",
              "board": platform.node(), "kernel": platform.release(),
              "source_revision": args.revision, "handoff_preflight": handoff,
              "transport": "fpga_manager PL load + /dev/mem AXI, FPGA UART TX-to-RX loopback",
              "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
              "firmware_sha256": digest(args.firmware.resolve()),
              "hart_count": args.harts, "cache": not args.no_cache,
              "kernel": args.kernel, "method": args.method, "jobs": args.jobs,
              "programmed": False, "clock_mhz": None, "boots": [], "final_state": None}
    report_path = output / "physical.json"
    if not args.assume_programmed:
        program_pl(bitstream)
        report["programmed"] = True
    report["clock_mhz"] = set_fclk0_mhz(31.25)
    bridge = XeBridge(Mmio(0x40000000, 0x40000), harts=args.harts, caches=not args.no_cache)
    try:
        bridge.load_words(words)
        for index in range(1, args.boots + 1):
            entry = capture_boot(bridge, output, index, args)
            report["boots"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"PASS: physical Phase 10 boot={index} serial_bytes={entry['uart']['bytes']}", flush=True)
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
    print(f"PASS: physical Phase 10 package safely STOPPED, {report_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--harts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--assume-programmed", action="store_true")
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
