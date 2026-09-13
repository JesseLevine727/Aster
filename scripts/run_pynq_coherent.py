#!/usr/bin/env python3
"""Capture real Phase 6 AsterBench v4 on PYNQ Linux; no JTAG or PS reset.

All evidence/configuration preflight precedes importing PYNQ. Download is
explicit, the previous overlay must match the caller's expected path, and a
known coherent bridge is always drained/stopped on success or failure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import time

import asterbench_coherent as bench
import coherent_physical as physical
from bench_results import sha
from coherent_bridge import CoherentBridge


def state(bridge):
    return {name: bridge.mmio.read(address) for name, address in
            (("control", 0), ("status", 4), ("hart_status", 0x28), ("stop_status", 0x40))}


def artifact(path):
    data = path.read_bytes()
    return dict(file=path.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def collect_boot(bridge, reference, output, index, host_pause, timeout, entry):
    mmio = bridge.mmio; jobs = reference["configuration"]["jobs"]
    path = output/f"boot{index}.uart"
    with path.open("xb") as raw:
        started = time.monotonic(); bridge.start(); time.sleep(host_pause)
        payload = bytearray()
        while time.monotonic()-started < timeout:
            count = mmio.read(0x0c)
            if not 0 <= count <= 512: raise RuntimeError("invalid physical receive FIFO count")
            for _ in range(count):
                word = mmio.read(8)
                if word & 0xffffff00 != 0x80000000: raise RuntimeError("physical serial FIFO count/pop disagreement")
                byte = bytes([word & 255]); raw.write(byte); payload.extend(byte)
            raw.flush()
            if len(payload) > jobs*8192: raise RuntimeError("oversized physical serial stream")
            if mmio.read(4) & 0x1a: raise RuntimeError(f"physical core/serial error: {bridge.faults()}")
            if payload.count(b"\n") >= jobs: break
            if count == 0: time.sleep(0.0001)
        else: raise TimeoutError("physical coherent workload timed out")
    # Retain diagnostic serial/comparison even if an exact reference gate fails.
    entry["uart"] = artifact(path)
    records = bench.parse_stream(payload.decode("ascii")); entry["records"] = records
    entry["reference_comparison"] = physical.reference_comparison(records, reference, index)
    time.sleep(0.02)  # >200 serial frames: check idle counts and trailing bytes.
    before = state(bridge)
    before.update(tx_bytes=mmio.read(0x10), rx_bytes=mmio.read(0x14), fifo_count=mmio.read(0x0c),
                  lifetime_retired=bridge.lifetime_retired(), faults=bridge.faults())
    entry["before_stop"] = before
    bridge.stop(); entry["after_stop"] = state(bridge)
    ram = output/f"boot{index}.ram"
    with ram.open("xb") as stream: stream.write(bridge.read_ram())
    entry["ram"] = artifact(ram); entry["elapsed_seconds"] = time.monotonic()-started
    entry["status"] = "PASS"
    try: physical.validate_boot(entry, reference, output)
    except BaseException:
        entry["status"] = "failed"
        raise


def run(args):
    output = args.output.resolve()
    bench.require(not output.exists(), "physical output must be a new directory; nothing is overwritten")
    physical.finite(args.host_pause, 0, 10); physical.finite(args.timeout, 1, 120)
    bench.require(args.host_pause < args.timeout, "host pause exceeds timeout")
    bench.require(type(args.collector_revision) is str and re.fullmatch(r"[0-9a-f]{40}", args.collector_revision), "collector needs a committed revision")
    bench.require(type(args.expected_loaded) is str and Path(args.expected_loaded).is_absolute(), "expected loaded overlay must be an absolute path")
    reference, hardware = physical.preflight(args.reference, args.overlay, clean=False)
    bitstream = (args.overlay.parent/"aster_linux.bit").resolve(strict=True)
    firmware = args.reference.parent/reference["artifacts"]["firmware"]["file"]
    # Complete ELF/hex equivalence was validated by preflight; keep exact bytes
    # in memory so no changed file can be substituted after PYNQ import.
    firmware_bytes = firmware.read_bytes()
    bench.require(hashlib.sha256(firmware_bytes).hexdigest() == reference["metadata"]["firmware_sha256"], "firmware changed after preflight")
    words = [int(word, 16) for word in firmware_bytes.splitlines()]
    sources = physical.collector_identity()
    expected_reference_hash, expected_overlay_hash = sha(args.reference), sha(args.overlay)
    print("PYNQ6: full overlay/reset/signoff/firmware/reference preflight passed", flush=True)
    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    previous = PL.bitfile_name
    bench.require(previous == args.expected_loaded, f"loaded overlay changed; expected {args.expected_loaded}, observed {previous}")
    bench.require(args.download or previous == str(bitstream), "requested bitstream is not loaded; explicit --download is required")
    bench.require(pynq.Device.active_device.name == "Pynq-Z1", "this physical acceptance target is Pynq-Z1")
    report = dict(schema=physical.SCHEMA, status="running", board=pynq.Device.active_device.name,
        pynq_version=pynq.__version__, kernel=platform.release(), transport=physical.TRANSPORT, external_pmod_loopback=False,
        previous_bitstream=previous, loaded_bitstream=str(bitstream), downloaded=args.download, clock_mhz=31.25, baud=115200,
        host_pause_seconds=args.host_pause, timeout_seconds=args.timeout, reference_sha256=expected_reference_hash,
        overlay_sha256=expected_overlay_hash, collector_revision=args.collector_revision, collector_files=sources,
        boots=[], final_state=None)
    output.mkdir(parents=True); path = output/"physical.json"
    def save(): path.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    save(); bridge = None; active_overlay = None; failure = None
    try:
        # Recheck all bits/metadata immediately before the only PL mutation.
        physical.preflight(args.reference, args.overlay, clean=False)
        bench.require(sha(args.reference) == expected_reference_hash and sha(args.overlay) == expected_overlay_hash,
                      "input manifest changed during preflight")
        active_overlay = Overlay(str(bitstream), download=False)
        if args.download:
            print("PYNQ6: downloading verified overlay through PCAP", flush=True)
            active_overlay.download()
        bench.require(PL.bitfile_name == str(bitstream), "PYNQ loaded overlay identity differs")
        Clocks.fclk0_mhz = 31.25
        bench.require(abs(Clocks.fclk0_mhz-31.25) <= 0.001, "physical FCLK0 is not 31.25 MHz")
        report["clock_mhz"] = float(Clocks.fclk0_mhz)
        bridge = CoherentBridge(MMIO(0x40000000, 0x40000), harts=2, caches=hardware["caches"])
        bridge.load_words(words)
        for index in range(1, reference["configuration"]["boots"]+1):
            entry = dict(boot=index, status="running"); report["boots"].append(entry); save()
            collect_boot(bridge, reference, output, index, args.host_pause, args.timeout, entry)
            save()
            print(f"PASS: physical Pynq-Z1 coherent boot={index} jobs={len(entry['records'])} "
                  f"serial_bytes={entry['uart']['bytes']} retained_RAM=65536 exact_reference_counters", flush=True)
        bench.require(physical.collector_identity() == sources, "collector source changed during capture")
        bench.require(sha(args.reference) == expected_reference_hash and sha(args.overlay) == expected_overlay_hash,
                      "input evidence changed during physical capture")
    except BaseException as error:
        failure = error
    finally:
        if bridge is not None:
            try:
                bridge.stop(); report["final_state"] = state(bridge)
            except BaseException as error:
                report["stop_error"] = str(error)
                if failure is None: failure = error
        report["status"] = "failed" if failure else "complete"
        if failure: report["error"] = str(failure)
        save()
        # Preserve the Overlay reference until the known bridge is stopped.
        del active_overlay
    if failure: raise failure
    try:
        physical.audit(path, args.reference, args.overlay, clean=False)
    except BaseException as error:
        report.update(status="failed", error=str(error)); save(); raise
    print(f"PASS: physical coherent package audited and safely STOPPED, {path}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "overlay", "output"): parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--collector-revision", required=True)
    parser.add_argument("--expected-loaded", required=True, help="fail without programming if PL reports another overlay")
    parser.add_argument("--download", action="store_true", help="explicitly program the validated bit/HWH pair via PCAP")
    parser.add_argument("--host-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try: run(args)
    except (ValueError, RuntimeError, OSError) as error: parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__": main()
