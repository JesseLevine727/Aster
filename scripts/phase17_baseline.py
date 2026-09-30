#!/usr/bin/env python3
"""Capture the Phase 17 retained same-top v1 baseline (P17-A5/A6).

Runs every capture in scripts/audit_phase17_baseline.py on the declared
all-engine coherent SoC (and the separately named aster_minimal CPU points)
under two memory models, once plus one determinism repeat, and writes a
self-describing bundle under docs/results/phase17/baseline-<revision>/:
raw records, oracle output, firmware hashes, configuration, toolchain,
the source hash of the committed revision, and derived metrics. The bundle is
accepted only if the read-only audit passes.

    python3 scripts/phase17_baseline.py            # capture from a clean tree
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_phase17_baseline as contract  # noqa: E402

BUILD = ROOT / "build"


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def make_vars(model: str) -> list[str]:
    spec = contract.MODELS[model]
    values = dict(contract.COMMON)
    values["SYNC_MEMORY"] = spec["SYNC_MEMORY"]
    values["MEMORY_WAIT_CYCLES"] = spec["MEMORY_WAIT_CYCLES"]
    return [f"{key}={value}" for key, value in values.items()]


def first_line(command: list[str]) -> str:
    result = run(command)
    return (result.stdout or result.stderr).strip().splitlines()[0]


def capture_one(capture: dict, model: str) -> tuple[str, str]:
    """Run one capture; return (record text, oracle/validator output)."""
    if capture["record"]:
        (BUILD / capture["record"]).unlink(missing_ok=True)  # never reuse a stale record
    result = run(["make", "--no-print-directory", *capture["make"], *make_vars(model)])
    if result.returncode != 0:
        raise SystemExit(f"FAIL: {model}/{capture['id']} make exited {result.returncode}\n"
                         f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    if capture["record"]:
        text = (BUILD / capture["record"]).read_text()
    else:
        text = "".join(line + "\n" for line in result.stdout.splitlines()
                       if line.startswith(("ASTERBENCH,", "ASTERSTOP,", "MNIST INFER ")))
    oracle = "".join(line + "\n" for line in result.stdout.splitlines()
                     if line.startswith(("PASS", "MNIST INFER ")))
    return text, oracle


def firmware_hash(capture: dict) -> tuple[str, str]:
    variable, *args = capture["firmware"]
    result = run(["make", "-s", "--no-print-directory", f"print-{variable}", *args])
    path = Path(result.stdout.strip())
    return str(path.relative_to(ROOT)), hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if not contract.working_tree_clean():
        sys.stderr.write("FAIL: commit the source tree first; the baseline binds a clean revision\n")
        return 1
    revision = contract._git("rev-parse", "HEAD").strip()
    bundle = ROOT / "docs/results/phase17" / f"baseline-{revision[:12]}"
    if bundle.exists():
        sys.stderr.write(f"FAIL: {bundle.relative_to(ROOT)} already exists (bundles are immutable)\n")
        return 1

    captures = []
    for model in contract.MODELS:
        for repeat in contract.REPEATS:
            for capture in contract.CAPTURES:
                text, oracle = capture_one(capture, model)
                record = bundle / "records" / model / f"r{repeat}" / f"{capture['id']}.record"
                log = bundle / "oracle" / model / f"r{repeat}" / f"{capture['id']}.log"
                record.parent.mkdir(parents=True, exist_ok=True)
                log.parent.mkdir(parents=True, exist_ok=True)
                record.write_text(text)
                log.write_text(oracle)
                print(f"captured {model}/r{repeat}/{capture['id']}", flush=True)

    for capture in contract.CAPTURES:
        path, digest = firmware_hash(capture)
        captures.append({"id": capture["id"], "group": capture["group"], "format": capture["format"],
                         "workload": capture["workload"], "method": capture["method"],
                         "make": capture["make"], "firmware": path, "firmware_sha256": digest})

    manifest = {
        "schema": contract.SCHEMA,
        "status": "complete",
        "captured_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"revision": revision, "dirty": False, "paths": list(contract.SOURCE_PATHS),
                   "tree_sha256": contract.source_tree_hash(revision)},
        "toolchain": {
            "gcc": first_line(["riscv32-unknown-elf-gcc", "--version"]),
            "verilator": first_line(["verilator", "--version"]),
            "python": first_line([sys.executable, "--version"]),
            "make": first_line(["make", "--version"]),
        },
        "top": "aster_coherent_soc (all engines) for coherent/mnist captures; aster_minimal for minimal captures",
        "models": contract.MODELS,
        "common": contract.COMMON,
        "captures": captures,
        "unsupported": contract.UNSUPPORTED,
        "summary": contract.summarize(bundle),
    }
    (bundle / "README.md").write_text(render_readme(manifest))
    manifest["files"] = contract.inventory(bundle)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    try:
        contract.audit(bundle, current=True)
    except (contract.AuditError, ValueError) as error:
        failed = bundle.with_name(bundle.name + "-FAILED")
        shutil.move(bundle, failed)
        sys.stderr.write(f"FAIL: audit rejected the capture ({error}); kept as {failed.name}\n")
        return 1
    print(f"PASS: wrote and audited {bundle.relative_to(ROOT)}")
    return 0


def render_readme(manifest: dict) -> str:
    s = manifest["summary"]
    lines = [
        f"# Phase 17 retained same-top v1 baseline ({manifest['source']['revision'][:12]})",
        "",
        "P17-A5/A6 evidence: every supported method of the v1 workloads on one declared",
        "all-engine `aster_coherent_soc` configuration (2 harts, L1 4×16, L2 off, DMA,",
        "DOT8, 4×4 NPU), plus the `aster_minimal` CPU workloads as a separately named",
        "data point. Each capture ran once plus one determinism repeat under two memory",
        "models. `scripts/audit_phase17_baseline.py` re-validates every record with its",
        "strict validator and independent oracle, re-checks engine counters against",
        "shapes from the workload sources, and binds the source hash; see `manifest.json`.",
        "",
        "| Memory model | Meaning |",
        "| --- | --- |",
    ]
    for name, spec in manifest["models"].items():
        lines.append(f"| `{name}` | {spec['label']} |")
    lines += ["", "Cycle counts are RTL simulation cycles at the recorded "
              f"{s['sync1']['clock_hz']:,} Hz configuration clock; they are not a timed",
              "operating point.", ""]
    for model in manifest["models"]:
        m = s[model]
        conv, red, ecg, cifar = m["conv2d"], m["reduction"], m["streaming_ecg"], m["cifar_cnn"]
        lines += [
            f"## `{model}`", "",
            "| Workload | Method | Cycles | Speedup vs scalar |",
            "| --- | --- | ---: | ---: |",
            f"| Conv2D 32×32, K=5 | coherent scalar | {conv['cycles']['conv2d_scalar_coh']:,} | 1.00× |",
            f"| | DOT8 | {conv['cycles']['conv2d_dot8']:,} | {conv['speedup_vs_coherent_scalar']['conv2d_dot8']:.2f}× |",
            f"| | NPU | {conv['cycles']['conv2d_npu']:,} | {conv['speedup_vs_coherent_scalar']['conv2d_npu']:.2f}× |",
            f"| Reduction 1024 words ×4 | 1 worker | {red['cycles_1_worker']:,} | 1.00× |",
            f"| | 2 workers | {red['cycles_2_workers']:,} | {red['speedup_2_workers']:.2f}× |",
        ]
        for method, row in m["mnist_mlp"].items():
            label = "MNIST MLP 784→32→10 (per image)" if method == "scalar" else ""
            lines.append(f"| {label} | {method} | {row['cycles_per_image']:,} | {row['speedup_vs_scalar']:.2f}× |")
        lines += [
            f"| Streaming ECG, 16 chunks × 64 | heterogeneous | {ecg['cycles']:,} | — |",
            f"| CIFAR-10 CNN, 20 images | NPU | {cifar['cycles']:,} | — |",
            "",
            f"- Conv2D NPU: {conv['npu_compute_cycles']:,} array-step cycles over 4 jobs; the array "
            f"computes in {100 * conv['npu_array_active_fraction']:.2f}% of the workload's cycles, and "
            f"{100 * conv['npu_pe_utilization_when_active']:.0f}% of its PEs do useful work when it does (N=1).",
            f"- CIFAR: the NPU is busy {100 * cifar['npu_busy_fraction']:.1f}% of the time but its array "
            f"computes in {100 * cifar['npu_array_active_fraction']:.2f}% of the cycles.",
            f"- MNIST accuracy on the 32 retained images: "
            f"{m['mnist_mlp']['npu']['correct']}/{m['mnist_mlp']['npu']['images']} "
            "(identical logits for every method).",
            "",
            "`aster_minimal` (separate top, not a direct engine speedup baseline): " +
            ", ".join(f"{name} {cycles:,}" for name, cycles in m["aster_minimal"].items()) + " cycles.",
            "",
        ]
    lines += [
        "## Unsupported combinations", "",
        "| Workload | Method | Reason |", "| --- | --- | --- |",
        *[f"| {u['workload']} | {u['method']} | {u['reason']} |" for u in manifest["unsupported"]],
        "",
        "## Notes", "",
        "- `minimal_coremark` is a one-iteration, fixed-work CRC correctness check; it is not",
        "  a CoreMark score (the port treats cycles as milliseconds and the run is far shorter",
        "  than CoreMark's ten-second rule). Dhrystone is an adapted port; raw cycles only.",
        "- MNIST emits the per-image AsterBench v9 record and one v11 summary over the summed",
        "  image windows. The v9 NPU fields describe only the last (fc2) NPU job of each image",
        "  (the audit checks them against that layer's shape); the v11 summary carries the",
        "  cumulative, requester-attributed totals, which the audit checks against the",
        "  two-layer shape oracle, the v9 cycle sum, and the model's reference checksum.",
        "- The `sync1` model is the memory timing of the physical tops; `async0` is an",
        "  idealization that no physical target implements.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
