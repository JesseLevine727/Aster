"""Read-only physical two-hart DOT8 functional proof, separate from benchmarks."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

import asterbench_dot8 as bench
import dot8_functional as functional
import dot8_functional_results as references
import dot8_overlay as overlay
import dot8_physical as physical
from bench_results import ROOT, sha

SCHEMA = "aster.dot8.functional-physical.v1"
FILES = physical.COLLECTOR_FILES | {"dot8_functional.py","dot8_functional_results.py","dot8_functional_physical.py","run_pynq_dot8_functional.py"}


def identity(): return {name:sha(Path(__file__).parent/name) for name in sorted(FILES)}


def preflight(reference_path,overlay_path,*,clean=False):
    reference = references.load(reference_path,clean=clean); hardware = overlay.audit(overlay_path,clean=clean)
    bench.require(hardware["dma"] is True and hardware["dot8"] is True and reference["configuration"]["l1"] == int(hardware["caches"]),"functional hardware/cache differs")
    physical.compatible_sources(reference["metadata"],hardware)
    return reference,hardware


def comparison(values,reference,index):
    expected = reference["observations"][index-1]["counts"]
    bench.require(type(expected) is list and len(expected) == 50,"missing reference functional counters")
    deltas = [a-b for a,b in zip(values["counts"],expected)]
    return dict(counter_deltas=deltas,exact_counter_match=all(v == 0 for v in deltas))


def validate_boot(boot,reference,directory):
    fields = {"boot","status","uart","ram","before_stop","after_stop","elapsed_seconds","ram_values","reference_comparison"}
    bench.require(type(boot) is dict and set(boot) == fields and boot["status"] == "PASS","incomplete physical functional boot")
    index = bench.integer(boot["boot"],1,2); data = {}
    for key in ("uart","ram"):
        item = boot[key]
        bench.require(type(item) is dict and set(item) == {"file","bytes","sha256"} and item["file"] == f"boot{index}.{key}","invalid physical functional artifact")
        physical.results.digest(item["sha256"]); bench.integer(item["bytes"],1,65536)
        path = directory/item["file"]; bench.require(not path.is_symlink(),"symlink physical functional artifact")
        raw = path.read_bytes(); data[key] = raw
        bench.require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],"physical functional artifact changed")
    bench.require(data["uart"] == functional.UART,"physical functional UART differs")
    values = functional.validate_ram(data["ram"],reference["programs"]["dot8_runtime"],reference["configuration"]["l1"])
    bench.require(bench.typed_equal(boot["ram_values"],values),"physical functional envelope differs from stopped RAM")
    compared = comparison(values,reference,index)
    bench.require(bench.typed_equal(compared,boot["reference_comparison"]) and compared["exact_counter_match"],"physical functional counters differ from reference; investigate without widening tolerances")
    before = boot["before_stop"]
    bench.require(type(before) is dict and set(before) == set(physical.stopped_state())|{"tx_bytes","rx_bytes","fifo_count","lifetime_retired","faults"},"incomplete functional hardware observations")
    for key,value in dict(control=1,status=1,hart_status=3,stop_status=0,tx_bytes=len(functional.UART),rx_bytes=len(functional.UART),fifo_count=0,
                          dot8=functional.host_dot8(values)).items():
        bench.require(bench.typed_equal(before[key],value),"functional live hardware/RAM differs: "+key)
    try:
        functional.validate_host_dma(before["dma"], values)
    except (KeyError, TypeError):
        raise ValueError("functional live hardware/RAM differs: dma")
    lifetime = before["lifetime_retired"]
    bench.require(type(lifetime) is list and len(lifetime) == 2,"missing per-hart physical execution")
    for h,value in enumerate(lifetime): bench.integer(value,values["cpu"][h][1])
    physical.validate_faults(before["faults"])
    bench.require(bench.typed_equal(boot["after_stop"],physical.stopped_state()),"functional CPU/DMA/compute did not drain and stop")
    physical.finite(boot["elapsed_seconds"],0,300)
    return values


def audit(path,reference_path,overlay_path,*,clean=True):
    reference,hardware = preflight(reference_path,overlay_path,clean=clean)
    bench.require(not path.is_symlink(),"symlink physical functional report")
    m = bench.json_record(path.read_text())
    fields = {"schema","status","board","pynq_version","kernel","transport","external_pmod_loopback","previous_bitstream",
              "previous_bitstream_sha256","previous_state","loaded_bitstream","downloaded","clock_mhz","baud","host_pause_seconds",
              "timeout_seconds","reference_sha256","overlay_sha256","collector_revision","collector_files","boots","final_state"}
    bench.require(type(m) is dict and set(m) == fields,"invalid physical functional envelope")
    for key,value in dict(schema=SCHEMA,status="complete",board="Pynq-Z1",transport=physical.TRANSPORT,external_pmod_loopback=False,
                          clock_mhz=31.25,baud=115200,reference_sha256=sha(reference_path),overlay_sha256=sha(overlay_path)).items():
        bench.require(bench.typed_equal(m[key],value),"wrong physical functional identity: "+key)
    for key in ("pynq_version","kernel","previous_bitstream","loaded_bitstream"):
        bench.require(type(m[key]) is str and m[key],"missing physical functional platform field")
    for key in ("previous_bitstream","loaded_bitstream"): bench.require(Path(m[key]).is_absolute(),"relative physical overlay path")
    physical.results.digest(m["previous_bitstream_sha256"])
    bench.require(bench.typed_equal(m["previous_state"],dict(control=0,status=0,hart_status=0,stop_status=1)),"prior overlay was not idle")
    bench.require(type(m["downloaded"]) is bool,"invalid physical functional download state")
    if not m["downloaded"]:
        bench.require(m["previous_bitstream"] == m["loaded_bitstream"] and m["previous_bitstream_sha256"] == hardware["files"]["aster_linux.bit"]["sha256"],"no-download changed selected functional image")
    physical.finite(m["host_pause_seconds"],0,10); physical.finite(m["timeout_seconds"],1,120)
    bench.require(m["host_pause_seconds"] < m["timeout_seconds"],"invalid functional observation window")
    revision = m["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}",revision),"invalid functional collector revision")
    files = m["collector_files"]; bench.require(type(files) is dict and set(files) == FILES,"incomplete functional collector closure")
    for name,value in files.items():
        physical.results.digest(value)
        if clean:
            raw = subprocess.check_output(["git","show",f"{revision}:scripts/{name}"],cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == value,"functional collector differs from Git")
    boots = m["boots"]
    bench.require(type(boots) is list and len(boots) == 2 and all(type(b) is dict for b in boots) and [b.get("boot") for b in boots] == [1,2],"missing/reordered functional warm boots")
    for boot in boots:
        validate_boot(boot,reference,path.parent)
        bench.require(boot["elapsed_seconds"] >= m["host_pause_seconds"],"functional host pause not observed")
    bench.require(bench.typed_equal(m["final_state"],physical.stopped_state()),"functional final CPU/DMA/compute unsafe")
    bench.require({p.name for p in path.parent.iterdir()} == {path.name}|{f"boot{i}.{ext}" for i in (1,2) for ext in ("uart","ram")},"unlisted physical functional artifacts")
    return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("report",type=Path)
    parser.add_argument("--reference",type=Path,required=True); parser.add_argument("--overlay",type=Path,required=True); args = parser.parse_args()
    try: audit(args.report,args.reference,args.overlay); print("PASS: physical DOT8 functional UART/RAM/events and Git audit")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
