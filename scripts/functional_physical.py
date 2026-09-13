"""Read-only validation of full-A C/runtime and reset-lifecycle board evidence."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

import asterbench_coherent as bench
import coherent_functional as functional
import coherent_overlay as overlay
import coherent_physical as physical
from coherent_results import digest, typed_equal
from bench_results import ROOT, sha

SCHEMA = "aster.coherent.functional-physical.v1"
FILES = physical.COLLECTOR_FILES | {"coherent_functional.py", "functional_physical.py", "run_pynq_functional.py"}
STOPPED = dict(control=0, status=0, hart_status=0, stop_status=1)


def identity(): return {name: sha(Path(__file__).parent/name) for name in sorted(FILES)}


def preflight(reference_path, overlay_path, *, clean=False):
    reference = functional.load(reference_path, clean=clean); hardware = overlay.audit(overlay_path, clean=clean)
    bench.require(reference["configuration"]["l1"] == int(hardware["caches"]), "functional cache configuration differs")
    for program in reference["programs"].values(): physical.compatible_sources(program["metadata"], hardware)
    return reference, hardware


def validate_boot(boot, reference, kind, directory):
    fields = {"boot", "status", "uart", "ram", "before_stop", "after_stop", "elapsed_seconds"}
    bench.require(type(boot) is dict and set(boot) == fields and boot["status"] == "PASS", "incomplete functional boot")
    index = bench.integer(boot["boot"], 1, 2); data = {}
    for key in ("uart", "ram"):
        artifact = boot[key]
        bench.require(type(artifact) is dict and set(artifact) == {"file", "sha256", "bytes"} and
                      artifact["file"] == f"boot{index}.{key}", "invalid functional board artifact")
        digest(artifact["sha256"]); bench.integer(artifact["bytes"], 1, 65536)
        path = directory/artifact["file"]; bench.require(not path.is_symlink(), "symlink functional board artifact")
        data[key] = path.read_bytes()
        bench.require(len(data[key]) == artifact["bytes"] and hashlib.sha256(data[key]).hexdigest() == artifact["sha256"], "functional board artifact changed")
    bench.require(data["uart"] == functional.UART[kind], "functional serial differs from exact reference")
    functional.validate_ram(data["ram"], reference["programs"][kind], kind)
    before = boot["before_stop"]
    fields = set(STOPPED) | {"tx_bytes", "rx_bytes", "fifo_count", "lifetime_retired", "faults"}
    bench.require(type(before) is dict and set(before) == fields, "missing functional hardware observations")
    for key, value in dict(control=1, status=1, hart_status=3 if kind == "runtime" else 1, stop_status=0,
                           tx_bytes=len(data["uart"]), rx_bytes=len(data["uart"]), fifo_count=0).items():
        bench.require(type(before[key]) is int and before[key] == value, "functional serial/core state differs: "+key)
    lifetime = before["lifetime_retired"]
    bench.require(type(lifetime) is list and len(lifetime) == 2, "missing functional per-hart execution")
    for value in lifetime: bench.integer(value, 1001)
    physical.validate_faults(before["faults"])
    bench.require(typed_equal(boot["after_stop"], STOPPED), "functional run not safely stopped")
    physical.finite(boot["elapsed_seconds"], 0, 180)


def audit(path, reference_path, overlay_path, *, clean=True):
    reference, hardware = preflight(reference_path, overlay_path, clean=clean)
    bench.require(not path.is_symlink(), "symlink functional physical report")
    report = bench.json_record(path.read_text())
    fields = {"schema", "status", "kind", "board", "pynq_version", "kernel", "transport", "external_pmod_loopback",
              "previous_bitstream", "loaded_bitstream", "downloaded", "clock_mhz", "baud", "host_pause_seconds", "timeout_seconds",
              "reference_sha256", "overlay_sha256", "collector_revision", "collector_files", "boots", "final_state"}
    bench.require(type(report) is dict and set(report) == fields and report["kind"] in functional.PROGRAMS, "invalid functional physical envelope")
    for key, value in dict(schema=SCHEMA, status="complete", board="Pynq-Z1", transport=physical.TRANSPORT,
                           external_pmod_loopback=False, clock_mhz=31.25, baud=115200,
                           reference_sha256=sha(reference_path), overlay_sha256=sha(overlay_path)).items():
        bench.require(typed_equal(report[key], value), "invalid functional physical report: "+key)
    for key in ("pynq_version", "kernel", "previous_bitstream", "loaded_bitstream"):
        bench.require(type(report[key]) is str and report[key], "missing functional platform field")
    bench.require(type(report["downloaded"]) is bool and (report["downloaded"] or report["previous_bitstream"] == report["loaded_bitstream"]), "invalid functional download state")
    physical.finite(report["host_pause_seconds"], 0, 10); physical.finite(report["timeout_seconds"], 1, 120)
    bench.require(report["host_pause_seconds"] < report["timeout_seconds"], "invalid functional host timing")
    revision = report["collector_revision"]
    bench.require(type(revision) is str and re.fullmatch(r"[0-9a-f]{40}", revision), "invalid functional collector revision")
    files = report["collector_files"]; bench.require(type(files) is dict and set(files) == FILES, "missing functional collector dependency")
    for name, fingerprint in files.items():
        digest(fingerprint)
        if clean:
            raw = subprocess.check_output(["git", "show", f"{revision}:scripts/{name}"], cwd=ROOT)
            bench.require(hashlib.sha256(raw).hexdigest() == fingerprint, "functional collector differs from Git")
    boots = report["boots"]
    bench.require(type(boots) is list and len(boots) == 2 and all(type(b) is dict for b in boots) and
                  [b.get("boot") for b in boots] == [1, 2], "missing/reordered functional warm boots")
    for boot in boots:
        validate_boot(boot, reference, report["kind"], path.parent)
        bench.require(boot["elapsed_seconds"] >= report["host_pause_seconds"], "functional host pause not observed")
    bench.require(typed_equal(report["final_state"], STOPPED), "functional final state unsafe")
    bench.require({p.name for p in path.parent.iterdir()} == {path.name} | {f"boot{i}.{key}" for i in (1, 2) for key in ("uart", "ram")}, "unlisted functional board evidence")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("report", type=Path)
    parser.add_argument("--reference", type=Path, required=True); parser.add_argument("--overlay", type=Path, required=True)
    args = parser.parse_args()
    try: audit(args.report, args.reference, args.overlay); print("PASS: physical full-A/lifecycle evidence and Git provenance")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: parser.exit(1, f"FAIL: {error}\n")
