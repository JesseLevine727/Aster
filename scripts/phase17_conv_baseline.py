#!/usr/bin/env python3
"""Audit the Phase 17 same-top Conv2D diagnostic matrix.

This is a development-time v10 consistency check, not the eventual v11 closeout
audit. It verifies that coherent scalar, DOT8, and NPU records describe the same
work/configuration, match the independent arithmetic oracle, and do not
attribute this DMA-free workload to DMA.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import asterbench_v10 as bench
import workload_reference as reference


ENGINES = ("conv2d_scalar_coh", "conv2d_dot8", "conv2d_npu")
COMMON_FIELDS = (
    "category", "size", "iterations", "param", "seed", "checksum", "clock_hz",
    "l1", "sync_memory", "line_words", "line_count", "memory_wait",
)
ZERO64 = "0x" + "0" * 16


def require(condition: bool, message: str) -> None:
    if not condition:
        raise bench.ValidationError(message)


def audit_records(records: dict[str, str]) -> dict[str, object]:
    require(set(records) == set(ENGINES), "matrix must contain exactly coherent scalar, DOT8, and NPU")
    parsed: dict[str, dict[str, str]] = {}
    for name in ENGINES:
        line = records[name]
        fields = bench._parse_fields(line)
        typed = bench.validate_line(line, name=name, category="dsp")
        reference.verify(typed, name)
        parsed[name] = fields

    reference_fields = parsed[ENGINES[0]]
    for name in ENGINES[1:]:
        for field in COMMON_FIELDS:
            require(parsed[name][field] == reference_fields[field],
                    f"{name} differs from coherent scalar in {field}")

    for name in ENGINES:
        require(parsed[name]["dma_bytes"] == ZERO64,
                f"{name} reported DMA bytes although this workload submits no DMA job")
    for name in ("conv2d_scalar_coh", "conv2d_dot8"):
        require(parsed[name]["accelerator_cycles"] == ZERO64,
                f"{name} reported NPU activity")
    npu_cycles = int(parsed["conv2d_npu"]["accelerator_cycles"], 16)
    require(npu_cycles > 0, "NPU workload reported no active-array cycles")

    cycles = {name: int(parsed[name]["cycles"], 16) for name in ENGINES}
    require(all(value > 0 for value in cycles.values()), "matrix contains a zero-cycle workload")
    return {
        "checksum": reference_fields["checksum"],
        "configuration": {
            field: reference_fields[field] for field in COMMON_FIELDS if field != "checksum"
        },
        "cycles": cycles,
        "npu_last_job_compute_cycles": npu_cycles,
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
        f"NPU/coherent-scalar={result['npu_over_coherent_scalar']:.2f}x"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
