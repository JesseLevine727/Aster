#!/usr/bin/env python3
"""Load and validate Aster on PYNQ Linux (run in the board's root environment).

Uses PCAP through PYNQ, not JTAG. UART bytes travel through the real FPGA
transmitter and receiver before the ARM host can read them through AXI.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_output(payload, kind):
    if kind == "hello":
        expected = b"Hello from Aster\n"
    elif kind == "stress":
        expected = (b"UART STRESS BEGIN\n" + bytes(33 + i % 90 for i in range(1024))
                    + b"\nUART STRESS PASS\n")
    else:
        text = payload.decode("ascii")
        if (not text.startswith("ASTERBENCH,") or ",status=PASS," not in text
                or not text.endswith(",accelerator_cycles=0x0000000000000000\n")):
            raise RuntimeError("invalid/incomplete AsterBench serial record")
        return
    if payload != expected:
        raise RuntimeError(f"serial mismatch: got {len(payload)} bytes, expected {len(expected)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--kind", choices=("hello", "stress", "bench"), required=True)
    parser.add_argument("--revision", required=True, help="source revision, including dirty marker if applicable")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--boots", type=int, default=2)
    parser.add_argument("--host-pause", type=float, default=0.2)
    args = parser.parse_args()
    if args.boots < 1 or args.host_pause < 0:
        parser.error("boots must be positive; host-pause cannot be negative")

    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    bitstream = args.bitstream.resolve()
    hwh = bitstream.with_suffix(".hwh")
    image = [int(line, 16) for line in args.firmware.read_text().splitlines()]
    if len(image) != 16384 or any(word < 0 or word > 0xffffffff for word in image):
        raise RuntimeError("firmware must contain exactly 16384 32-bit ROM words")
    previous = PL.bitfile_name
    if args.no_download and str(bitstream) != previous:
        raise RuntimeError(f"requested image is not loaded: {previous}")
    print(f"PYNQ: opening {bitstream}, download={not args.no_download}", flush=True)
    overlay = Overlay(str(bitstream), download=not args.no_download)
    print("PYNQ: overlay ready; configuring FCLK0", flush=True)
    # The PS remains under Linux control. Configure the exact FCLK expected by
    # the RTL and record the observed frequency rather than assuming a preset.
    Clocks.fclk0_mhz = 31.25
    if abs(Clocks.fclk0_mhz - 31.25) > 0.01:
        raise RuntimeError("unable to establish the required 31.25 MHz FCLK")
    print("PYNQ: FCLK0 verified; probing AXI bridge identity", flush=True)
    mmio = MMIO(0x40000000, 0x40000)
    if mmio.read(0x18) != 0x41535452 or mmio.read(0x1c) != 0x00020001:
        raise RuntimeError("unexpected Aster bridge identity/version")
    if mmio.read(0x20) != 31250000:
        raise RuntimeError("bitstream clock contract mismatch")
    print("PYNQ: AXI identity verified; loading boot ROM", flush=True)

    report = {
        "schema": 1, "board": pynq.Device.active_device.name,
        "pynq_version": pynq.__version__, "kernel": platform.release(),
        "source_revision": args.revision, "kind": args.kind,
        "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
        "external_pmod_loopback": False, "previous_bitstream": previous,
        "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
        "firmware_sha256": digest(args.firmware),
        "clock_mhz": Clocks.fclk0_mhz, "baud": 115200,
        "host_pause_seconds": args.host_pause, "boots": [],
    }
    mmio.write(0, 0)
    for index, word in enumerate(image):
        mmio.write(0x10000 + 4 * index, word)
    try:
        for boot in range(args.boots):
            mmio.write(0, 0)
            mmio.write(0, 1)
            start = time.monotonic()
            time.sleep(args.host_pause)
            payload = bytearray()
            while time.monotonic() - start < 15:
                count = mmio.read(0x0c)
                for _ in range(count):
                    value = mmio.read(0x08)
                    if not value & 0x80000000:
                        raise RuntimeError("serial FIFO count/pop disagreement")
                    payload.append(value & 255)
                if (args.kind == "stress" and payload.endswith(b"\nUART STRESS PASS\n")) or (
                    args.kind != "stress" and payload.endswith(b"\n")
                ):
                    break
                if mmio.read(4) & 0x1a:
                    raise RuntimeError("Aster trap, receive overflow or framing error")
                if count == 0:
                    time.sleep(0.0001)
            validate_output(bytes(payload), args.kind)
            time.sleep(0.02)
            status = mmio.read(4)
            tx_bytes, rx_bytes = mmio.read(0x10), mmio.read(0x14)
            if status & 0x1a or mmio.read(0x0c) != 0 or tx_bytes != len(payload) or rx_bytes != len(payload):
                raise RuntimeError("serial count/status mismatch or trailing output")
            report["boots"].append({
                "boot": boot, "status": "PASS", "bridge_status": status,
                "tx_bytes": tx_bytes, "rx_bytes": rx_bytes,
                "uart_output": payload.decode("ascii"),
                "elapsed_seconds": time.monotonic() - start,
            })
            print(f"PASS: physical Pynq-Z1 {args.kind}, boot={boot}, serial_bytes={len(payload)}", flush=True)
    finally:
        mmio.write(0, 0)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    # Keep the overlay reference live until the CPU is stopped and evidence saved.
    del overlay


if __name__ == "__main__":
    main()
