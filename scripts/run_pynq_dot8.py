#!/usr/bin/env python3
"""Collect actual paired scalar/custom execution through PYNQ Linux, never JTAG.

Offline input audits precede PYNQ import. The exact previously loaded bitstream
and its validated HWH/idle state are checked before any register write or PCAP
download. A known new bridge is always drained/stopped on success or failure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import time

import asterbench_dot8 as bench
import dot8_physical as physical
from bench_results import sha
from coherent_bridge import CoherentBridge
from dma_bridge import DmaBridge
from dot8_bridge import Dot8Bridge
from pynq_handoff import validate_handoff


def control_state(bridge):
    return {name:bridge.mmio.read(address) for name,address in
            (("control",0),("status",4),("hart_status",0x28),("stop_status",0x40))}


def state(bridge):
    return dict(control_state(bridge),dma=bridge.dma_snapshot(),dot8=bridge.dot8_snapshot())


def artifact(path):
    raw = path.read_bytes()
    return dict(file=path.name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def guard_previous(pynq, expected_path, expected_sha):
    """Read-only; no MMIO until loaded file/hash/HWH/map/clock checks succeed."""
    bench.require(pynq.Device.active_device.name == "Pynq-Z1", "physical target is not Pynq-Z1")
    bench.require(pynq.PL.bitfile_name == expected_path, "loaded overlay changed; refusing to touch another project")
    bit = Path(expected_path)
    bench.require(not bit.is_symlink() and sha(bit) == expected_sha, "loaded bitstream hash differs")
    hwh = bit.with_suffix(".hwh"); bench.require(not hwh.is_symlink(), "symlink loaded HWH")
    candidates = []
    for dot8,dma in ((False,False),(False,True),(True,True)):
        for caches in (False,True):
            try: candidates.append(validate_handoff(hwh,2,expected_coherent=True,expected_cache=caches,expected_dma=dma,expected_dot8=dot8))
            except ValueError: pass
    bench.require(len(candidates) == 1, "current overlay lacks a known coherent/DMA/dot8 HWH")
    handoff = candidates[0]
    ip = pynq.PL.ip_dict.get("aster",{})
    bench.require(ip.get("phys_addr") == 0x40000000 and ip.get("addr_range") == 0x40000, "current host address map differs")
    bench.require(abs(pynq.Clocks.fclk0_mhz-31.25) <= 0.001, "current FCLK0 is not 31.25 MHz")
    cls = Dot8Bridge if handoff.get("dot8",False) else DmaBridge if handoff.get("dma",False) else CoherentBridge
    bridge = cls(pynq.MMIO(0x40000000,0x40000),harts=2,caches=handoff["caches"])
    bridge.require_stopped(); before = control_state(bridge)
    bench.require(bench.typed_equal(before,dict(control=0,status=0,hart_status=0,stop_status=1)) and bridge.mmio.read(0x0c) == 0,
                  "current Aster has active work/serial; refusing to replace it")
    return before


def collect_boot(bridge,reference,output,index,host_pause,timeout,entry):
    mmio = bridge.mmio; methods = reference["configuration"]["jobs"]*2; path = output/f"boot{index}.uart"
    with path.open("xb") as raw:
        started = time.monotonic(); bridge.start(); time.sleep(host_pause); payload = bytearray()
        while time.monotonic()-started < timeout:
            count = mmio.read(0x0c)
            if type(count) is not int or not 0 <= count <= 512: raise RuntimeError("invalid dot8 serial FIFO count")
            for _ in range(count):
                word = mmio.read(8)
                if type(word) is not int or word & 0xffffff00 != 0x80000000: raise RuntimeError("serial count/pop disagreement")
                byte = bytes([word & 255]); raw.write(byte); payload.extend(byte)
            raw.flush()
            if len(payload) > methods*bench.MAX_RECORD: raise RuntimeError("oversized physical dot8 serial stream")
            if mmio.read(4) & 0x1a: raise RuntimeError(f"physical core/serial error: {bridge.faults()}")
            if payload.count(b"\n") >= methods: break
            if count == 0: time.sleep(0.0001)
        else: raise TimeoutError("physical dot8 workload timed out")
    entry["uart"] = artifact(path)
    rows = bench.parse_stream(bytes(payload)); entry["records"] = rows
    entry["reference_comparison"] = physical.reference_comparison(rows,reference,index)
    time.sleep(0.02)  # >200 serial frames; final diagnostics must stay frozen.
    before = state(bridge)
    before.update(tx_bytes=mmio.read(0x10),rx_bytes=mmio.read(0x14),fifo_count=mmio.read(0x0c),
                  lifetime_retired=bridge.lifetime_retired(),faults=bridge.faults())
    entry["before_stop"] = before
    bridge.stop(); entry["after_stop"] = state(bridge)
    ram = output/f"boot{index}.ram"
    with ram.open("xb") as stream: stream.write(bridge.read_ram())
    entry.update(ram=artifact(ram),elapsed_seconds=time.monotonic()-started,status="PASS")
    try: physical.validate_boot(entry,reference,output)
    except BaseException:
        entry["status"] = "failed"; raise


def run(args):
    bench.require(not args.output.is_symlink(), "symlink physical output")
    output = args.output.resolve(); bench.require(not output.exists(), "physical output must be new; old evidence is preserved")
    physical.finite(args.host_pause,0,10); physical.finite(args.timeout,1,120)
    bench.require(args.host_pause < args.timeout and type(args.download) is bool, "invalid observation/download options")
    bench.require(type(args.collector_revision) is str and re.fullmatch(r"[0-9a-f]{40}",args.collector_revision), "collector needs a committed revision")
    bench.require(type(args.expected_loaded) is str and Path(args.expected_loaded).is_absolute(), "expected loaded overlay must be absolute")
    physical.results.digest(args.expected_loaded_sha256)
    reference,hardware = physical.preflight(args.reference,args.overlay,clean=False)
    bitstream = (args.overlay.parent/"aster_linux.bit").resolve(strict=True)
    firmware = args.reference.parent/reference["artifacts"]["firmware"]["file"]
    raw = firmware.read_bytes()
    bench.require(hashlib.sha256(raw).hexdigest() == reference["metadata"]["firmware_sha256"], "firmware changed after preflight")
    words = [int(word,16) for word in raw.splitlines()]
    sources = physical.collector_identity(); reference_hash,overlay_hash = sha(args.reference),sha(args.overlay)
    print("PYNQ8: complete overlay/reset/signoff/ELF/ROM/reference preflight passed",flush=True)
    import pynq

    bench.require(args.download or args.expected_loaded == str(bitstream), "requested overlay not loaded; explicit --download required")
    previous_state = guard_previous(pynq,args.expected_loaded,args.expected_loaded_sha256)
    report = dict(schema=physical.SCHEMA,status="running",board=pynq.Device.active_device.name,pynq_version=pynq.__version__,
        kernel=platform.release(),transport=physical.TRANSPORT,external_pmod_loopback=False,
        previous_bitstream=args.expected_loaded,previous_bitstream_sha256=args.expected_loaded_sha256,previous_state=previous_state,
        loaded_bitstream=str(bitstream),downloaded=args.download,clock_mhz=31.25,baud=115200,
        host_pause_seconds=args.host_pause,timeout_seconds=args.timeout,reference_sha256=reference_hash,overlay_sha256=overlay_hash,
        collector_revision=args.collector_revision,collector_files=sources,boots=[],final_state=None)
    output.mkdir(parents=True); path = output/"physical.json"
    def save(): path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    save(); bridge = None; active_overlay = None; failure = None
    try:
        physical.preflight(args.reference,args.overlay,clean=False)
        bench.require(sha(args.reference) == reference_hash and sha(args.overlay) == overlay_hash, "input changed before PCAP")
        guard_previous(pynq,args.expected_loaded,args.expected_loaded_sha256)
        active_overlay = pynq.Overlay(str(bitstream),download=False)
        if args.download:
            print("PYNQ8: downloading verified dot8 overlay through Linux/PCAP",flush=True); active_overlay.download()
        bench.require(pynq.PL.bitfile_name == str(bitstream), "PYNQ loaded-overlay identity differs")
        pynq.Clocks.fclk0_mhz = 31.25
        bench.require(abs(pynq.Clocks.fclk0_mhz-31.25) <= 0.001, "physical FCLK0 differs")
        report["clock_mhz"] = float(pynq.Clocks.fclk0_mhz)
        bridge = Dot8Bridge(pynq.MMIO(0x40000000,0x40000),harts=2,caches=hardware["caches"])
        bridge.require_stopped(); bridge.load_words(words)
        for index in range(1,reference["configuration"]["boots"]+1):
            entry = dict(boot=index,status="running"); report["boots"].append(entry); save()
            collect_boot(bridge,reference,output,index,args.host_pause,args.timeout,entry); save()
            print(f"PASS: physical Pynq-Z1 dot8 boot={index} methods={len(entry['records'])} serial_bytes={entry['uart']['bytes']} "
                  "retained_RAM=65536 exact_reference_counters",flush=True)
        bench.require(physical.collector_identity() == sources and sha(args.reference) == reference_hash and sha(args.overlay) == overlay_hash,
                      "collector/input changed during physical capture")
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
    try: physical.audit(path,args.reference,args.overlay,clean=False)
    except BaseException as error:
        report.update(status="failed",error=str(error)); save(); raise
    print(f"PASS: physical dot8 package audited and CPU/DMA/compute safely STOPPED, {path}",flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference","overlay","output"): parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--collector-revision",required=True); parser.add_argument("--expected-loaded",required=True)
    parser.add_argument("--expected-loaded-sha256",required=True)
    parser.add_argument("--download",action="store_true"); parser.add_argument("--host-pause",type=float,default=0.2)
    parser.add_argument("--timeout",type=float,default=120); args = parser.parse_args()
    try: run(args)
    except (ValueError,RuntimeError,OSError) as error: parser.exit(1,f"FAIL: {error}\n")
