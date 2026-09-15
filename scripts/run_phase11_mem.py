#!/usr/bin/env python3
"""Phase 11 physical runner: fpga_manager PL load + /dev/mem MMIO.

Runs on the PYNQ ARM host (root). Programs the all-engine overlay, loads the
Phase 11 inference firmware, captures the v9 records over the FPGA UART
loopback and validates every one with the independent oracle before accepting
them.
"""
import argparse
import json
import platform
import re
import time
from pathlib import Path

from pynq_handoff import validate_handoff
import asterbench_v9 as bench
from run_xe_mem import Mmio, XeBridge, program_pl, set_fclk0_mhz, read_firmware, digest, artifact


def capture_boot(bridge, output, index, args, model):
    uart_path = output / f"boot{index}.uart"
    started = time.monotonic()
    payload = bytearray()
    count = len(model["test"]["labels"])
    bridge.start()
    time.sleep(args.host_pause)
    complete = re.compile(rb"(?:ASTERBENCH,[^\n]*\n){" + str(count).encode() + rb"}")
    with uart_path.open("xb") as raw:
        while time.monotonic() - started < args.timeout:
            fifo = bridge.mmio.read(0x0C)
            if not 0 <= fifo <= 512:
                raise RuntimeError("invalid receive FIFO count")
            for _ in range(fifo):
                word = bridge.mmio.read(8)
                if word & 0xFFFFFF00 != 0x80000000:
                    raise RuntimeError("serial FIFO count/pop disagreement")
                value = bytes([word & 0xFF])
                raw.write(value)
                payload.extend(value)
            raw.flush()
            if b"MNIST INFER FAIL" in payload:
                raise RuntimeError("Phase 11 firmware reported failure")
            if bridge.mmio.read(4) & 0x1A:
                raise RuntimeError("trap, receive overflow or framing error")
            if complete.search(payload) and b"MNIST INFER PASS" in payload:
                break
            if fifo == 0:
                time.sleep(0.0001)
        else:
            raise TimeoutError("physical Phase 11 capture timed out")
    time.sleep(0.02)
    records = [line for line in payload.decode("ascii").splitlines(keepends=True)
               if line.startswith("ASTERBENCH,")]
    if len(records) != count:
        raise RuntimeError("physical capture did not emit every record")
    for line in records:
        bench.validate_line(line, model, method=args.method)
    before = bridge.state()
    bridge.stop()
    ram_path = output / f"boot{index}.ram"
    ram = bridge.read_ram()
    with ram_path.open("xb") as stream:
        stream.write(ram)
    after = bridge.state()
    return {"boot": index, "status": "PASS", "method": args.method,
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
    model = bench.load_model(args.model.resolve(strict=True))
    words = read_firmware(args.firmware.resolve(strict=True))
    output.mkdir(parents=True)
    report = {"schema": "aster.phase11.physical-mem.v1", "status": "running",
              "board": platform.node(), "kernel": platform.release(),
              "source_revision": args.revision, "handoff_preflight": handoff,
              "model_hash": model["hash"],
              "transport": "fpga_manager PL load + /dev/mem AXI, FPGA UART TX-to-RX loopback",
              "bitstream_sha256": digest(bitstream), "hwh_sha256": digest(hwh),
              "firmware_sha256": digest(args.firmware.resolve()),
              "clock_mhz": None, "method": args.method, "programmed": False,
              "boots": [], "final_state": None}
    report_path = output / "physical.json"
    if not args.assume_programmed:
        program_pl(bitstream)
        report["programmed"] = True
    report["clock_mhz"] = set_fclk0_mhz(31.25)
    bridge = XeBridge(Mmio(0x40000000, 0x40000), harts=2, caches=True)
    try:
        bridge.load_words(words)
        for index in range(1, args.boots + 1):
            entry = capture_boot(bridge, output, index, args, model)
            report["boots"].append(entry)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            print(f"PASS: physical Phase 11 {args.method} boot={index} serial_bytes={entry['uart']['bytes']}", flush=True)
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
    print(f"PASS: physical Phase 11 {args.method} package safely STOPPED, {report_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--method", required=True, choices=bench.METHODS)
    parser.add_argument("--assume-programmed", action="store_true")
    parser.add_argument("--boots", type=int, default=2)
    parser.add_argument("--host-pause", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
