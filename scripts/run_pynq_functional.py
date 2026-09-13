#!/usr/bin/env python3
"""Run the full-A C runtime or selective-reset lifecycle on actual PYNQ Linux."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import time

import asterbench_coherent as bench
import coherent_functional as functional
import coherent_physical as physical
import functional_physical as proof
from bench_results import sha
from coherent_bridge import CoherentBridge
from run_pynq_coherent import artifact, state


def collect(bridge, reference, kind, output, index, host_pause, timeout, entry):
    expected = functional.UART[kind]; mmio = bridge.mmio; path = output/f"boot{index}.uart"
    with path.open("xb") as raw:
        started = time.monotonic(); bridge.start(); time.sleep(host_pause); payload = bytearray()
        while time.monotonic()-started < timeout:
            count = mmio.read(0x0c)
            if not 0 <= count <= 512: raise RuntimeError("invalid functional serial FIFO count")
            for _ in range(count):
                word = mmio.read(8)
                if word & 0xffffff00 != 0x80000000: raise RuntimeError("functional serial count/pop disagreement")
                byte = bytes([word & 255]); raw.write(byte); payload.extend(byte)
            raw.flush()
            if not expected.startswith(payload): raise RuntimeError("functional firmware reported wrong serial output")
            if mmio.read(4) & 0x1a: raise RuntimeError(f"functional core/serial error: {bridge.faults()}")
            if payload == expected: break
            if count == 0: time.sleep(0.0001)
        else: raise TimeoutError("functional hardware job timed out")
    entry["uart"] = artifact(path); time.sleep(0.02)
    before = state(bridge)
    before.update(tx_bytes=mmio.read(0x10), rx_bytes=mmio.read(0x14), fifo_count=mmio.read(0x0c),
                  lifetime_retired=bridge.lifetime_retired(), faults=bridge.faults())
    entry["before_stop"] = before; bridge.stop(); entry["after_stop"] = state(bridge)
    ram = output/f"boot{index}.ram"
    with ram.open("xb") as stream: stream.write(bridge.read_ram())
    entry.update(ram=artifact(ram), elapsed_seconds=time.monotonic()-started, status="PASS")
    try: proof.validate_boot(entry, reference, kind, output)
    except BaseException:
        entry["status"] = "failed"; raise


def run(args):
    output = args.output.resolve(); bench.require(not output.exists(), "functional output must be a new directory")
    bench.require(args.kind in functional.PROGRAMS and re.fullmatch(r"[0-9a-f]{40}", args.collector_revision) and
                  Path(args.expected_loaded).is_absolute(), "invalid functional run options")
    physical.finite(args.host_pause, 0, 10); physical.finite(args.timeout, 1, 120)
    bench.require(args.host_pause < args.timeout, "functional host pause exceeds deadline")
    reference, hardware = proof.preflight(args.reference, args.overlay, clean=False)
    program = reference["programs"][args.kind]
    raw = (args.reference.parent/program["artifacts"]["firmware"]["file"]).read_bytes()
    bench.require(hashlib.sha256(raw).hexdigest() == program["metadata"]["firmware_sha256"], "functional firmware changed after preflight")
    words = [int(word, 16) for word in raw.splitlines()]
    sources = proof.identity(); reference_hash = sha(args.reference); overlay_hash = sha(args.overlay)
    bitstream = (args.overlay.parent/"aster_linux.bit").resolve(strict=True)
    print("PYNQ6_FUNCTIONAL: complete ELF/ROM/source/reset/signoff preflight passed", flush=True)
    import pynq
    from pynq import Clocks, MMIO, Overlay, PL

    previous = PL.bitfile_name
    bench.require(previous == args.expected_loaded and (args.download or previous == str(bitstream)), "functional loaded-overlay guard failed")
    bench.require(pynq.Device.active_device.name == "Pynq-Z1", "functional target is not Pynq-Z1")
    report = dict(schema=proof.SCHEMA, status="running", kind=args.kind, board=pynq.Device.active_device.name,
        pynq_version=pynq.__version__, kernel=platform.release(), transport=physical.TRANSPORT, external_pmod_loopback=False,
        previous_bitstream=previous, loaded_bitstream=str(bitstream), downloaded=args.download, clock_mhz=31.25, baud=115200,
        host_pause_seconds=args.host_pause, timeout_seconds=args.timeout, reference_sha256=reference_hash, overlay_sha256=overlay_hash,
        collector_revision=args.collector_revision, collector_files=sources, boots=[], final_state=None)
    output.mkdir(parents=True); path = output/"functional-physical.json"
    def save(): path.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    save(); bridge = None; active_overlay = None; failure = None
    try:
        proof.preflight(args.reference, args.overlay, clean=False)
        bench.require(sha(args.reference) == reference_hash and sha(args.overlay) == overlay_hash, "functional input changed before PCAP")
        active_overlay = Overlay(str(bitstream), download=False)
        if args.download:
            print("PYNQ6_FUNCTIONAL: downloading verified overlay through PCAP", flush=True); active_overlay.download()
        bench.require(PL.bitfile_name == str(bitstream), "functional overlay identity differs after PCAP")
        Clocks.fclk0_mhz = 31.25
        bench.require(abs(Clocks.fclk0_mhz-31.25) <= 0.001, "functional FCLK0 mismatch")
        report["clock_mhz"] = float(Clocks.fclk0_mhz)
        bridge = CoherentBridge(MMIO(0x40000000, 0x40000), harts=2, caches=hardware["caches"])
        bridge.load_words(words)
        for index in (1, 2):
            entry = dict(boot=index, status="running"); report["boots"].append(entry); save()
            collect(bridge, reference, args.kind, output, index, args.host_pause, args.timeout, entry); save()
            print(f"PASS: physical Pynq-Z1 {args.kind} cache={int(hardware['caches'])} boot={index} "
                  f"serial_bytes={entry['uart']['bytes']} retained_RAM=65536 independent_functional_oracle", flush=True)
        bench.require(proof.identity() == sources and sha(args.reference) == reference_hash and sha(args.overlay) == overlay_hash,
                      "functional collector/input changed during capture")
    except BaseException as error: failure = error
    finally:
        if bridge is not None:
            try: bridge.stop(); report["final_state"] = state(bridge)
            except BaseException as error:
                report["stop_error"] = str(error)
                if failure is None: failure = error
        report["status"] = "failed" if failure else "complete"
        if failure: report["error"] = str(failure)
        save(); del active_overlay
    if failure: raise failure
    try: proof.audit(path, args.reference, args.overlay, clean=False)
    except BaseException as error:
        report.update(status="failed", error=str(error)); save(); raise
    print(f"PASS: functional physical package audited and safely STOPPED, {path}", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "overlay", "output"): parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--kind", choices=tuple(functional.PROGRAMS), required=True)
    parser.add_argument("--collector-revision", required=True); parser.add_argument("--expected-loaded", required=True)
    parser.add_argument("--download", action="store_true"); parser.add_argument("--host-pause", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    try: run(args)
    except (ValueError, RuntimeError, OSError) as error: parser.exit(1, f"FAIL: {error}\n")
