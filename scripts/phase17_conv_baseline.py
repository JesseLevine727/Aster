#!/usr/bin/env python3
"""Audit the Phase 17 same-top Conv2D diagnostic matrix using v11 records.

The NPU totals are checked against an independent shape oracle derived from the
workload definition (square image of `size` pixels, K×K kernel `param`, one
im2col GEMM of M=(H-K+1)^2, N=1, K*K per iteration) and the NPU's documented
counting rules (one read per operand byte, one write per result byte, one
array step per K per tile). A last-job-only or otherwise non-cumulative total
therefore fails.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import asterbench_v11 as bench
import workload_reference as reference


ENGINES = ("conv2d_scalar_coh", "conv2d_dot8", "conv2d_npu")
# The diagnostic matrix uses the default 4x4 NPU geometry.
NPU_ROWS = NPU_COLS = 4
NPU_ORACLE_FIELDS = ("npu_tiles", "npu_bytes_read", "npu_bytes_written", "npu_compute_cycles")
COMMON_FIELDS = (
    "category", "size", "iterations", "param", "seed", "checksum", "clock_hz",
    "harts", "workers", "l1", "sync_memory", "line_words", "line_count", "memory_wait",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise bench.ValidationError(message)


def conv_npu_oracle(size: int, kernel: int, iterations: int) -> dict[str, int]:
    """Cumulative NPU counters expected for `iterations` im2col Conv2D jobs."""
    side = math.isqrt(size)
    require(side * side == size, "Conv2D oracle needs a square image")
    require(0 < kernel <= side, "Conv2D kernel does not fit the image")
    out = side - kernel + 1
    m, n, k = out * out, 1, kernel * kernel
    tiles = reads = 0
    for row in range(0, m, NPU_ROWS):
        rows = min(NPU_ROWS, m - row)
        for col in range(0, n, NPU_COLS):
            cols = min(NPU_COLS, n - col)
            tiles += 1
            reads += k * (rows + cols)
    per_job = {
        "npu_tiles": tiles,
        "npu_bytes_read": reads,
        "npu_bytes_written": 4 * m * n,
        "npu_compute_cycles": k * tiles,
        "macs": m * n * k,
    }
    return {key: value * iterations for key, value in per_job.items()}


def audit_records(records: dict[str, str]) -> dict[str, object]:
    require(set(records) == set(ENGINES), "matrix must contain exactly coherent scalar, DOT8, and NPU")
    parsed: dict[str, dict[str, object]] = {}
    for name in ENGINES:
        line = records[name]
        typed = bench.validate_line(line, name=name, category="dsp")
        reference.verify(typed, name)
        parsed[name] = typed

    reference_fields = parsed[ENGINES[0]]
    for name in ENGINES[1:]:
        for field in COMMON_FIELDS:
            require(parsed[name][field] == reference_fields[field],
                    f"{name} differs from coherent scalar in {field}")

    for name in ENGINES:
        record = parsed[name]
        require(record["dma_jobs"] == record["dma_completed_jobs"] == 0 and
                record["dma_aborted_jobs"] == record["dma_error_jobs"] == 0 and
                record["dma_bytes"] == record["dma_job_cycles"] == 0,
                f"{name} reported DMA activity although this workload submits no DMA job")

    scalar = parsed["conv2d_scalar_coh"]
    dot8 = parsed["conv2d_dot8"]
    npu = parsed["conv2d_npu"]
    require(scalar["npu_jobs"] == 0 and scalar["npu_job_cycles"] == 0 and
            scalar["npu_compute_cycles"] == 0,
            "coherent scalar workload reported NPU activity")
    require(scalar["h0_dot8_accept"] == scalar["h1_dot8_accept"] == 0,
            "coherent scalar workload reported DOT8 activity")
    require(dot8["npu_jobs"] == 0 and dot8["npu_job_cycles"] == 0 and
            dot8["npu_compute_cycles"] == 0,
            "DOT8 workload reported NPU activity")
    require(dot8["h0_dot8_accept"] + dot8["h1_dot8_accept"] > 0,
            "DOT8 workload reported no accepted operations")
    require(npu["h0_dot8_accept"] == npu["h1_dot8_accept"] == 0,
            "NPU workload reported DOT8 activity")
    require(npu["npu_jobs"] == npu["iterations"] and
            npu["npu_completed_jobs"] == npu["npu_jobs"] and
            npu["npu_aborted_jobs"] == npu["npu_error_jobs"] == 0,
            "NPU job totals do not match the four-iteration workload")
    expected = conv_npu_oracle(npu["size"], npu["param"], npu["iterations"])
    for field in NPU_ORACLE_FIELDS:
        require(npu[field] == expected[field],
                f"NPU {field}={npu[field]} differs from the cumulative shape oracle "
                f"({expected[field]})")
    npu_cycles = npu["npu_compute_cycles"]
    require(npu_cycles > 0, "NPU workload reported no active-array cycles")

    cycles = {name: parsed[name]["cycles"] for name in ENGINES}
    require(all(value > 0 for value in cycles.values()), "matrix contains a zero-cycle workload")
    return {
        "checksum": f"0x{reference_fields['checksum']:08x}",
        "configuration": {
            field: reference_fields[field] for field in COMMON_FIELDS if field != "checksum"
        },
        "cycles": cycles,
        "npu_cumulative_compute_cycles": npu_cycles,
        "npu_array_active_fraction": npu_cycles / cycles["conv2d_npu"],
        "npu_pe_utilization_when_active": expected["macs"] / (npu_cycles * NPU_ROWS * NPU_COLS),
        "npu_over_coherent_scalar": cycles["conv2d_scalar_coh"] / cycles["conv2d_npu"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records-dir", type=Path, default=Path("build"))
    args = parser.parse_args()
    records = {
        name: (args.records_dir / f"{name}.record").read_text()
        for name in ENGINES
    }
    result = audit_records(records)
    print(
        "PASS: Phase 17 same-top Conv2D diagnostic "
        f"checksum={result['checksum']} cycles={result['cycles']} "
        f"NPU/coherent-scalar={result['npu_over_coherent_scalar']:.2f}x "
        f"npu_compute_cycles={result['npu_cumulative_compute_cycles']} "
        f"array_active={100 * result['npu_array_active_fraction']:.2f}% "
        f"pe_utilization_when_active={100 * result['npu_pe_utilization_when_active']:.0f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
