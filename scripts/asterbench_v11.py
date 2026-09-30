#!/usr/bin/env python3
"""Strict AsterBench v11 validator for coherent-SoC workload records.

v2-v10 remain implemented by their original validators. V11 reports hart 0's
coherent CPU counter bank (cycles, retired, memory and cache transactions), the
DOT8 counters of both harts, and cumulative, requester-attributed DMA and NPU
totals. Hart 1's CPU counters are not in v11; a multi-hart CPU breakdown is a
Phase 20 record extension.
"""

from __future__ import annotations

import argparse
import re
import sys

CATEGORIES = ("cpu", "memory", "dsp", "ml", "system")
DOT8_EVENTS = ("accept", "wait", "complete", "retire")
STRING_FIELDS = {"name", "category", "status"}
DECIMAL_FIELDS = {
    "version", "size", "iterations", "param", "clock_hz", "harts", "workers",
    "l1", "sync_memory", "line_words", "line_count", "memory_wait",
    "dma_jobs", "dma_completed_jobs", "dma_aborted_jobs", "dma_error_jobs",
    "npu_jobs", "npu_completed_jobs", "npu_aborted_jobs", "npu_error_jobs",
}
HEX32_FIELDS = {"seed", "checksum"}
HEX64_FIELDS = {
    "cycles", "retired", "memory_transactions", "backing_transactions",
    "cache_accesses", "cache_misses", "dma_bytes", "dma_job_cycles",
    "npu_bytes_read", "npu_bytes_written", "npu_tiles", "npu_job_cycles",
    "npu_compute_cycles",
} | {f"h{hart}_dot8_{event}" for hart in (0, 1) for event in DOT8_EVENTS}
ALL_FIELDS = STRING_FIELDS | DECIMAL_FIELDS | HEX32_FIELDS | HEX64_FIELDS

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX32 = re.compile(r"0x[0-9a-f]{8}\Z")
_HEX64 = re.compile(r"0x[0-9a-f]{16}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")


class ValidationError(ValueError):
    """An AsterBench v11 record violates its schema or invariants."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require(len(line) <= 8192, "record exceeds v11 length bound")
    require("\r" not in line, "record contains carriage return")
    require(line.startswith("ASTERBENCH,"), "record prefix is not ASTERBENCH")
    require(line.endswith("\n") and line.count("\n") == 1, "record is not one complete line")
    fields: dict[str, str] = {}
    body = line[len("ASTERBENCH,"):-1]
    require(body != "", "record has no fields")
    for token in body.split(","):
        require("=" in token, "record field has no '='")
        key, value = token.split("=", 1)
        require(key != "" and value != "", "record field has an empty key/value")
        require(key not in fields, f"duplicate record field {key!r}")
        fields[key] = value
    require(set(fields) == ALL_FIELDS, "record fields do not match the v11 schema exactly")
    return fields


def _decimal(fields: dict[str, str], key: str) -> int:
    require(_DECIMAL.match(fields[key]) is not None, f"field {key} is not canonical decimal")
    value = int(fields[key])
    require(value <= 0xFFFFFFFF, f"field {key} exceeds 32 bits")
    return value


def _hex(fields: dict[str, str], key: str, pattern: re.Pattern[str]) -> int:
    require(pattern.match(fields[key]) is not None,
            f"field {key} has invalid hexadecimal width/encoding")
    return int(fields[key], 16)


def validate_line(line: str, *, name: str | None = None,
                  category: str | None = None) -> dict[str, object]:
    fields = _parse_fields(line)
    decimal = {key: _decimal(fields, key) for key in DECIMAL_FIELDS}
    hex32 = {key: _hex(fields, key, _HEX32) for key in HEX32_FIELDS}
    counters = {key: _hex(fields, key, _HEX64) for key in HEX64_FIELDS}

    require(decimal["version"] == 11, "version is not 11")
    require(_NAME.match(fields["name"]) is not None, "invalid workload name")
    require(fields["category"] in CATEGORIES, "unknown workload category")
    require(fields["status"] == "PASS", "record status is not PASS")
    if name is not None:
        require(fields["name"] == name, "record name differs from requested workload")
    if category is not None:
        require(fields["category"] == category, "record category differs")

    require(4 <= decimal["size"] <= 65536 and decimal["size"] % 4 == 0,
            "size must be 4..65536 and word aligned")
    require(decimal["iterations"] > 0 and decimal["param"] > 0,
            "iterations and param must be positive")
    require(decimal["clock_hz"] > 0, "clock_hz must be positive")
    require(decimal["harts"] in (1, 2) and 1 <= decimal["workers"] <= decimal["harts"],
            "invalid hart/worker topology")
    require(decimal["l1"] in (0, 1) and decimal["sync_memory"] in (0, 1),
            "invalid cache/memory configuration")
    for key in ("line_words", "line_count"):
        value = decimal[key]
        require(2 <= value <= 1024 and value & (value - 1) == 0,
                f"{key} must be a power of two, 2..1024")
    require(decimal["sync_memory"] <= decimal["memory_wait"] <= 1024,
            "memory_wait is out of range")

    cycles = counters["cycles"]
    require(cycles > 0 and 0 < counters["retired"] <= cycles,
            "retired must be in (0, cycles]")
    require(0 < counters["memory_transactions"] <= cycles,
            "memory_transactions out of range")
    require(0 < counters["backing_transactions"] <= cycles,
            "backing_transactions out of range")
    require(counters["cache_misses"] <= counters["cache_accesses"] <= cycles,
            "cache counters out of range")
    for hart in (0, 1):
        for event in DOT8_EVENTS:
            require(counters[f"h{hart}_dot8_{event}"] <= cycles,
                    f"h{hart}_dot8_{event} exceeds its cycle window")
        require(counters[f"h{hart}_dot8_accept"] == counters[f"h{hart}_dot8_complete"] ==
                counters[f"h{hart}_dot8_retire"],
                f"h{hart} DOT8 accept/complete/retire counts differ")
        if hart >= decimal["harts"]:
            require(all(counters[f"h{hart}_dot8_{event}"] == 0 for event in DOT8_EVENTS),
                    "absent hart has DOT8 activity")

    require(decimal["dma_completed_jobs"] + decimal["dma_aborted_jobs"] +
            decimal["dma_error_jobs"] == decimal["dma_jobs"],
            "DMA job outcomes do not sum to accepted DMA jobs")
    require(decimal["dma_completed_jobs"] == decimal["dma_jobs"] and
            decimal["dma_aborted_jobs"] == decimal["dma_error_jobs"] == 0,
            "PASS record contains an incomplete/failed DMA job")
    if decimal["dma_jobs"] == 0:
        require(counters["dma_bytes"] == counters["dma_job_cycles"] == 0,
                "DMA metrics are nonzero without DMA jobs")
    require(counters["dma_job_cycles"] <= cycles,
            "DMA busy cycles exceed the common window")

    require(decimal["npu_completed_jobs"] + decimal["npu_aborted_jobs"] +
            decimal["npu_error_jobs"] == decimal["npu_jobs"],
            "NPU job outcomes do not sum to accepted NPU jobs")
    require(decimal["npu_completed_jobs"] == decimal["npu_jobs"] and
            decimal["npu_aborted_jobs"] == decimal["npu_error_jobs"] == 0,
            "PASS record contains an incomplete/failed NPU job")
    require(counters["npu_compute_cycles"] <= counters["npu_job_cycles"] <= cycles,
            "NPU compute/job cycles exceed the common window")
    if decimal["npu_jobs"] == 0:
        require(all(counters[key] == 0 for key in (
            "npu_bytes_read", "npu_bytes_written", "npu_tiles",
            "npu_job_cycles", "npu_compute_cycles")),
            "NPU metrics are nonzero without NPU jobs")

    result: dict[str, object] = {
        "name": fields["name"], "category": fields["category"], "status": fields["status"],
        **decimal, **hex32, **counters,
    }
    return result


def validate_stream(lines, **kwargs):
    records = [validate_line(line if line.endswith("\n") else line + "\n", **kwargs)
               for line in lines if line.strip()]
    require(records, "no v11 records were provided")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--name")
    parser.add_argument("--category", choices=CATEGORIES)
    args = parser.parse_args()
    try:
        records = validate_stream(sys.stdin, name=args.name, category=args.category)
    except ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for record in records:
        print(f"PASS: v11 {record['name']} [{record['category']}] "
              f"size={record['size']} cycles={record['cycles']} "
              f"retired={record['retired']} dma_bytes={record['dma_bytes']} "
              f"npu_jobs={record['npu_jobs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
