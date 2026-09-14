#!/usr/bin/env python3
"""Strict, independent AsterBench v7 validator for the Phase 9 INT8 GEMM.

The firmware emits complete allocations so this validator can verify the
arithmetic, byte placement, guards, descriptor/counter ABIs and NPU accounting
without trusting the firmware's scalar comparison.  It intentionally does
not import any of the older AsterBench validators.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable


RAM_LO = 0x10000000
RAM_HI = 0x10008000
CLOCK_HZ = 31_250_000
MAX_U64 = (1 << 64) - 1
PLACEMENTS = {
    "aligned": (0, 0, 0),
    "a_plus1": (1, 0, 0),
    "b_plus2": (0, 2, 0),
    "c_plus3": (0, 0, 3),
}

CPU_EVENTS = (
    "cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss",
    "backing", "atomic", "sc_success", "sc_failure", "intervention",
    "invalidation", "writeback",
)
DMA_EVENTS = (
    "busy", "wait", "reads", "writes", "bytes", "backing_reads", "backing_writes",
    "forwards", "dirty_words", "invalidations", "success", "aborts", "errors", "rejected",
)
STRING_FIELDS = {
    "name", "window", "policy", "status", "method", "order", "placement",
    "a_hex", "b_hex", "c_initial_hex", "scalar_output", "npu_output",
}
DECIMAL_FIELDS = {
    "version", "capture", "m", "n", "k", "a_stride", "b_stride", "c_stride",
    "a_offset", "b_offset", "c_offset", "harts", "workers", "a_allocation_bytes",
    "b_allocation_bytes", "c_allocation_bytes", "c_guard_bytes", "scalar_errors",
    "npu_errors", "guard_errors", "descriptor_abi", "counter_abi", "cpu_abi",
    "dma_abi", "dma_counter_abi", "clock_hz", "l1", "sync_memory", "line_words",
    "line_count", "memory_wait", "npu_status", "npu_error_code", "npu_aborted",
    "npu_bytes_read", "npu_bytes_written", "npu_tiles",
}
HEX32_FIELDS = {"seed", "a_addr", "b_addr", "c_addr"}
HEX64_FIELDS = {
    f"h{hart}_{event}" for hart in (0, 1) for event in CPU_EVENTS
} | {f"dma_{event}" for event in DMA_EVENTS} | {
    "npu_job_cycles", "npu_compute_cycles",
}
ALL_FIELDS = STRING_FIELDS | DECIMAL_FIELDS | HEX32_FIELDS | HEX64_FIELDS
NUMERIC_FIELDS = DECIMAL_FIELDS | HEX32_FIELDS | HEX64_FIELDS
COUNTER_FIELDS = {
    f"h{hart}_{event}" for hart in (0, 1) for event in CPU_EVENTS
} | {f"dma_{event}" for event in DMA_EVENTS}
NPU_FIELDS = {
    "npu_status", "npu_error_code", "npu_aborted", "npu_bytes_read",
    "npu_bytes_written", "npu_job_cycles", "npu_compute_cycles", "npu_tiles",
}
PAIR_EXCLUDED_FIELDS = {"method"} | COUNTER_FIELDS | NPU_FIELDS

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX32 = re.compile(r"0x[0-9a-f]{8}\Z")
_HEX64 = re.compile(r"0x[0-9a-f]{16}\Z")
_HEX_BYTES = re.compile(r"[0-9a-f]*\Z")


class ValidationError(ValueError):
    """A record or capture violates the v7 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require("\r" not in line, "record contains carriage return")
    require(len(line) <= 1_048_576, "record exceeds v7 length bound")
    require(line.startswith("ASTERBENCH,"), "record prefix is not ASTERBENCH")
    require(line.endswith("\n") and line.count("\n") == 1, "record is not one complete line")
    fields: dict[str, str] = {}
    for item in line[len("ASTERBENCH,"):-1].split(","):
        require(item and item.count("=") == 1, "empty or malformed record field")
        key, value = item.split("=", 1)
        require(key in ALL_FIELDS, f"unknown v7 field: {key}")
        require(key not in fields, f"duplicate v7 field: {key}")
        require(value != "", f"empty v7 value: {key}")
        fields[key] = value
    require(set(fields) == ALL_FIELDS, "v7 record has missing fields")
    return fields


def parse_record(line: str) -> dict[str, object]:
    """Parse and validate one complete ASTERBENCH v7 record."""
    raw = _parse_fields(line)
    result: dict[str, object] = dict(raw)
    for key in DECIMAL_FIELDS:
        require(_DECIMAL.fullmatch(raw[key]) is not None, f"noncanonical decimal: {key}")
        value = int(raw[key], 10)
        require(value <= MAX_U64, f"decimal overflows u64: {key}")
        result[key] = value
    for key in HEX32_FIELDS:
        require(_HEX32.fullmatch(raw[key]) is not None, f"noncanonical 32-bit hex: {key}")
        result[key] = int(raw[key], 16)
    for key in HEX64_FIELDS:
        require(_HEX64.fullmatch(raw[key]) is not None, f"noncanonical 64-bit hex: {key}")
        result[key] = int(raw[key], 16)
    for key in ("a_hex", "b_hex", "c_initial_hex", "scalar_output", "npu_output"):
        require(_HEX_BYTES.fullmatch(raw[key]) is not None, f"noncanonical byte hex: {key}")
        require(len(raw[key]) % 2 == 0, f"odd-length byte hex: {key}")
        try:
            result[key] = bytes.fromhex(raw[key])
        except ValueError as error:
            raise ValidationError(f"invalid byte hex: {key}") from error
    validate_record(result)
    return result


def _aligned(value: int) -> int:
    return (value + 63) & ~63


def _a_value(index: int, seed: int) -> int:
    if (index + seed) % 29 == 0:
        return 0x80
    if (index + seed) % 31 == 0:
        return 0x7F
    return (index * 73 + seed * 19 + (index >> 2)) & 0xFF


def _b_value(index: int, seed: int) -> int:
    if (index + seed) % 23 == 0:
        return 0x80
    if (index + seed) % 41 == 0:
        return 0x7F
    return (index * 29 + seed * 47 + (index >> 1)) & 0xFF


def _signed(byte: int) -> int:
    return byte - 256 if byte & 0x80 else byte


def _oracle(record: dict[str, object]) -> bytes:
    m, n, k = (int(record[key]) for key in ("m", "n", "k"))
    a_stride, b_stride, c_stride = (int(record[key]) for key in ("a_stride", "b_stride", "c_stride"))
    a_offset, b_offset, c_offset = (int(record[key]) for key in ("a_offset", "b_offset", "c_offset"))
    a = record["a_hex"]
    b = record["b_hex"]
    output = bytearray(record["c_initial_hex"])
    for row in range(m):
        for col in range(n):
            total = 0
            for reduction in range(k):
                total += _signed(a[a_offset + row * a_stride + reduction]) * _signed(
                    b[b_offset + reduction * b_stride + col]
                )
            value = total & 0xFFFFFFFF
            offset = c_offset + row * c_stride + col * 4
            output[offset:offset + 4] = value.to_bytes(4, "little")
    return bytes(output)


def _expected_reads(record: dict[str, object]) -> int:
    m, n, k = (int(record[key]) for key in ("m", "n", "k"))
    return sum(
        (min(4, m - row) + min(4, n - col)) * k
        for row in range(0, m, 4)
        for col in range(0, n, 4)
    )


def validate_record(record: dict[str, object]) -> dict[str, object]:
    """Validate a typed record and return it for convenient composition."""
    require(record["version"] == 7, "record is not AsterBench v7")
    require(record["name"] == "gemm_int8", "unexpected v7 workload")
    require(record["window"] == "cpu_command_to_result_visible", "unexpected measurement window")
    require(record["policy"] == "paired_same_input", "unexpected pairing policy")
    require(record["status"] == "PASS", "firmware reported a failed record")
    require(record["method"] in ("scalar", "npu"), "unknown GEMM method")
    require(record["order"] == "scalar_npu", "unexpected method order")
    require(record["placement"] in PLACEMENTS, "unknown byte placement")
    require(1 <= record["capture"] <= 96, "capture is outside the frozen study")
    require(0 < record["m"] <= 1024 and 0 < record["n"] <= 1024 and 0 < record["k"] <= 1024,
            "primary v7 dimensions must be nonzero and <= 1024")

    m, n, k = record["m"], record["n"], record["k"]
    require(record["a_stride"] == k + 3, "A stride does not match the frozen benchmark")
    require(record["b_stride"] == n + 5, "B stride does not match the frozen benchmark")
    require(record["c_stride"] == n * 4 + 4, "C stride does not match the frozen benchmark")
    expected_offsets = PLACEMENTS[record["placement"]]
    require((record["a_offset"], record["b_offset"], record["c_offset"]) == expected_offsets,
            "placement offsets do not match the named pattern")

    a_used = (m - 1) * record["a_stride"] + k
    b_used = (k - 1) * record["b_stride"] + n
    c_used = (m - 1) * record["c_stride"] + (n - 1) * 4 + 4
    a_bytes, b_bytes, c_bytes = (_aligned(value + 64) for value in (a_used, b_used, c_used))
    require((record["a_allocation_bytes"], record["b_allocation_bytes"], record["c_allocation_bytes"]) ==
            (a_bytes, b_bytes, c_bytes), "allocation size is not reproducible")
    require(record["c_guard_bytes"] == c_bytes - c_used, "C guard size is incorrect")

    for address_key, offset, allocation in (
        ("a_addr", record["a_offset"], a_bytes),
        ("b_addr", record["b_offset"], b_bytes),
        ("c_addr", record["c_offset"], c_bytes),
    ):
        base = record[address_key] - offset
        require(base % 64 == 0, f"{address_key} base is not 64-byte aligned")
        require(RAM_LO <= base and base + allocation <= RAM_HI, f"{address_key} allocation escapes shared RAM")
    a_begin, a_end = record["a_addr"] - record["a_offset"], record["a_addr"] - record["a_offset"] + a_bytes
    b_begin, b_end = record["b_addr"] - record["b_offset"], record["b_addr"] - record["b_offset"] + b_bytes
    c_begin, c_end = record["c_addr"] - record["c_offset"], record["c_addr"] - record["c_offset"] + c_bytes
    require(a_end <= b_begin or b_end <= a_begin, "A and B allocations overlap")
    require(a_end <= c_begin or c_end <= a_begin, "A and C allocations overlap")
    require(b_end <= c_begin or c_end <= b_begin, "B and C allocations overlap")

    for key, expected_length in (
        ("a_hex", a_bytes), ("b_hex", b_bytes), ("c_initial_hex", c_bytes),
        ("scalar_output", c_bytes), ("npu_output", c_bytes),
    ):
        require(len(record[key]) == expected_length, f"{key} length does not match allocation")
    seed = record["seed"]
    require(record["a_hex"] == bytes(_a_value(index, seed) for index in range(a_bytes)),
            "A allocation does not match the declared seed")
    require(record["b_hex"] == bytes(_b_value(index, seed) for index in range(b_bytes)),
            "B allocation does not match the declared seed")
    require(record["c_initial_hex"] == bytes([0xA5]) * c_bytes, "C initial guards are not intact")
    expected = _oracle(record)
    require(record["scalar_output"] == expected, "independent scalar oracle rejects scalar output")
    require(record["npu_output"] == expected, "independent scalar oracle rejects NPU output")
    require(record["scalar_errors"] == record["npu_errors"] == record["guard_errors"] == 0,
            "firmware-side correctness checks reported an error")

    require(record["descriptor_abi"] == 1 and record["counter_abi"] == 1, "NPU ABI mismatch")
    require(record["cpu_abi"] == 4 and record["dma_abi"] == 1 and record["dma_counter_abi"] == 5,
            "CPU/DMA ABI mismatch")
    require(record["clock_hz"] == CLOCK_HZ, "clock is not the pinned 31.25 MHz clock")
    require(record["harts"] in (1, 2) and record["workers"] == 1, "unsupported benchmark topology")
    require(record["l1"] in (0, 1) and record["sync_memory"] in (0, 1), "invalid cache/timing flags")
    require(record["line_words"] >= 2 and record["line_words"] <= 1024 and record["line_words"] & (record["line_words"] - 1) == 0,
            "invalid cache line width")
    require(record["line_count"] >= 2 and record["line_count"] <= 1024 and record["line_count"] & (record["line_count"] - 1) == 0,
            "invalid cache line count")
    require(record["sync_memory"] <= record["memory_wait"] <= 1024, "invalid RAM timing configuration")

    for hart in (0, 1):
        require(record[f"h{hart}_cycles"] > 0, f"hart {hart} did not advance")
    require(record["h1_cycles"] == record["h0_cycles"], "hart cycle windows are not aligned")
    require(record["h0_retired"] > 0 and record["h0_memory"] > 0 and record["h0_backing"] > 0,
            "CPU measurement window has no retired/memory/backing activity")
    require(all(record[f"h1_{event}"] == 0 for event in CPU_EVENTS if event != "cycles"),
            "benchmark secondary hart performed unaccounted work")
    if not record["l1"]:
        require(all(record[f"h0_{event}"] == 0 for event in ("i_access", "i_miss", "d_access", "d_miss", "writeback")),
                "cache events are nonzero with L1 disabled")
    require(all(record[key] <= record["h0_cycles"] for key in ("h0_retired", "h0_memory", "h0_backing")),
            "CPU event exceeds its cycle window")
    require(all(value <= MAX_U64 for key, value in record.items() if key in NUMERIC_FIELDS), "numeric field overflows u64")

    if record["method"] == "scalar":
        require(all(record[key] == 0 for key in NPU_FIELDS), "scalar record contains NPU activity")
    else:
        tiles = ((m + 3) // 4) * ((n + 3) // 4)
        require(record["npu_status"] == 2 and record["npu_error_code"] == 0 and record["npu_aborted"] == 0,
                "NPU record is not a clean DONE")
        require(record["npu_bytes_read"] == _expected_reads(record), "NPU operand-byte accounting mismatch")
        require(record["npu_bytes_written"] == m * n * 4, "NPU output-byte accounting mismatch")
        require(record["npu_tiles"] == tiles, "NPU tile accounting mismatch")
        require(record["npu_compute_cycles"] == k * tiles, "NPU compute-cycle accounting mismatch")
        require(record["npu_job_cycles"] > 0, "NPU job-cycle accounting is empty")
    return record


def _parse_stop(line: str) -> dict[str, int]:
    require("\r" not in line and line.startswith("ASTERSTOP,"), "invalid ASTERSTOP line")
    require(line.endswith("\n") and line.count("\n") == 1, "incomplete ASTERSTOP line")
    fields: dict[str, int] = {}
    for item in line[len("ASTERSTOP,"):-1].split(","):
        require(item.count("=") == 1, "malformed ASTERSTOP field")
        key, value = item.split("=", 1)
        require(key in {"records", "ram_bytes", "device_transactions", "npu_transactions"} and key not in fields,
                "unknown or duplicate ASTERSTOP field")
        require(_DECIMAL.fullmatch(value) is not None, "noncanonical ASTERSTOP number")
        fields[key] = int(value, 10)
    require(set(fields) == {"records", "ram_bytes", "device_transactions", "npu_transactions"},
            "incomplete ASTERSTOP")
    require(fields["records"] == 2 and fields["ram_bytes"] == 65536, "invalid STOP evidence")
    require(fields["device_transactions"] > 0 and fields["npu_transactions"] > 0,
            "STOP evidence contains no coherent device traffic")
    require(fields["npu_transactions"] <= fields["device_transactions"], "invalid transaction totals")
    return fields


def validate_pair(records: Iterable[dict[str, object]]) -> tuple[dict[str, object], dict[str, object]]:
    pair = tuple(records)
    require(len(pair) == 2, "v7 capture must contain exactly two records")
    require({record["method"] for record in pair} == {"scalar", "npu"}, "capture is not scalar/NPU pair")
    scalar = next(record for record in pair if record["method"] == "scalar")
    npu = next(record for record in pair if record["method"] == "npu")
    for key in ALL_FIELDS - PAIR_EXCLUDED_FIELDS:
        require(scalar[key] == npu[key], f"paired record differs in {key}")
    return scalar, npu


def validate_log(raw: str) -> dict[str, object]:
    """Validate a simulator/serial log containing exactly one v7 capture."""
    records: list[dict[str, object]] = []
    stops: list[dict[str, int]] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith("ASTERBENCH,"):
            records.append(parse_record(line))
        elif line.startswith("ASTERSTOP,"):
            stops.append(_parse_stop(line))
    require(len(records) == 2 and len(stops) == 1, "log must contain two records and one ASTERSTOP")
    validate_pair(records)
    return {"records": records, "stop": stops[0]}


STUDY_SHAPES = (
    (1, 1, 1), (1, 3, 4), (3, 1, 7), (3, 5, 8), (4, 4, 16), (5, 7, 3),
    (7, 5, 15), (8, 8, 31), (15, 3, 32), (16, 16, 64), (31, 5, 7), (32, 32, 32),
)
STUDY_PLACEMENTS = tuple(PLACEMENTS)


def study_plan() -> list[dict[str, object]]:
    """Return the exact 96-entry primary plan frozen by Phase 9."""
    plan: list[dict[str, object]] = []
    capture = 1
    for m, n, k in STUDY_SHAPES:
        for placement in STUDY_PLACEMENTS:
            for l1 in (0, 1):
                plan.append({
                    "capture": capture, "m": m, "n": n, "k": k,
                    "placement": placement, "l1": l1, "sync_memory": 0,
                    "memory_wait": 0, "line_words": 4, "line_count": 16,
                    "harts": 1, "workers": 1,
                    "seed": (0x9E3779B9 ^ (capture * 0x85EBCA6B)) & 0xFFFFFFFF,
                })
                capture += 1
    require(len(plan) == 96, "frozen v7 study does not contain 96 captures")
    return plan


def _load(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def mutation_tests(raw: str) -> list[str]:
    """Run strict parser mutations against a valid capture and report names."""
    validated = validate_log(raw)
    first = next(line for line in raw.splitlines(keepends=True) if line.startswith("ASTERBENCH,"))
    output_start = first.index(",scalar_output=") + len(",scalar_output=")
    output_nibble = "7" if first[output_start] != "7" else "8"
    mutations = {
        "missing_field": first.replace(",m=", ",", 1),
        "duplicate_field": first.replace(",m=", ",capture=1,m=", 1),
        "unknown_field": first.replace(",m=", ",unknown=0,m=", 1),
        "noncanonical_decimal": first.replace(",m=", ",m=01,m=", 1),
        "output_nibble": first[:output_start] + output_nibble + first[output_start + 1:],
    }
    passed: list[str] = []
    for name, mutation in mutations.items():
        try:
            parse_record(mutation)
        except ValidationError:
            passed.append(name)
        else:
            raise ValidationError(f"mutation was accepted: {name}")
    # The original remains valid after all negative cases.
    require(validated["stop"]["records"] == 2, "mutation suite damaged the baseline")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate one simulator/serial log")
    validate.add_argument("path", nargs="?", default="-", help="log path, or - for stdin")
    mutate = sub.add_parser("mutations", help="run parser mutation checks against a valid log")
    mutate.add_argument("path", help="validated AsterBench v7 log")
    sub.add_parser("study-plan", help="print the frozen 96-capture primary plan")
    args = parser.parse_args()
    try:
        if args.command == "study-plan":
            print(json.dumps(study_plan(), indent=2, sort_keys=True))
        elif args.command == "validate":
            result = validate_log(_load(args.path))
            print(json.dumps({"records": len(result["records"]), "stop": result["stop"], "status": "PASS"}, sort_keys=True))
        else:
            names = mutation_tests(_load(args.path))
            print(json.dumps({"mutations": names, "status": "PASS"}, sort_keys=True))
        return 0
    except (OSError, ValidationError, UnicodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
