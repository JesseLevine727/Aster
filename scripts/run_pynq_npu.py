#!/usr/bin/env python3
"""Guarded Phase 9 NPU runtime on PYNQ Linux/PCAP.

All file, overlay, ABI and firmware checks happen before importing PYNQ. The
ARM host loads only the boot ROM and controls RUN/STOP; the RISC-V NPU driver
owns descriptors and shared-RAM payloads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import struct
import time

from npu_bridge import NpuBridge
from pynq_handoff import validate_handoff


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


def capture_boot(bridge, output, index, timeout, host_pause):
    uart_path = output / f"boot{index}.uart"
    started = time.monotonic()
    payload = bytearray()
    bridge.start()
    time.sleep(host_pause)
    with uart_path.open("xb") as raw:
        while time.monotonic() - started < timeout:
            count = bridge.mmio.read(0x0C)
            if type(count) is not int or not 0 <= count <= 512:
                raise RuntimeError("invalid NPU receive FIFO count")
            for _ in range(count):
                word = bridge.mmio.read(8)
                if type(word) is not int or word & 0xFFFFFF00 != 0x80000000:
                    raise RuntimeError("NPU serial FIFO count/pop disagreement")
                value = bytes([word & 0xFF])
                raw.write(value)
                payload.extend(value)
            raw.flush()
            if b"NPU RUNTIME FAIL" in payload:
                raise RuntimeError("NPU runtime firmware reported failure")
            if bridge.mmio.read(4) & 0x1A:
                raise RuntimeError("NPU trap, receive overflow or framing error")
            if re.search(rb"NPU RUNTIME PASS jobs=9 checks=[0-9]+ summary=0x[0-9a-fA-F]+\n$", payload):
                break
            if count == 0:
                time.sleep(0.0001)
        else:
            raise TimeoutError("physical Phase 9 NPU runtime timed out")

    time.sleep(0.02)
    before = {
        "control": bridge.mmio.read(0),
        "status": bridge.mmio.read(4),
        "hart_status": bridge.mmio.read(0x28),
        "stop_status": bridge.mmio.read(0x40),
        "tx_bytes": bridge.mmio.read(0x10),
        "rx_bytes": bridge.mmio.read(0x14),
        "fifo_count": bridge.mmio.read(0x0C),
        "lifetime_retired": bridge.lifetime_retired(),
        "faults": bridge.faults(),
    }
    if (before["control"], before["status"], before["hart_status"], before["stop_status"],
            before["tx_bytes"], before["rx_bytes"], before["fifo_count"]) != (1, 1, 1, 0, len(payload), len(payload), 0):
        raise RuntimeError("NPU runtime serial/core state mismatch before STOP")
    bridge.stop()
    ram_path = output / f"boot{index}.ram"
    ram = bridge.read_ram()
    with ram_path.open("xb") as stream:
        stream.write(ram)
    match = re.search(rb"summary=0x([0-9a-fA-F]+)\n$", payload)
    if match is None:
        raise RuntimeError("NPU runtime omitted the shared summary address")
    summary = int(match.group(1), 16)
    if not 0x10000000 <= summary < 0x10008000 or summary & 3:
        raise RuntimeError("NPU summary escaped aligned shared RAM")
    offset = summary - 0x10000000
    if struct.unpack_from("<III", ram, offset) != (0x4E505539, int(re.search(rb"checks=([0-9]+)", payload).group(1)), 9):
        raise RuntimeError("stopped RAM does not retain the NPU runtime summary")
    return {
        "boot": index,
        "status": "PASS",
        "uart": artifact(uart_path),
        "ram": artifact(ram_path),
        "before_stop": before,
        "after_stop": {"control": 0, "status": 0, "hart_status": 0, "stop_status": 1},
        "uart_output": payload.decode("ascii"),
        "summary_address": summary,
        "elapsed_seconds": time.monotonic() - started,
    }


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("physical output already exists; evidence is never overwritten")
    if type(args.revision) is not str or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("revision must be a committed 40-character Git revision")
    if args.expected_loaded != "none" and not args.expected_loaded.startswith("/"):
        raise ValueError("expected loaded overlay must be an absolute path or 'none'")
    expected_loaded = None if args.expected_loaded == "none" else args.expected_loaded
    if not 1 <= args.boots <= 4 or not 1 <= args.timeout <= 120 or not 0 <= args.host_pause < args.timeout:
        raise ValueError("invalid boot/timing options")
    bitstream = args.bitstream.resolve(strict=True)
    if bitstream.suffix != ".bit":
        raise ValueError("Phase 9 overlay must be a .bit file")
    hwh = bitstream.with_suffix(".hwh")
    hwh.resolve(strict=True)
    handoff = validate_handoff(hwh, args.harts, expected_coherent=True,
                               expected_cache=not args.no_cache, expected_dma=True, expected_npu=True)
    firmware = args.firmware.resolve(strict=True)
    words = read_firmware(firmware)
    print("PYNQ9: NPU overlay/handoff/firmware preflight passed", flush=True)

    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    if pynq.Device.active_device.name != "Pynq-Z1":
        raise RuntimeError("physical Phase 9 target is not Pynq-Z1")
    previous = PL.bitfile_name
    if previous != expected_loaded:
        raise RuntimeError(f"loaded overlay changed; expected {expected_loaded}, observed {previous}")
    overlay = Overlay(str(bitstream), download=False)
    if not args.no_download:
        print("PYNQ9: downloading verified NPU overlay through Linux/PCAP", flush=True)
        overlay.download()
    elif previous != str(bitstream):
        raise RuntimeError("--no-download requires the exact NPU overlay to be loaded")
    if PL.bitfile_name != str(bitstream):
        raise RuntimeError("PYNQ loaded-overlay identity differs")
    Clocks.fclk0_mhz = 31.25
    if abs(Clocks.fclk0_mhz - 31.25) > 0.001:
        raise RuntimeError("physical FCLK0 is not 31.25 MHz")

    output.mkdir(parents=True)
    report = {
        "schema": "aster.npu.physical.v1",
        "status": "running",
        "board": pynq.Device.active_device.name,
        "pynq_version": pynq.__version__,
        "kernel": platform.release(),
        "source_revision": args.revision,
        "handoff_preflight": handoff,
        "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
        "external_pmod_loopback": False,
        "previous_bitstream": previous,
        "loaded_bitstream": str(bitstream),
        "downloaded": not args.no_download,
        "bitstream_sha256": digest(bitstream),
        "hwh_sha256": digest(hwh),
        "firmware_sha256": digest(firmware),
        "clock_mhz": float(Clocks.fclk0_mhz),
        "hart_count": args.harts,
        "cache": not args.no_cache,
        "boots": [],
        "final_state": None,
    }
    report_path = output / "physical.json"
    bridge = NpuBridge(MMIO(0x40000000, 0x40000), harts=args.harts, caches=not args.no_cache)
    try:
        bridge.load_words(words)
        for index in range(1, args.boots + 1):
            entry = capture_boot(bridge, output, index, args.timeout, args.host_pause)
            report["boots"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"PASS: physical Pynq-Z1 NPU boot={index} serial_bytes={entry['uart']['bytes']} retained_RAM=65536", flush=True)
        bridge.stop()
        report["final_state"] = {"control": 0, "status": 0, "hart_status": 0, "stop_status": 1}
        report["status"] = "complete"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except BaseException as error:
        try:
            bridge.stop()
            report["final_state"] = {"control": 0, "status": 0, "hart_status": 0, "stop_status": 1}
        except BaseException as stop_error:
            report["stop_error"] = str(stop_error)
        report["status"] = "failed"
        report["error"] = str(error)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        raise
    finally:
        del overlay
    print(f"PASS: physical Phase 9 NPU package safely STOPPED, {report_path}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-loaded", required=True,
                        help="absolute prior overlay path, or 'none' when PL is unloaded")
    parser.add_argument("--harts", type=int, choices=(1, 2), default=2)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--boots", type=int, default=2)
    parser.add_argument("--host-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
