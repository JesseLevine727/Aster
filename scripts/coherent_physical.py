"""Read-only contracts for Phase 6 physical AsterBench v4 capture packages."""
import argparse
import hashlib
import math
from pathlib import Path
import re
import subprocess

import asterbench_coherent as bench
import coherent_overlay as overlay
import coherent_results as results
from bench_results import ROOT, sha

SCHEMA = "aster.coherent.physical.v1"
TRANSPORT = "PYNQ Linux PCAP/AXI, FPGA UART TX-to-RX serial loopback"
COLLECTOR_FILES = {"asterbench.py", "asterbench_coherent.py", "bench_results.py", "coherent_bridge.py", "coherent_elf.py",
                   "coherent_overlay.py", "coherent_physical.py", "coherent_results.py", "pynq_handoff.py",
                   "run_coherent_sim.py", "run_pynq_coherent.py"}


def collector_identity():
    return {name: sha(Path(__file__).parent/name) for name in sorted(COLLECTOR_FILES)}


def compatible(reference, hardware):
    c = reference["configuration"]
    for key, value in dict(harts=2, sync_memory=1, memory_wait=1, line_words=4, line_count=16, uart_seed=0).items():
        bench.require(type(c[key]) is int and c[key] == value, "reference is not the physical overlay configuration: "+key)
    bench.require(c["l1"] == int(hardware["caches"]), "reference/overlay caches differ")
    compatible_sources(reference["metadata"], hardware)


def compatible_sources(metadata, hardware):
    bench.require(metadata["dirty"] is False and hardware["dirty"] is False, "physical proof requires clean source")
    # Tools/workloads/docs may advance after an expensive bitstream build, but
    # the actual RTL, pinned core, constraints and build/reset recipes may not.
    def hardware_sources(sources):
        return {name: value for name, value in sources.items() if name.startswith(("rtl/", "fpga/", "vendor/picorv32/")) or
                name in {"Makefile", "scripts/pynq_handoff.py", "scripts/check_pynq_reset.py"}}
    left = hardware_sources(metadata["source_files"]); right = hardware_sources(hardware["source_files"])
    bench.require(left and left == right, "reference and bitstream hardware/build sources differ")


def preflight(reference_path, overlay_path, *, clean=False):
    # Board carries an artifact package, not a Git repository. The host must
    # additionally perform the default clean=True final audit against Git.
    bench.require(not reference_path.is_symlink() and not reference_path.with_suffix(".log").is_symlink(), "symlink reference/log")
    reference = results.load(reference_path, clean=clean)
    hardware = overlay.audit(overlay_path, clean=clean)
    compatible(reference, hardware)
    return reference, hardware


def finite(value, low, high):
    bench.require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, "invalid physical timing option")


def reference_comparison(records, reference, index):
    expected = reference["records"][index-1]
    bench.require(len(records) == len(expected), "physical/reference job count mismatch")
    deltas = []
    for actual, original in zip(records, expected):
        bench.require(all(type(actual[key]) is type(original[key]) and actual[key] == original[key]
                          for key in bench.FIELDS-bench.COUNTERS), "physical workload/configuration/result differs from reference")
        deltas.append({key: actual[key]-original[key] for key in sorted(bench.COUNTERS)})
    return dict(counter_deltas=deltas, exact_counter_match=all(value == 0 for job in deltas for value in job.values()))


def validate_faults(faults):
    bench.require(type(faults) is list and len(faults) == 2, "missing physical fault observations")
    for hart, fault in enumerate(faults):
        expected = dict(hart=hart, trapped=False, atomic_fault_valid=False, cause=0, address=0, instruction=0, pc=0)
        # Adapter IDLE latches address/opcode on EVERY legal atomic request,
        # including successful operations. Their fault meaning is conditional
        # on atomic_fault_valid. PC is live unless the hart has trapped.
        bench.require(type(fault) is dict and set(fault) == set(expected), "invalid physical fault fields")
        for key in ("address", "instruction", "pc"):
            bench.integer(fault[key], 0, bench.U32); expected[key] = fault[key]
        bench.require(results.typed_equal(fault, expected), "physical atomic fault/trap")


def validate_boot(boot, reference, directory):
    fields = {"boot", "status", "uart", "records", "ram", "before_stop", "after_stop", "elapsed_seconds", "reference_comparison"}
    bench.require(type(boot) is dict and set(boot) == fields and boot["status"] == "PASS", "incomplete physical boot")
    index = bench.integer(boot["boot"], 1, reference["configuration"]["boots"])
    data = {}
    for key, extension in (("uart", "uart"), ("ram", "ram")):
        artifact = boot[key]
        bench.require(type(artifact) is dict and set(artifact) == {"file", "bytes", "sha256"} and
                      artifact["file"] == f"boot{index}.{extension}", "invalid physical artifact")
        results.digest(artifact["sha256"]); bench.integer(artifact["bytes"], 1, 131072)
        path = directory/artifact["file"]; bench.require(not path.is_symlink(), "symlink physical artifact")
        data[key] = path.read_bytes()
        bench.require(len(data[key]) == artifact["bytes"] and hashlib.sha256(data[key]).hexdigest() == artifact["sha256"], "physical artifact changed")
    records = bench.parse_stream(data["uart"].decode("ascii"))
    bench.require(results.typed_equal(records, boot["records"]), "physical typed/raw UART mismatch")
    comparison = reference_comparison(records, reference, index)
    bench.require(results.typed_equal(comparison, boot["reference_comparison"]), "altered physical/reference comparison")
    # Until any difference is independently explained and reproduced through
    # the serial shell, fail closed rather than silently widening tolerances.
    bench.require(comparison["exact_counter_match"], "physical counters differ from direct-SoC reference")
    bench.validate_ram(data["ram"], records, reference["symbols"])
    before = boot["before_stop"]
    bench.require(type(before) is dict and set(before) == {"control", "status", "hart_status", "stop_status", "tx_bytes", "rx_bytes",
                                                         "fifo_count", "lifetime_retired", "faults"}, "invalid pre-stop observation")
    for key, value in dict(control=1, status=1, hart_status=1, stop_status=0,
                           tx_bytes=len(data["uart"]), rx_bytes=len(data["uart"]), fifo_count=0).items():
        bench.require(type(before[key]) is int and before[key] == value, "physical serial/core state mismatch: "+key)
    lifetime = before["lifetime_retired"]
    bench.require(type(lifetime) is list and len(lifetime) == 2, "missing physical per-hart observation")
    for hart in range(2):
        bench.integer(lifetime[hart])
        minimum = sum(row[f"h{hart}_retired"] for row in records)
        bench.require(lifetime[hart] >= minimum > 0 if hart < records[0]["workers"] else lifetime[hart] == 0,
                      "missing/unexpected physical hart execution")
    validate_faults(before["faults"])
    bench.require(results.typed_equal(boot["after_stop"], dict(control=0, status=0, hart_status=0, stop_status=1)), "warm stop did not acknowledge safe reset")
    finite(boot["elapsed_seconds"], 0, 180)  # includes final flush and 16K AXI RAM reads
    return records


def audit(path, reference_path, overlay_path, *, clean=True):
    reference, hardware = preflight(reference_path, overlay_path, clean=clean)
    bench.require(not path.is_symlink(), "symlink physical report")
    report = bench.json_record(path.read_text())
    fields = {"schema", "status", "board", "pynq_version", "kernel", "transport", "external_pmod_loopback",
              "previous_bitstream", "loaded_bitstream", "downloaded", "clock_mhz", "baud", "host_pause_seconds", "timeout_seconds",
              "reference_sha256", "overlay_sha256", "collector_revision", "collector_files", "boots", "final_state"}
    bench.require(type(report) is dict and set(report) == fields, "invalid physical report fields")
    for key, value in dict(schema=SCHEMA, status="complete", board="Pynq-Z1", transport=TRANSPORT,
                           external_pmod_loopback=False, clock_mhz=31.25, baud=115200,
                           reference_sha256=sha(reference_path), overlay_sha256=sha(overlay_path)).items():
        bench.require(results.typed_equal(report[key], value), "wrong physical report: "+key)
    for key in ("pynq_version", "kernel", "previous_bitstream", "loaded_bitstream"):
        bench.require(type(report[key]) is str and report[key], "missing physical platform: "+key)
    bench.require(type(report["downloaded"]) is bool, "invalid download state")
    if not report["downloaded"]:
        bench.require(report["previous_bitstream"] == report["loaded_bitstream"], "no-download changed loaded overlay")
    finite(report["host_pause_seconds"], 0, 10); finite(report["timeout_seconds"], 1, 120)
    bench.require(report["host_pause_seconds"] < report["timeout_seconds"], "invalid observation window")
    revision = report["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}", revision), "missing collector revision")
    files = report["collector_files"]
    bench.require(type(files) is dict and set(files) == COLLECTOR_FILES, "incomplete collector provenance")
    for name, fingerprint in files.items():
        results.digest(fingerprint)
        if clean:
            data = subprocess.check_output(["git", "show", f"{revision}:scripts/{name}"], cwd=ROOT)
            bench.require(hashlib.sha256(data).hexdigest() == fingerprint, "collector differs from claimed Git revision")
    boots = report["boots"]
    bench.require(type(boots) is list and all(type(b) is dict for b in boots) and len(boots) == reference["configuration"]["boots"] and
                  [b.get("boot") for b in boots] == list(range(1, len(boots)+1)), "incomplete/reordered physical boots")
    for boot in boots:
        validate_boot(boot, reference, path.parent)
        bench.require(boot["elapsed_seconds"] >= report["host_pause_seconds"], "host pause not observed")
    bench.require(results.typed_equal(report["final_state"], dict(control=0, status=0, hart_status=0, stop_status=1)), "board not safely stopped")
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {f"boot{i}.{ext}" for i in range(1, len(boots)+1) for ext in ("uart", "ram")},
                  "missing/unlisted physical evidence")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    args = parser.parse_args()
    try:
        checked = audit(args.report, args.reference, args.overlay)
        print(f"PASS: physical coherent evidence and Git provenance, {len(checked['boots'])} warm boots")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"FAIL: {error}\n")
