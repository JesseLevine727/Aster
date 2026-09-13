"""Read-only contracts for physical AsterBench v6, including raw DMA diagnostics."""
import argparse
import hashlib
import math
from pathlib import Path
import re
import subprocess

import asterbench_dot8 as bench
import dot8_overlay as overlay
import dot8_results as results
from bench_results import ROOT, sha

SCHEMA = "aster.dot8.physical.v1"
TRANSPORT = "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback"
COLLECTOR_FILES = {"asterbench.py", "asterbench_coherent.py", "asterbench_dma.py", "bench_results.py",
                   "coherent_bridge.py", "coherent_elf.py", "coherent_overlay.py", "coherent_results.py",
                   "run_coherent_sim.py", "pynq_handoff.py", "dma_bridge.py", "dma_overlay.py",
                   "dma_results.py", "dma_physical.py", "run_pynq_dma.py", "asterbench_dot8.py",
                   "dot8_bridge.py", "dot8_overlay.py", "dot8_results.py", "dot8_physical.py", "run_dot8_sim.py", "run_pynq_dot8.py"}


def collector_identity():
    return {name: sha(Path(__file__).parent/name) for name in sorted(COLLECTOR_FILES)}


def compatible_sources(metadata, hardware):
    bench.require(metadata["dirty"] is False and hardware["dirty"] is False, "physical proof requires clean source")
    def select(sources):
        return {name:value for name,value in sources.items() if name.startswith(("rtl/", "fpga/", "vendor/picorv32/")) or
                name in {"Makefile", "scripts/pynq_handoff.py", "scripts/check_pynq_reset.py"}}
    left, right = select(metadata["source_files"]), select(hardware["source_files"])
    bench.require(left and left == right, "reference/bitstream hardware, constraint or build/reset source mismatch")


def compatible(reference, hardware):
    c = reference["configuration"]
    for key, value in dict(harts=2, sync_memory=1, memory_wait=1, line_words=4, line_count=16, uart_seed=0).items():
        bench.require(type(c[key]) is int and c[key] == value, "nonphysical reference configuration: "+key)
    bench.require(hardware["dma"] is True and hardware["dot8"] is True and c["l1"] == int(hardware["caches"]), "DMA/cache hardware mismatch")
    compatible_sources(reference["metadata"], hardware)


def preflight(reference_path, overlay_path, *, clean=False):
    bench.require(not reference_path.is_symlink() and not reference_path.with_suffix(".log").is_symlink(), "symlink reference/log")
    reference = results.load(reference_path, clean=clean); hardware = overlay.audit(overlay_path, clean=clean)
    compatible(reference, hardware)
    return reference, hardware


def finite(value, low, high):
    bench.require(type(value) in (int,float) and math.isfinite(value) and low <= value <= high, "invalid physical timing")


def zero_dma():
    return dict(status=0, bytes_done=0, error_code=0, counting=0, job_cycles=0, counters=[0]*14)


def stopped_state():
    return dict(control=0, status=0, hart_status=0, stop_status=1, dma=zero_dma(), dot8=zero_dot8())


def zero_dot8():
    return dict(counting=0,busy=0,counters=[0]*8)


def expected_dot8(row):
    return dict(counting=0,busy=0,counters=[row[f"h{h}_dot8_{event}"] for h in range(2) for event in bench.DOT8_EVENTS])


def reference_comparison(records, reference, index):
    expected = reference["records"][index-1]
    bench.require(len(records) == len(expected), "physical/reference method count differs")
    deltas = []
    for actual, original in zip(records, expected):
        bench.require(all(bench.typed_equal(actual[key],original[key]) for key in bench.FIELDS-bench.COUNTERS),
                      "physical configuration/output/engine diagnostics differ from reference")
        deltas.append({key:actual[key]-original[key] for key in sorted(bench.COUNTERS)})
    return dict(counter_deltas=deltas, exact_counter_match=all(v == 0 for row in deltas for v in row.values()))


def validate_faults(faults):
    bench.require(type(faults) is list and len(faults) == 2, "missing physical fault observations")
    for hart, fault in enumerate(faults):
        expected = dict(hart=hart, trapped=False, atomic_fault_valid=False, cause=0, address=0, instruction=0, pc=0)
        bench.require(type(fault) is dict and set(fault) == set(expected), "wrong fault fields")
        # Legal atomic requests latch address/instruction too; PC can be live.
        for key in ("address", "instruction", "pc"): expected[key] = bench.integer(fault[key],0,bench.U32)
        bench.require(bench.typed_equal(fault, expected), "physical fault/trap")


def validate_boot(boot, reference, directory):
    fields = {"boot", "status", "uart", "records", "ram", "before_stop", "after_stop", "elapsed_seconds", "reference_comparison"}
    bench.require(type(boot) is dict and set(boot) == fields and boot["status"] == "PASS", "incomplete physical dot8 boot")
    index = bench.integer(boot["boot"],1,reference["configuration"]["boots"]); data = {}
    for key in ("uart", "ram"):
        artifact = boot[key]
        bench.require(type(artifact) is dict and set(artifact) == {"file", "bytes", "sha256"} and
                      artifact["file"] == f"boot{index}.{key}", "invalid physical artifact")
        results.digest(artifact["sha256"]); bench.integer(artifact["bytes"],1,1048576)
        path = directory/artifact["file"]; bench.require(not path.is_symlink(), "symlink physical artifact")
        data[key] = path.read_bytes()
        bench.require(len(data[key]) == artifact["bytes"] and hashlib.sha256(data[key]).hexdigest() == artifact["sha256"],
                      "physical artifact changed")
    records = bench.parse_stream(data["uart"])
    bench.require(bench.typed_equal(records,boot["records"]), "physical typed/raw UART differs")
    comparison = reference_comparison(records,reference,index)
    bench.require(bench.typed_equal(comparison,boot["reference_comparison"]) and comparison["exact_counter_match"],
                  "physical/reference counters differ; investigate, do not widen tolerances")
    bench.validate_ram(data["ram"],records)
    before = boot["before_stop"]
    bench.require(type(before) is dict and set(before) == {"control","status","hart_status","stop_status","dma","dot8",
                  "tx_bytes","rx_bytes","fifo_count","lifetime_retired","faults"}, "invalid pre-stop observation")
    for key,value in dict(control=1,status=1,hart_status=1,stop_status=0,tx_bytes=len(data["uart"]),
                          rx_bytes=len(data["uart"]),fifo_count=0,dma=zero_dma(),dot8=expected_dot8(records[-1])).items():
        bench.require(bench.typed_equal(before[key],value), "physical serial/CPU/DMA state differs: "+key)
    lifetime = before["lifetime_retired"]
    bench.require(type(lifetime) is list and len(lifetime) == 2, "missing per-hart lifetime counters")
    bench.integer(lifetime[0]); bench.integer(lifetime[1])
    bench.require(lifetime[0] >= sum(row["h0_retired"] for row in records) > 0 and lifetime[1] == 0,
                  "primary did not execute or latency benchmark released secondary")
    validate_faults(before["faults"])
    bench.require(bench.typed_equal(boot["after_stop"],stopped_state()), "CPU/DMA/compute did not safely reset after drain/flush")
    finite(boot["elapsed_seconds"],0,300)
    return records


def audit(path, reference_path, overlay_path, *, clean=True):
    reference,hardware = preflight(reference_path,overlay_path,clean=clean)
    bench.require(not path.is_symlink(), "symlink physical report")
    report = bench.json_record(path.read_text())
    fields = {"schema","status","board","pynq_version","kernel","transport","external_pmod_loopback",
              "previous_bitstream","previous_bitstream_sha256","previous_state","loaded_bitstream","downloaded",
              "clock_mhz","baud","host_pause_seconds","timeout_seconds","reference_sha256","overlay_sha256",
              "collector_revision","collector_files","boots","final_state"}
    bench.require(type(report) is dict and set(report) == fields, "invalid physical report fields")
    for key,value in dict(schema=SCHEMA,status="complete",board="Pynq-Z1",transport=TRANSPORT,external_pmod_loopback=False,
                          clock_mhz=31.25,baud=115200,reference_sha256=sha(reference_path),overlay_sha256=sha(overlay_path)).items():
        bench.require(bench.typed_equal(report[key],value), "wrong physical report: "+key)
    for key in ("pynq_version","kernel","previous_bitstream","loaded_bitstream"):
        bench.require(type(report[key]) is str and report[key], "missing physical platform field")
    for key in ("previous_bitstream","loaded_bitstream"): bench.require(Path(report[key]).is_absolute(), "invalid overlay path")
    results.digest(report["previous_bitstream_sha256"])
    bench.require(bench.typed_equal(report["previous_state"],dict(control=0,status=0,hart_status=0,stop_status=1)),
                  "previous overlay was not safely idle before deployment")
    bench.require(type(report["downloaded"]) is bool, "invalid download state")
    if not report["downloaded"]:
        bench.require(report["previous_bitstream"] == report["loaded_bitstream"] and
                      report["previous_bitstream_sha256"] == hardware["files"]["aster_linux.bit"]["sha256"],
                      "no-download changed selected bitstream")
    finite(report["host_pause_seconds"],0,10); finite(report["timeout_seconds"],1,120)
    bench.require(report["host_pause_seconds"] < report["timeout_seconds"], "invalid observation window")
    revision = report["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}",revision), "missing collector revision")
    files = report["collector_files"]
    bench.require(type(files) is dict and set(files) == COLLECTOR_FILES, "incomplete collector provenance")
    for name,value in files.items():
        results.digest(value)
        if clean:
            raw = subprocess.check_output(["git","show",f"{revision}:scripts/{name}"],cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == value, "collector differs from committed source")
    boots = report["boots"]
    bench.require(type(boots) is list and len(boots) == reference["configuration"]["boots"] and
                  all(type(b) is dict for b in boots) and [b.get("boot") for b in boots] == list(range(1,len(boots)+1)),
                  "incomplete/reordered physical boots")
    for boot in boots:
        validate_boot(boot,reference,path.parent)
        bench.require(boot["elapsed_seconds"] >= report["host_pause_seconds"], "host pause was not observed")
    bench.require(bench.typed_equal(report["final_state"],stopped_state()), "physical CPU/DMA/compute not safely STOPPED")
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {f"boot{i}.{ext}" for i in range(1,len(boots)+1) for ext in ("uart","ram")},
                  "missing/extra physical artifacts")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("report",type=Path)
    parser.add_argument("--reference",type=Path,required=True); parser.add_argument("--overlay",type=Path,required=True)
    args = parser.parse_args()
    try:
        report = audit(args.report,args.reference,args.overlay)
        print(f"PASS: physical dot8 raw UART/RAM/counters/provenance, {len(report['boots'])} warm boots")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")

