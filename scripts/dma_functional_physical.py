"""Read-only physical DMA runtime/code-publication evidence, not latency results."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

import asterbench_dma as bench
import dma_functional as functional
import dma_functional_results as reference_tools
import dma_overlay as overlay
import dma_physical as physical
from bench_results import ROOT, sha

SCHEMA = "aster.dma.functional-physical.v1"
FILES = physical.COLLECTOR_FILES | {"dma_functional.py","dma_functional_results.py",
                                    "dma_functional_physical.py","run_pynq_dma_functional.py"}


def identity(): return {name:sha(Path(__file__).parent/name) for name in sorted(FILES)}


def preflight(reference_path,overlay_path,*,clean=False):
    reference = reference_tools.load(reference_path,clean=clean); hardware = overlay.audit(overlay_path,clean=clean)
    bench.require(hardware["dma"] is True and reference["configuration"]["l1"] == int(hardware["caches"]),
                  "functional DMA/cache hardware differs")
    for program in reference["programs"].values(): physical.compatible_sources(program["metadata"],hardware)
    return reference,hardware


def validate_boot(boot,reference,kind,directory):
    fields = {"boot","status","uart","ram","before_stop","after_stop","elapsed_seconds","ram_values"}
    bench.require(type(boot) is dict and set(boot) == fields and boot["status"] == "PASS", "incomplete functional DMA boot")
    index = bench.integer(boot["boot"],1,2); data = {}
    for key in ("uart","ram"):
        item = boot[key]
        bench.require(type(item) is dict and set(item) == {"file","bytes","sha256"} and item["file"] == f"boot{index}.{key}",
                      "invalid functional physical artifact")
        physical.results.digest(item["sha256"]); bench.integer(item["bytes"],1,65536)
        path = directory/item["file"]; bench.require(not path.is_symlink(), "symlink functional physical artifact")
        raw = path.read_bytes(); data[key] = raw
        bench.require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"], "functional physical bytes changed")
    bench.require(data["uart"] == functional.UART[kind], "actual functional serial differs")
    values = functional.validate_ram(data["ram"],reference["programs"][kind],kind,reference["configuration"]["l1"])
    bench.require(bench.typed_equal(boot["ram_values"],values), "functional report differs from actual stopped RAM")
    before = boot["before_stop"]
    bench.require(type(before) is dict and set(before) == set(physical.stopped_state()) | {
                  "tx_bytes","rx_bytes","fifo_count","lifetime_retired","faults"}, "missing functional hardware observations")
    for key,value in dict(control=1,status=1,hart_status=3,stop_status=0,tx_bytes=len(data["uart"]),rx_bytes=len(data["uart"]),fifo_count=0).items():
        bench.require(bench.typed_equal(before[key],value), "functional serial/hart state differs: "+key)
    functional.validate_host_dma(before["dma"],values,kind)
    lifetime = before["lifetime_retired"]
    bench.require(type(lifetime) is list and len(lifetime) == 2, "missing functional per-hart execution")
    for h,value in enumerate(lifetime): bench.integer(value,max(1001,values["cpu"][h][1]))
    physical.validate_faults(before["faults"])
    bench.require(bench.typed_equal(boot["after_stop"],physical.stopped_state()), "functional DMA/CPU not safely stopped")
    physical.finite(boot["elapsed_seconds"],0,300)
    return values


def audit(path,reference_path,overlay_path,*,clean=True):
    reference,hardware = preflight(reference_path,overlay_path,clean=clean)
    bench.require(not path.is_symlink(), "symlink functional physical report")
    report = bench.json_record(path.read_text())
    fields = {"schema","status","kind","board","pynq_version","kernel","transport","external_pmod_loopback",
              "previous_bitstream","previous_bitstream_sha256","previous_state","loaded_bitstream","downloaded",
              "clock_mhz","baud","host_pause_seconds","timeout_seconds","reference_sha256","overlay_sha256",
              "collector_revision","collector_files","boots","final_state"}
    bench.require(type(report) is dict and set(report) == fields and report["kind"] in functional.PROGRAMS, "invalid functional physical envelope")
    for key,value in dict(schema=SCHEMA,status="complete",board="Pynq-Z1",transport=physical.TRANSPORT,external_pmod_loopback=False,
        clock_mhz=31.25,baud=115200,reference_sha256=sha(reference_path),overlay_sha256=sha(overlay_path)).items():
        bench.require(bench.typed_equal(report[key],value), "wrong functional physical report: "+key)
    for key in ("pynq_version","kernel","previous_bitstream","loaded_bitstream"):
        bench.require(type(report[key]) is str and report[key], "missing functional physical platform field")
    for key in ("previous_bitstream","loaded_bitstream"): bench.require(Path(report[key]).is_absolute(), "relative physical overlay path")
    physical.results.digest(report["previous_bitstream_sha256"])
    bench.require(bench.typed_equal(report["previous_state"],dict(control=0,status=0,hart_status=0,stop_status=1)), "previous Aster not idle")
    bench.require(type(report["downloaded"]) is bool, "invalid functional download state")
    if not report["downloaded"]:
        bench.require(report["previous_bitstream"] == report["loaded_bitstream"] and
                      report["previous_bitstream_sha256"] == hardware["files"]["aster_linux.bit"]["sha256"], "no-download changed functional overlay")
    physical.finite(report["host_pause_seconds"],0,10); physical.finite(report["timeout_seconds"],1,120)
    bench.require(report["host_pause_seconds"] < report["timeout_seconds"], "invalid functional host observation window")
    revision = report["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}",revision), "invalid functional collector revision")
    files = report["collector_files"]; bench.require(type(files) is dict and set(files) == FILES, "missing functional collector source")
    for name,value in files.items():
        physical.results.digest(value)
        if clean:
            raw = subprocess.check_output(["git","show",f"{revision}:scripts/{name}"],cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == value, "functional collector differs from Git")
    boots = report["boots"]
    bench.require(type(boots) is list and len(boots) == 2 and all(type(b) is dict for b in boots) and [b.get("boot") for b in boots] == [1,2],
                  "missing/reordered functional warm boots")
    for boot in boots:
        validate_boot(boot,reference,report["kind"],path.parent)
        bench.require(boot["elapsed_seconds"] >= report["host_pause_seconds"], "functional host pause not observed")
    bench.require(bench.typed_equal(report["final_state"],physical.stopped_state()), "functional final CPU/DMA state unsafe")
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {f"boot{i}.{ext}" for i in (1,2) for ext in ("uart","ram")},
                  "unlisted functional physical artifact")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("report",type=Path)
    parser.add_argument("--reference",type=Path,required=True); parser.add_argument("--overlay",type=Path,required=True)
    args = parser.parse_args()
    try: audit(args.report,args.reference,args.overlay); print("PASS: physical DMA runtime/code evidence and Git provenance")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
