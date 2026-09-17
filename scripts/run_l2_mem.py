#!/usr/bin/env python3
"""v1.1 physical capture: fpga_manager PL load + /dev/mem MMIO.

Runs a coherent AsterBench v10 workload on the L2-enabled Pynq-Z1 overlay at
31.25 MHz, validates the emitted record with the strict v10 validator and the
independent oracle, and snapshots the stopped RAM. It must run as root.
"""
import argparse
import json
import platform
import re
import time
from pathlib import Path

from pynq_handoff import validate_handoff
import asterbench_v10 as bench
import workload_reference as reference
from run_xe_mem import Mmio, XeBridge, program_pl, set_fclk0_mhz, read_firmware, digest, artifact


def capture_boot(bridge, output, index, args):
    uart_path = output / f"boot{index}.uart"
    started = time.monotonic()
    payload = bytearray()
    bridge.start()
    time.sleep(args.host_pause)
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
            if bridge.mmio.read(4) & 0x1A:
                raise RuntimeError("trap, receive overflow or framing error")
            if b"ASTERBENCH," in payload and payload.endswith(b"\n"):
                break
            if count == 0:
                time.sleep(0.0001)
        else:
            raise TimeoutError("physical v1.1 capture timed out")
    time.sleep(0.02)
    record = payload.decode("ascii").strip()
    parsed = bench.validate_line(record if record.endswith("\n") else record + "\n", name=args.name)
    reference.verify(parsed, args.name)
    before = bridge.state()
    bridge.stop()
    ram_path = output / f"boot{index}.ram"
    ram = bridge.read_ram()
    with ram_path.open("xb") as stream:
        stream.write(ram)
    after = bridge.state()
    return {"boot": index, "status": "PASS", "name": args.name,
            "cycles": parsed["cycles"], "checksum": f"0x{parsed['checksum']:08x}",
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
    handoff = validate_handoff(hwh, 2, expected_coherent=True, expected_cache=True,
                               expected_dma=True, expected_dot8=True, expected_npu=True)
    words = read_firmware(args.firmware.resolve(strict=True))
    output.mkdir(parents=True)
    report = {"schema": args.schema, "status": "running",
              "board": platform.node(), "kernel": platform.release(),
              "source_revision": args.revision, "handoff_preflight": handoff,
              "transport": "fpga_manager PL load + /dev/mem AXI, FPGA UART TX-to-RX loopback",
              "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
              "firmware_sha256": digest(args.firmware.resolve()),
              "clock_mhz": None, "name": args.name, "programmed": False,
              "boots": [], "final_state": None}
    report_path = output / "physical.json"
    if not args.assume_programmed:
        program_pl(bitstream)
        report["programmed"] = True
    report["clock_mhz"] = set_fclk0_mhz(args.clock_mhz)
    if handoff["clock_hz"] != round(args.clock_mhz * 1e6):
        raise ValueError(f"handoff clock {handoff['clock_hz']} does not match requested {args.clock_mhz} MHz")
    bridge = XeBridge(Mmio(0x40000000, 0x40000), harts=2, caches=True,
                      clock_hz=round(args.clock_mhz * 1e6))
    try:
        bridge.load_words(words)
        for index in range(1, args.boots + 1):
            entry = capture_boot(bridge, output, index, args)
            report["boots"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"PASS: physical v1.1 boot={index} name={args.name} "
                  f"cycles={entry['cycles']} checksum={entry['checksum']}", flush=True)
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
    print(f"PASS: physical v1.1 package safely STOPPED, {report_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--schema", default="aster.v1.1.physical-mem.v1")
    parser.add_argument("--assume-programmed", action="store_true")
    parser.add_argument("--boots", type=int, default=2)
    parser.add_argument("--clock-mhz", type=float, default=31.25)
    parser.add_argument("--host-pause", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
