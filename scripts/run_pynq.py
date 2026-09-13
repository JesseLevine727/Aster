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
import math
from asterbench import parse_record
from bench_results import validate_result
from pynq_handoff import validate_handoff


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_output(payload, kind):
    if kind == "hello":
        expected = b"Hello from Aster\n"
    elif kind == "stress":
        expected = (b"UART STRESS BEGIN\n" + bytes(33 + i % 90 for i in range(1024))
                    + b"\nUART STRESS PASS\n")
    elif kind == "runtime":
        expected = b"MULTICORE RUNTIME PASS\n"
    elif kind == "parallel":
        from asterbench_parallel import parse_stream
        return parse_stream(payload.decode("ascii"))
    elif kind == "bench":
        return parse_record(payload.decode("ascii"))
    else:
        raise ValueError("unknown firmware kind")
    if payload != expected:
        raise RuntimeError(f"serial mismatch: got {len(payload)} bytes, expected {len(expected)}")


def compare_parallel_reference(records, reference):
    from asterbench_parallel import COUNTERS, FIELDS
    expected = reference["records"][0]
    if len(records) != len(expected) or any(row[key] != original[key] for row, original in zip(records, expected)
                                           for key in FIELDS - set(COUNTERS)):
        raise ValueError("physical workload/configuration/results differ from host reference")
    # UART stalls change the phase of the waiting worker between jobs. Do not
    # falsify FPGA performance by demanding event-UART cycle counts verbatim.


def read_retirement(mmio, hart):
    base = 0x30 + hart*8
    for _ in range(10):
        hi, lo, again = mmio.read(base+4), mmio.read(base), mmio.read(base+4)
        if hi == again:
            return (hi << 32) | lo
    raise RuntimeError("unstable hardware retirement observation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--kind", choices=("hello", "stress", "bench", "runtime", "parallel"), required=True)
    parser.add_argument("--hart-count", type=int, choices=(1, 2), default=2,
                        help="Phase 5 hardware hart count; legacy kinds keep their original map")
    parser.add_argument("--revision", required=True, help="source revision, including dirty marker if applicable")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path,
                        help="matching bench_results.py or parallel_results.py capture (required for bench/parallel)")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--boots", type=int, default=2)
    parser.add_argument("--host-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.boots < 1 or not math.isfinite(args.host_pause) or args.host_pause < 0:
        parser.error("boots must be positive; host-pause cannot be negative")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 120 or args.host_pause >= args.timeout:
        parser.error("timeout must be 1..120 seconds and exceed host-pause")
    if args.output.exists():
        parser.error("output already exists; choose a new evidence file")
    if args.kind in ("bench", "parallel") and not args.provenance:
        parser.error("bench/parallel requires --provenance with compiler/source/firmware metadata")
    multicore = args.kind in ("runtime", "parallel")
    reference = None
    provenance = None
    if args.provenance:
        if args.kind == "parallel":
            from parallel_results import load
            reference = load(args.provenance) # validates the accompanying raw .log too
            if reference["records"][0][0]["harts"] != args.hart_count:
                raise RuntimeError("reference requires a different hardware hart count")
        else:
            reference = json.loads(args.provenance.read_text())
            validate_result(reference)
        provenance = reference["metadata"]
        if provenance["firmware_sha256"] != digest(args.firmware):
            raise RuntimeError("provenance firmware hash does not match the image to load")
        expected_revision = provenance["revision"] + ("-dirty" if provenance["dirty"] else "")
        if args.revision != expected_revision:
            raise RuntimeError(f"revision must match provenance: {expected_revision}")

    bitstream = args.bitstream.resolve()
    hwh = bitstream.with_suffix(".hwh")
    handoff = validate_handoff(hwh, args.hart_count if multicore else 0)
    print("PYNQ: hardware handoff preflight passed", flush=True)
    image = [int(line, 16) for line in args.firmware.read_text().splitlines()]
    if len(image) != 16384 or any(word < 0 or word > 0xffffffff for word in image):
        raise RuntimeError("firmware must contain exactly 16384 32-bit ROM words")
    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    previous = PL.bitfile_name
    if args.no_download and str(bitstream) != previous:
        raise RuntimeError(f"requested image is not loaded: {previous}")
    print(f"PYNQ: opening {bitstream}, download={not args.no_download}", flush=True)
    overlay = Overlay(str(bitstream), download=False)
    print("PYNQ: metadata ready", flush=True)
    if not args.no_download:
        print("PYNQ: downloading through PCAP", flush=True)
        overlay.download()
    print("PYNQ: overlay ready; configuring FCLK0", flush=True)
    # The PS remains under Linux control. Configure the exact FCLK expected by
    # the RTL and record the observed frequency rather than assuming a preset.
    Clocks.fclk0_mhz = 31.25
    if abs(Clocks.fclk0_mhz - 31.25) > 0.01:
        raise RuntimeError("unable to establish the required 31.25 MHz FCLK")
    print("PYNQ: FCLK0 verified; probing AXI bridge identity", flush=True)
    mmio = MMIO(0x40000000, 0x40000)
    bridge_version = 0x00050001 if multicore else 0x00020001
    if mmio.read(0x18) != 0x41535452 or mmio.read(0x1c) != bridge_version:
        raise RuntimeError("unexpected Aster bridge identity/version")
    if multicore and mmio.read(0x24) != args.hart_count:
        raise RuntimeError("unexpected physical hart count")
    if mmio.read(0x20) != 31250000:
        raise RuntimeError("bitstream clock contract mismatch")
    print("PYNQ: AXI identity verified; loading boot ROM", flush=True)

    report = {
        "schema": 2 if multicore else 1, "board": pynq.Device.active_device.name,
        "pynq_version": pynq.__version__, "kernel": platform.release(),
        "source_revision": args.revision, "kind": args.kind,
        "handoff_preflight": handoff,
        "host_build_provenance": provenance,
        "transport": "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback",
        "external_pmod_loopback": False, "previous_bitstream": previous,
        "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
        "firmware_sha256": digest(args.firmware),
        "clock_mhz": Clocks.fclk0_mhz, "baud": 115200,
        "host_pause_seconds": args.host_pause, "boots": [],
    }
    if multicore:
        report.update(hart_count=args.hart_count, bridge_version=bridge_version,
                      host_reference_sha256=digest(args.provenance) if args.provenance else None)
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
            while time.monotonic() - start < args.timeout:
                count = mmio.read(0x0c)
                for _ in range(count):
                    value = mmio.read(0x08)
                    if not value & 0x80000000:
                        raise RuntimeError("serial FIFO count/pop disagreement")
                    payload.append(value & 255)
                if len(payload) > 65536:
                    raise RuntimeError("oversized serial output")
                if (args.kind == "stress" and payload.endswith(b"\nUART STRESS PASS\n")) or (
                    args.kind == "parallel" and payload.count(b"\n") >= reference["records"][0][0]["jobs"]
                ) or (
                    args.kind not in ("stress", "parallel") and payload.endswith(b"\n")
                ):
                    break
                if mmio.read(4) & 0x1a:
                    raise RuntimeError("Aster trap, receive overflow or framing error")
                if count == 0:
                    time.sleep(0.0001)
            counter_record = validate_output(bytes(payload), args.kind)
            if args.kind == "parallel":
                compare_parallel_reference(counter_record, reference)
            if counter_record and any(row["clock_hz"] != mmio.read(0x20) for row in
                                      (counter_record if isinstance(counter_record, list) else [counter_record])):
                raise RuntimeError("firmware/host bridge clock metadata disagreement")
            time.sleep(0.02)
            status = mmio.read(4)
            tx_bytes, rx_bytes = mmio.read(0x10), mmio.read(0x14)
            if status & 0x1a or mmio.read(0x0c) != 0 or tx_bytes != len(payload) or rx_bytes != len(payload):
                raise RuntimeError("serial count/status mismatch or trailing output")
            report["boots"].append({
                "boot": boot, "status": "PASS", "bridge_status": status,
                "tx_bytes": tx_bytes, "rx_bytes": rx_bytes,
                "uart_output": payload.decode("ascii"),
                "counter_record": counter_record,
                "elapsed_seconds": time.monotonic() - start,
            })
            if multicore:
                state = mmio.read(0x28)
                retirements = [read_retirement(mmio, hart) for hart in (0, 1)]
                workers = counter_record[0]["workers"] if args.kind == "parallel" else args.hart_count
                if state != 1 or retirements[0] == 0 or (retirements[1] == 0 if workers == 2 else retirements[1] != 0):
                    raise RuntimeError("missing/unexpected per-hart execution or final hart state")
                report["boots"][-1].update(hart_status=state, lifetime_retired=retirements)
            print(f"PASS: physical Pynq-Z1 {args.kind}, boot={boot}, serial_bytes={len(payload)}", flush=True)
    finally:
        mmio.write(0, 0)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    # Keep the overlay reference live until the CPU is stopped and evidence saved.
    del overlay


if __name__ == "__main__":
    main()
