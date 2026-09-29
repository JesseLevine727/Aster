#!/usr/bin/env python3
"""Read-only audit of the Phase 17 retained same-top v1 baseline (P17-A5/A6).

The bundle binds raw records, firmware hashes, configuration, toolchain and the
source state of one revision. The audit re-validates every record with its
strict validator and independent oracle, requires every capture in a memory
model to share one declared SoC configuration, re-checks requester-attributed
engine counters against shapes taken from the workload sources, requires each
determinism repeat to be byte-identical, recomputes the derived summary, and
binds the recorded source hash to the recorded revision (and, with --current,
to the working tree).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asterbench_v9 as v9  # noqa: E402
import asterbench_v10 as v10  # noqa: E402
import asterbench_v11 as v11  # noqa: E402
import cifar_reference  # noqa: E402
import phase17_conv_baseline as conv_matrix  # noqa: E402
import workload_reference as reference  # noqa: E402

SCHEMA = "aster.phase17.baseline.v1"
SOURCE_PATHS = ("Makefile", "rtl", "software", "vendor", "verification", "scripts", "fpga", "asic")
MODELS = {
    "sync1": {"SYNC_MEMORY": 1, "MEMORY_WAIT_CYCLES": 1,
              "label": "physical target: synchronous memory, one wait cycle (PYNQ-Z1 and SKY130 tops)"},
    "async0": {"SYNC_MEMORY": 0, "MEMORY_WAIT_CYCLES": 0,
               "label": "idealization: asynchronous zero-wait memory (no physical target)"},
}
REPEATS = (1, 2)
# One declared all-engine coherent SoC; the Makefile defaults, passed explicitly.
COMMON = {"HART_COUNT": 2, "ENABLE_L1": 1, "L1_LINE_WORDS": 4, "L1_LINE_COUNT": 16,
          "ENABLE_L2": 0, "NPU_ROWS": 4, "NPU_COLS": 4}
MNIST_METHODS = ("scalar", "multicore", "dot8", "npu")
MNIST_MODEL = ROOT / "docs/results/phase11/model.json"
CIFAR_MODEL = ROOT / "docs/results/workloads/cifar_model.json"


def _capture(cid, group, fmt, workload, method, make, record, firmware):
    return {"id": cid, "group": group, "format": fmt, "workload": workload, "method": method,
            "make": make, "record": record, "firmware": firmware}


CAPTURES = [
    _capture("reduce_scalar", "coherent", "v11", "reduction", "scalar (1 worker)",
             ["reduce", "REDUCE_WORKERS=1"], "reduce_scalar.record",
             ["REDUCE_HEX", "REDUCE_WORKERS=1"]),
    _capture("reduce_parallel", "coherent", "v11", "reduction", "multicore (2 workers)",
             ["reduce", "REDUCE_WORKERS=2"], "reduce_parallel.record",
             ["REDUCE_HEX", "REDUCE_WORKERS=2"]),
    _capture("conv2d_scalar_coh", "coherent", "v11", "conv2d", "scalar",
             ["conv-engine", "CONV_ENGINE=scalar_coh"], "conv2d_scalar_coh.record",
             ["CONV_HEX", "CONV_ENGINE=scalar_coh"]),
    _capture("conv2d_dot8", "coherent", "v11", "conv2d", "dot8",
             ["conv-engine", "CONV_ENGINE=dot8"], "conv2d_dot8.record",
             ["CONV_HEX", "CONV_ENGINE=dot8"]),
    _capture("conv2d_npu", "coherent", "v11", "conv2d", "npu",
             ["conv-engine", "CONV_ENGINE=npu"], "conv2d_npu.record",
             ["CONV_HEX", "CONV_ENGINE=npu"]),
    _capture("streaming_ecg", "coherent", "v11", "ecg", "heterogeneous (CPU+DMA+DOT8+NPU)",
             ["ecg"], "streaming_ecg.record", ["ECG_HEX"]),
    _capture("cifar_cnn", "coherent", "v11", "cifar", "npu",
             ["cifar"], "cifar_cnn.record", ["CIFAR_HEX"]),
] + [
    _capture(f"mnist_{method}", "mnist", "v9", "mnist_mlp", method,
             ["phase11-infer", f"PHASE11_METHOD={method}"], None,
             ["PHASE11_HEX", f"PHASE11_METHOD={method}"])
    for method in MNIST_METHODS
] + [
    _capture(f"minimal_{name}", "minimal", "v10", name, "scalar (aster_minimal)",
             ["workload", f"WORKLOAD={name}"], f"workload_{name}.record",
             ["WORKLOAD_HEX", f"WORKLOAD={name}"])
    for name in ("strided", "sort_search", "fft", "conv2d")
] + [
    _capture("minimal_coremark", "minimal", "v10", "coremark", "scalar (aster_minimal)",
             ["coremark"], "coremark.record", ["COREMARK_HEX"]),
    _capture("minimal_dhrystone", "minimal", "v10", "dhrystone", "scalar (aster_minimal)",
             ["dhrystone"], "dhrystone.record", ["DHRY_HEX"]),
]

UNSUPPORTED = [
    {"workload": "conv2d", "method": "multicore",
     "reason": "v1 has no two-worker Conv2D firmware"},
    {"workload": "reduction", "method": "dot8/npu",
     "reason": "an integer sum has no dot-product or GEMM mapping"},
    {"workload": "ecg", "method": "scalar/dot8/npu-only",
     "reason": "v1 ECG is one fixed heterogeneous pipeline; per-engine variants are not implemented"},
    {"workload": "cifar", "method": "scalar/multicore/dot8",
     "reason": "v1 CIFAR firmware is NPU-only"},
]

# NPU GEMM shapes (M, N, K) per job, taken from the workload sources.
ECG_CLASSIFIER = (3, 1, 4)          # workload_ecg.c: ECG_CLASSES x 1 x ECG_FEATURES
CIFAR_LAYERS = ((196, 16, 27),      # workload_cifar.c: conv1 C1_M x CONV1_OUT x CONV1_K
                (25, 32, 144),      # conv2 C2_M x CONV2_OUT x CONV2_K
                (10, 1, 128))       # fc FC_OUT x 1 x FC_IN
MNIST_LAST_LAYER = (10, 1, 32)      # mnist_infer.c: fc2 FC2_OUT x 1 x FC2_IN
MNIST_LAYERS = ((32, 1, 784),      # fc1 FC1_OUT x 1 x FC1_IN
                MNIST_LAST_LAYER)


class AuditError(ValueError):
    """The retained baseline violates its contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gemm_npu_oracle(m: int, n: int, k: int, rows: int = 4, cols: int = 4) -> dict[str, int]:
    """Per-job NPU counters for one GEMM (one read per operand byte, one write per result byte)."""
    tiles = reads = 0
    for row in range(0, m, rows):
        for col in range(0, n, cols):
            tiles += 1
            reads += k * (min(rows, m - row) + min(cols, n - col))
    return {"npu_tiles": tiles, "npu_bytes_read": reads,
            "npu_bytes_written": 4 * m * n, "npu_compute_cycles": k * tiles}


def sum_oracle(shapes, jobs_each: int) -> dict[str, int]:
    total = {"npu_tiles": 0, "npu_bytes_read": 0, "npu_bytes_written": 0, "npu_compute_cycles": 0}
    for shape in shapes:
        for key, value in gemm_npu_oracle(*shape).items():
            total[key] += value * jobs_each
    return total


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def source_tree_hash(revision: str) -> str:
    listing = _git("ls-tree", "-r", revision, "--", *SOURCE_PATHS)
    return hashlib.sha256(listing.encode()).hexdigest()


def working_tree_clean() -> bool:
    return _git("status", "--porcelain", "--", *SOURCE_PATHS).strip() == ""


def fields_of(line: str) -> dict[str, str]:
    body = line.strip()[len("ASTERBENCH,"):]
    return dict(token.split("=", 1) for token in body.split(","))


def mnist_checksum(mnist_model: dict) -> int:
    """The v11 summary checksum, recomputed from the model's reference outputs."""
    checksum = 0
    test = mnist_model["test"]
    for logits, best in zip(test["reference_logits"], test["reference_classes"]):
        for value in logits:
            checksum = ((checksum * 33) ^ (value & 0xFF)) & 0xFFFFFFFF
        checksum = ((checksum * 33) ^ best) & 0xFFFFFFFF
    return checksum


def check_model_config(model: str, sync_memory: int, memory_wait: int | None, where: str) -> None:
    spec = MODELS[model]
    require(sync_memory == spec["SYNC_MEMORY"],
            f"{where}: sync_memory={sync_memory} does not match memory model {model}")
    if memory_wait is not None:
        require(memory_wait == spec["MEMORY_WAIT_CYCLES"],
                f"{where}: memory_wait={memory_wait} does not match memory model {model}")


def validate_capture(capture: dict, text: str, model: str, mnist_model: dict,
                     cifar_model: dict) -> dict[str, object]:
    where = f"{model}/{capture['id']}"
    if capture["format"] == "v11":
        record = v11.validate_line(text, name=capture["id"])
        if capture["id"] == "cifar_cnn":
            require(record["checksum"] == cifar_reference.record_checksum(cifar_model),
                    f"{where}: checksum differs from the independent CIFAR artifact")
        else:
            reference.verify(record, capture["id"])
        check_model_config(model, record["sync_memory"], record["memory_wait"], where)
        require(record["harts"] == COMMON["HART_COUNT"] and record["l1"] == COMMON["ENABLE_L1"] and
                record["line_words"] == COMMON["L1_LINE_WORDS"] and
                record["line_count"] == COMMON["L1_LINE_COUNT"],
                f"{where}: SoC top/cache configuration differs from the declared baseline")
        return record
    if capture["format"] == "v9":
        method = capture["method"]
        lines = [line for line in text.splitlines(True) if line.strip()]
        results = v9.validate_stream(lines, mnist_model, method=method, complete=True)
        records = [fields_of(line) for line in lines if line.startswith("ASTERBENCH,version=9,")]
        for fields in records:
            check_model_config(model, int(fields["sync_memory"]), None, where)
            require(int(fields["l1"]) == COMMON["ENABLE_L1"], f"{where}: L1 setting differs")
            if method == "npu":
                # v9 reports the NPU status of the last (fc2) job only; check that
                # documented semantics against the fc2 shape instead of treating it as a total.
                expected = gemm_npu_oracle(*MNIST_LAST_LAYER)
                for key, value in expected.items():
                    actual = int(fields[key], 16) if fields[key].startswith("0x") else int(fields[key])
                    require(actual == value,
                            f"{where}: v9 {key}={actual} is not the last-layer value {value}")
        clocks = {int(fields["clock_hz"]) for fields in records}
        require(len(clocks) == 1, f"{where}: records disagree on clock_hz")
        summaries = [line for line in lines if line.startswith("ASTERBENCH,version=11,")]
        require(len(summaries) == 1, f"{where}: expected exactly one v11 summary record")
        summary = v11.validate_line(summaries[0], name=f"mnist_mlp_{method}", category="ml")
        check_model_config(model, summary["sync_memory"], summary["memory_wait"], where)
        cycles_total = sum(r["h0_cycles"] for r in results)
        require(summary["cycles"] == cycles_total,
                f"{where}: v11 cycles {summary['cycles']} != sum of the v9 image windows {cycles_total}")
        require(summary["checksum"] == mnist_checksum(mnist_model),
                f"{where}: v11 checksum differs from the model's reference outputs")
        require(summary["iterations"] == len(results) and summary["size"] == 784 and summary["param"] == 32,
                f"{where}: v11 summary shape fields are wrong")
        require(summary["workers"] == (2 if method == "multicore" else 1),
                f"{where}: v11 worker count does not match the method")
        require(summary["dma_jobs"] == 0 and summary["h1_dot8_accept"] == 0,
                f"{where}: unexpected DMA or hart-1 DOT8 activity")
        require((summary["h0_dot8_accept"] > 0) == (method == "dot8"),
                f"{where}: DOT8 activity does not match the method")
        if method == "npu":
            require(summary["npu_jobs"] == len(MNIST_LAYERS) * len(results),
                    f"{where}: expected two NPU jobs per image")
            for key, value in sum_oracle(MNIST_LAYERS, len(results)).items():
                require(summary[key] == value,
                        f"{where}: v11 {key}={summary[key]} differs from oracle {value}")
        else:
            require(summary["npu_jobs"] == 0, f"{where}: non-NPU method reported NPU jobs")
        return {"clock_hz": clocks.pop(), "images": len(results),
                "correct": sum(1 for r in results if r["class"] == r["label"]),
                "h0_cycles_total": cycles_total,
                "v11": {key: summary[key] for key in (
                    "cycles", "retired", "npu_jobs", "npu_job_cycles", "npu_compute_cycles")}}
    record = v10.validate_line(text, name=capture["workload"])
    if capture["workload"] != "coremark":  # CoreMark self-checks its CRCs in firmware
        reference.verify(record, capture["workload"])
    # The v10 validator returns only the workload fields; read the (already
    # validated) configuration fields from the raw record.
    raw = fields_of(text)
    record = {**record, **{key: int(raw[key]) for key in (
        "clock_hz", "l1", "sync_memory", "line_words", "line_count", "memory_wait")}}
    check_model_config(model, record["sync_memory"], record["memory_wait"], where)
    require(record["l1"] == COMMON["ENABLE_L1"] and record["line_words"] == COMMON["L1_LINE_WORDS"] and
            record["line_count"] == COMMON["L1_LINE_COUNT"],
            f"{where}: aster_minimal cache configuration differs from the declared baseline")
    return record


def check_engine_counters(model: str, parsed: dict[str, dict], texts: dict[str, str]) -> dict:
    """Workload-derived counter oracles for one memory model and repeat."""
    for cid in ("reduce_scalar", "reduce_parallel"):
        record = parsed[cid]
        require(record["dma_jobs"] == 0 and record["npu_jobs"] == 0 and
                record["h0_dot8_accept"] == record["h1_dot8_accept"] == 0,
                f"{model}/{cid}: a CPU reduction reported engine activity")
    require(parsed["reduce_scalar"]["workers"] == 1 and parsed["reduce_parallel"]["workers"] == 2,
            f"{model}: reduction worker counts are wrong")

    conv = conv_matrix.audit_records({cid: texts[cid] for cid in conv_matrix.ENGINES})

    ecg = parsed["streaming_ecg"]
    chunks, chunk = ecg["iterations"], ecg["size"]
    require(ecg["dma_jobs"] == ecg["dma_completed_jobs"] == chunks and ecg["dma_bytes"] == chunks * chunk,
            f"{model}/streaming_ecg: DMA totals are not one {chunk}-byte job per chunk")
    require(ecg["npu_jobs"] == chunks, f"{model}/streaming_ecg: expected one NPU job per chunk")
    for key, value in sum_oracle([ECG_CLASSIFIER], chunks).items():
        require(ecg[key] == value, f"{model}/streaming_ecg: {key}={ecg[key]} differs from oracle {value}")
    require(ecg["h0_dot8_accept"] == 0 and ecg["h1_dot8_accept"] > 0,
            f"{model}/streaming_ecg: DOT8 FIR must run on hart 1 only")

    cifar = parsed["cifar_cnn"]
    images = cifar["iterations"]
    require(cifar["dma_jobs"] == 0 and cifar["npu_jobs"] == len(CIFAR_LAYERS) * images,
            f"{model}/cifar_cnn: expected three NPU jobs per image and no DMA")
    for key, value in sum_oracle(CIFAR_LAYERS, images).items():
        require(cifar[key] == value, f"{model}/cifar_cnn: {key}={cifar[key]} differs from oracle {value}")
    return conv


def check_clocks(model: str, parsed: dict[str, dict]) -> int:
    clocks = {cid: record["clock_hz"] for cid, record in parsed.items()}
    require(len(set(clocks.values())) == 1, f"{model}: captures disagree on clock_hz: {clocks}")
    return next(iter(clocks.values()))


def summarize(bundle: Path) -> dict:
    """Derived metrics from the first repeat of every capture."""
    mnist_model = v9.load_model(MNIST_MODEL)
    cifar_model = cifar_reference.load_model(CIFAR_MODEL)
    summary: dict[str, dict] = {}
    for model in MODELS:
        texts = {c["id"]: (bundle / "records" / model / "r1" / f"{c['id']}.record").read_text()
                 for c in CAPTURES}
        p = {c["id"]: validate_capture(c, texts[c["id"]], model, mnist_model, cifar_model)
             for c in CAPTURES}
        conv = conv_matrix.audit_records({cid: texts[cid] for cid in conv_matrix.ENGINES})
        scalar_mnist = p["mnist_scalar"]["h0_cycles_total"]
        summary[model] = {
            "clock_hz": p["reduce_scalar"]["clock_hz"],
            "conv2d": {
                "cycles": conv["cycles"],
                "speedup_vs_coherent_scalar": {
                    cid: round(conv["cycles"]["conv2d_scalar_coh"] / cycles, 4)
                    for cid, cycles in conv["cycles"].items()},
                "npu_compute_cycles": conv["npu_cumulative_compute_cycles"],
                "npu_array_active_fraction": round(conv["npu_array_active_fraction"], 6),
                "npu_pe_utilization_when_active": round(conv["npu_pe_utilization_when_active"], 4),
            },
            "reduction": {
                "cycles_1_worker": p["reduce_scalar"]["cycles"],
                "cycles_2_workers": p["reduce_parallel"]["cycles"],
                "speedup_2_workers": round(p["reduce_scalar"]["cycles"] / p["reduce_parallel"]["cycles"], 4),
            },
            "mnist_mlp": {
                method: {
                    "images": p[f"mnist_{method}"]["images"],
                    "correct": p[f"mnist_{method}"]["correct"],
                    "cycles_per_image": p[f"mnist_{method}"]["h0_cycles_total"] // p[f"mnist_{method}"]["images"],
                    "speedup_vs_scalar": round(scalar_mnist / p[f"mnist_{method}"]["h0_cycles_total"], 4),
                    "retired_per_image": p[f"mnist_{method}"]["v11"]["retired"] // p[f"mnist_{method}"]["images"],
                    "npu_busy_fraction": round(p[f"mnist_{method}"]["v11"]["npu_job_cycles"] /
                                               p[f"mnist_{method}"]["v11"]["cycles"], 6),
                    "npu_array_active_fraction": round(p[f"mnist_{method}"]["v11"]["npu_compute_cycles"] /
                                                       p[f"mnist_{method}"]["v11"]["cycles"], 6),
                } for method in MNIST_METHODS},
            "streaming_ecg": {
                "cycles": p["streaming_ecg"]["cycles"],
                "cycles_per_chunk": p["streaming_ecg"]["cycles"] // p["streaming_ecg"]["iterations"],
                "dma_bytes": p["streaming_ecg"]["dma_bytes"],
                "npu_jobs": p["streaming_ecg"]["npu_jobs"],
            },
            "cifar_cnn": {
                "cycles": p["cifar_cnn"]["cycles"],
                "cycles_per_image": p["cifar_cnn"]["cycles"] // p["cifar_cnn"]["iterations"],
                "npu_busy_fraction": round(p["cifar_cnn"]["npu_job_cycles"] / p["cifar_cnn"]["cycles"], 6),
                "npu_array_active_fraction": round(p["cifar_cnn"]["npu_compute_cycles"] / p["cifar_cnn"]["cycles"], 6),
            },
            "aster_minimal": {
                c["workload"]: p[c["id"]]["cycles"] for c in CAPTURES if c["group"] == "minimal"},
        }
    summary["sync1_over_async0_cycles"] = {
        c["id"]: round(_cycles(bundle, "sync1", c) / _cycles(bundle, "async0", c), 4) for c in CAPTURES}
    return summary


def _cycles(bundle: Path, model: str, capture: dict) -> int:
    text = (bundle / "records" / model / "r1" / f"{capture['id']}.record").read_text()
    if capture["format"] == "v9":
        return sum(int(fields_of(line)["h0_cycles"], 16)
                   for line in text.splitlines() if line.startswith("ASTERBENCH,"))
    return int(fields_of(text)["cycles"], 16)


def inventory(bundle: Path) -> dict[str, str]:
    return {str(path.relative_to(bundle)): sha256(path)
            for path in sorted(bundle.rglob("*")) if path.is_file() and path.name != "manifest.json"}


def audit(bundle: Path, *, current: bool = False) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text())
    require(manifest.get("schema") == SCHEMA, "manifest schema is wrong")
    require(manifest.get("status") == "complete", "baseline manifest is not complete")
    require(manifest.get("files") == inventory(bundle), "manifest file inventory differs from disk")
    require(manifest.get("models") == MODELS and manifest.get("common") == COMMON,
            "manifest memory models or common SoC configuration differ from the audit contract")
    require([c["id"] for c in manifest.get("captures", [])] == [c["id"] for c in CAPTURES],
            "manifest capture list differs from the audit contract")
    require(manifest.get("unsupported") == UNSUPPORTED, "unsupported-method list differs")

    source = manifest["source"]
    revision = source["revision"]
    require(source.get("dirty") is False, "baseline was captured from a dirty source tree")
    require(source["tree_sha256"] == source_tree_hash(revision),
            "recorded source hash does not match the recorded revision")
    if current:
        require(working_tree_clean(), "current source tree has uncommitted changes")
        require(source_tree_hash("HEAD") == source["tree_sha256"],
                "current source differs from the baseline revision")

    mnist_model = v9.load_model(MNIST_MODEL)
    cifar_model = cifar_reference.load_model(CIFAR_MODEL)
    # Recompute the integer CIFAR network independently; it must reproduce the
    # artifact's reference logits that the record checksum is compared against.
    cifar_reference.reference(CIFAR_MODEL)
    for model in MODELS:
        for repeat in REPEATS:
            texts, parsed = {}, {}
            for capture in CAPTURES:
                path = bundle / "records" / model / f"r{repeat}" / f"{capture['id']}.record"
                require(path.is_file(), f"missing record {path.relative_to(bundle)}")
                texts[capture["id"]] = path.read_text()
                parsed[capture["id"]] = validate_capture(capture, texts[capture["id"]], model,
                                                         mnist_model, cifar_model)
            check_clocks(model, parsed)
            coherent = {c["id"]: parsed[c["id"]] for c in CAPTURES if c["format"] == "v11"}
            check_engine_counters(model, coherent, texts)
        for capture in CAPTURES:
            first = (bundle / "records" / model / "r1" / f"{capture['id']}.record").read_bytes()
            second = (bundle / "records" / model / "r2" / f"{capture['id']}.record").read_bytes()
            require(first == second, f"{model}/{capture['id']}: determinism repeat differs")

    for capture in manifest["captures"]:
        require(len(capture.get("firmware_sha256", "")) == 64,
                f"{capture['id']}: firmware hash is missing")
    summary = summarize(bundle)
    require(manifest.get("summary") == summary, "manifest summary differs from re-evaluation")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, nargs="?")
    parser.add_argument("--current", action="store_true",
                        help="also require the working tree to equal the baseline revision")
    args = parser.parse_args()
    bundle = args.bundle
    if bundle is None:
        candidates = sorted(path for path in (ROOT / "docs/results/phase17").glob("baseline-*")
                            if not path.name.endswith("-FAILED"))
        if not candidates:
            sys.stderr.write("FAIL: no Phase 17 baseline bundle under docs/results/phase17\n")
            return 1
        bundle = candidates[-1]
    try:
        summary = audit(bundle, current=args.current)
    except (AuditError, ValueError, OSError, KeyError) as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    conv = summary["sync1"]["conv2d"]["speedup_vs_coherent_scalar"]
    print(f"PASS: Phase 17 retained baseline {bundle.name} "
          f"({len(CAPTURES)} captures x {len(MODELS)} memory models x {len(REPEATS)} repeats); "
          f"sync1 conv2d NPU {conv['conv2d_npu']}x DOT8 {conv['conv2d_dot8']}x vs coherent scalar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
