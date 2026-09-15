#!/usr/bin/env python3
"""Strict, independent AsterBench v8 validator for the Phase 10 cross-engine study.

The firmware emits the complete A/B allocations, the poison output allocation
and the final output, so this validator can recompute every signed-INT8 result
with widened Python integers without trusting the firmware's own comparison.
It intentionally does not import any earlier AsterBench validator.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Iterable


CLOCK_HZ = 31_250_000
MAX_U64 = (1 << 64) - 1
MASK32 = 0xFFFFFFFF

KERNELS = ("dot", "fir", "gemm")
METHODS = ("scalar", "multicore", "dot8", "npu")
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
DOT8_EVENTS = ("accept", "wait", "complete", "retired")

STRING_FIELDS = {"name", "window", "policy", "status", "method", "placement",
                 "a_hex", "b_hex", "output_hex"}
DECIMAL_FIELDS = {
    "version", "capture", "job", "jobs", "m", "n", "k", "taps", "outputs",
    "a_stride", "b_stride", "c_stride", "a_offset", "b_offset", "c_offset",
    "a_allocation_bytes", "b_allocation_bytes", "c_allocation_bytes",
    "harts", "workers", "errors", "cpu_abi", "dma_abi", "dma_counter_abi",
    "dot8_instruction_abi", "dot8_counter_abi", "npu_descriptor_abi",
    "npu_counter_abi", "clock_hz", "l1", "sync_memory", "line_words",
    "line_count", "memory_wait", "npu_active", "npu_status", "npu_error_code",
    "npu_bytes_read", "npu_bytes_written", "npu_tiles",
}
HEX32_FIELDS = {"seed", "a_addr", "b_addr", "c_addr"}
HEX64_FIELDS = (
    {f"h{hart}_{event}" for hart in (0, 1) for event in CPU_EVENTS}
    | {f"dma_{event}" for event in DMA_EVENTS}
    | {f"h{hart}_dot8_{event}" for hart in (0, 1) for event in DOT8_EVENTS}
    | {"npu_job_cycles", "npu_compute_cycles"}
)
ALL_FIELDS = STRING_FIELDS | DECIMAL_FIELDS | HEX32_FIELDS | HEX64_FIELDS
COUNTER_FIELDS = (
    {f"h{hart}_{event}" for hart in (0, 1) for event in CPU_EVENTS}
    | {f"dma_{event}" for event in DMA_EVENTS}
    | {f"h{hart}_dot8_{event}" for hart in (0, 1) for event in DOT8_EVENTS}
)
NPU_FIELDS = {
    "npu_active", "npu_status", "npu_error_code", "npu_bytes_read",
    "npu_bytes_written", "npu_tiles", "npu_job_cycles", "npu_compute_cycles",
}

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX32 = re.compile(r"0x[0-9a-f]{8}\Z")
_HEX64 = re.compile(r"0x[0-9a-f]{16}\Z")
_HEX_BYTES = re.compile(r"[0-9a-f]*\Z")


class ValidationError(ValueError):
    """A record or capture violates the v8 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require("\r" not in line, "record contains carriage return")
    require(len(line) <= 1_048_576, "record exceeds v8 length bound")
    require(line.startswith("ASTERBENCH,"), "record prefix is not ASTERBENCH")
    require(line.endswith("\n") and line.count("\n") == 1, "record is not one complete line")
    fields: dict[str, str] = {}
    body = line[len("ASTERBENCH,"):-1]
    require(body != "", "record has no fields")
    for token in body.split(","):
        require("=" in token, "record field has no '='")
        key, value = token.split("=", 1)
        require(key != "", "record field has an empty key")
        require(key not in fields, f"duplicate record field {key!r}")
        fields[key] = value
    require(set(fields) == ALL_FIELDS, "record fields do not match the v8 schema exactly")
    return fields


def _int(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    require(_DECIMAL.match(value) is not None, f"field {key} is not a canonical decimal")
    return int(value)


def _hex32(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    require(_HEX32.match(value) is not None, f"field {key} is not 8 hexadecimal digits")
    return int(value, 16)


def _hex64(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    require(_HEX64.match(value) is not None, f"field {key} is not 16 hexadecimal digits")
    return int(value, 16)


def _bytes(fields: dict[str, str], key: str) -> bytes:
    value = fields[key]
    require(_HEX_BYTES.match(value) is not None and len(value) % 2 == 0,
            f"field {key} is not canonical byte hex")
    return bytes.fromhex(value)


def a_value(i: int, seed: int) -> int:
    if ((i + seed) & MASK32) % 29 == 0:
        return 0x80
    if ((i + seed) & MASK32) % 31 == 0:
        return 0x7F
    return (i * 73 + seed * 19 + (i >> 2)) & 0xFF


def b_value(i: int, seed: int) -> int:
    if ((i + seed) & MASK32) % 23 == 0:
        return 0x80
    if ((i + seed) & MASK32) % 41 == 0:
        return 0x7F
    return (i * 29 + seed * 47 + (i >> 1)) & 0xFF


def signed(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def allocation(used: int, offset: int) -> int:
    return ((offset + used + 64) + 63) & ~63


def shape(name: str, fields: dict[str, str]) -> dict[str, int]:
    m, n, k, taps = _int(fields, "m"), _int(fields, "n"), _int(fields, "k"), _int(fields, "taps")
    if name == "dot":
        require(m == 1 and n == 1, "dot requires m=n=1")
        return {"m": 1, "n": 1, "k": k, "a_used": k, "b_used": k, "outputs": 1,
                "a_stride": k, "b_stride": 1, "c_stride": 4}
    if name == "fir":
        require(n == 1 and taps >= 1, "fir requires n=1 and taps>=1")
        require(m == taps, "fir requires m=taps")
        return {"m": taps, "n": 1, "k": k, "a_used": k + taps - 1, "b_used": k,
                "outputs": taps, "a_stride": 1, "b_stride": 1, "c_stride": 4}
    require(name == "gemm", "unknown kernel")
    return {"m": m, "n": n, "k": k, "a_used": m * k, "b_used": k * n,
            "outputs": m * n, "a_stride": k, "b_stride": n, "c_stride": 4 * n}


def expected_output(name: str, fields: dict[str, str], a: bytes, b: bytes) -> bytes:
    dims = shape(name, fields)
    a_off, b_off = _int(fields, "a_offset"), _int(fields, "b_offset")
    offset = _int(fields, "c_offset")
    size = _int(fields, "c_allocation_bytes")
    buffer = bytearray([0xA5]) * size
    for i in range(dims["m"]):
        for j in range(dims["n"]):
            total = 0
            for k in range(dims["k"]):
                total += (signed(a[a_off + i * dims["a_stride"] + k])
                          * signed(b[b_off + k * dims["b_stride"] + j]))
            value = total & MASK32
            base = offset + i * dims["c_stride"] + 4 * j
            for byte in range(4):
                buffer[base + byte] = (value >> (8 * byte)) & 0xFF
    return bytes(buffer)


DOT_K = (1, 4, 7, 16, 64, 256, 1024)
FIR_K = (4, 16, 64, 256)
GEMM_SHAPES = ((1, 1, 1), (1, 3, 4), (3, 5, 8), (4, 4, 16), (8, 8, 31),
               (16, 16, 64), (32, 32, 32))
PLAN_PLACEMENTS = ("aligned", "a_plus1")
PLAN_CACHE = (0, 1)


def study_plan() -> list[dict[str, object]]:
    """The predeclared Phase 10 primary study, independent of any capture."""
    plan: list[dict[str, object]] = []
    capture = 0
    for name in KERNELS:
        if name == "dot":
            shapes = [(1, 1, k, 8) for k in DOT_K]
        elif name == "fir":
            shapes = [(8, 1, k, 8) for k in FIR_K]
        else:
            shapes = [(m, n, k, 8) for (m, n, k) in GEMM_SHAPES]
        for (m, n, k, taps) in shapes:
            for placement in PLAN_PLACEMENTS:
                for l1 in PLAN_CACHE:
                    for method in METHODS:
                        capture += 1
                        plan.append({"capture": capture, "kernel": name, "method": method,
                                     "m": m, "n": n, "k": k, "taps": taps,
                                     "placement": placement, "l1": l1})
    return plan


def expected_inputs(name: str, fields: dict[str, str]) -> tuple[bytes, bytes]:
    dims = shape(name, fields)
    seed = _hex32(fields, "seed")
    a_off, b_off = _int(fields, "a_offset"), _int(fields, "b_offset")
    a = bytearray([0xA5]) * _int(fields, "a_allocation_bytes")
    b = bytearray([0xA5]) * _int(fields, "b_allocation_bytes")
    for i in range(dims["a_used"]):
        a[a_off + i] = a_value(i, seed)
    for i in range(dims["b_used"]):
        b[b_off + i] = b_value(i, seed)
    return bytes(a), bytes(b)


def validate_line(line: str, *, method: str | None = None, kernel: str | None = None,
                  jobs: int | None = None) -> dict[str, int]:
    fields = _parse_fields(line)
    require(_int(fields, "version") == 8, "version is not 8")
    name = fields["name"]
    require(name in KERNELS, "unknown kernel name")
    got_method = fields["method"]
    require(got_method in METHODS, "unknown method")
    require(fields["placement"] in PLACEMENTS, "unknown placement")
    require(fields["status"] == "PASS", "record status is not PASS")
    require(fields["window"] == "cpu_command_to_result_visible", "window differs from the contract")
    require(fields["policy"] == "prepared_reinitialize", "policy differs from the contract")
    if method is not None:
        require(got_method == method, "record method differs from the requested method")
    if kernel is not None:
        require(name == kernel, "record kernel differs from the requested kernel")

    dims = shape(name, fields)
    require(_int(fields, "outputs") == dims["outputs"], "outputs field disagrees with the shape")
    require(_int(fields, "a_stride") == dims["a_stride"], "a_stride disagrees with the kernel contract")
    require(_int(fields, "b_stride") == dims["b_stride"], "b_stride disagrees with the kernel contract")
    require(_int(fields, "c_stride") == dims["c_stride"], "c_stride disagrees with the kernel contract")
    require(_int(fields, "workers") == (2 if got_method == "multicore" else 1),
            "workers disagrees with the method")
    require(_int(fields, "errors") == 0, "firmware reported internal errors")
    require(_int(fields, "jobs") >= 1, "jobs must be positive")
    require(1 <= _int(fields, "job") <= _int(fields, "jobs"), "job index out of range")
    if jobs is not None:
        require(_int(fields, "jobs") == jobs, "jobs field differs from the requested count")

    a_off, b_off, c_off = PLACEMENTS[fields["placement"]]
    require(_int(fields, "a_offset") == a_off and _int(fields, "b_offset") == b_off
            and _int(fields, "c_offset") == c_off, "placement offsets disagree")
    for key, used, offset in (("a", dims["a_used"], a_off), ("b", dims["b_used"], b_off),
                              ("c", dims["outputs"] * 4, c_off)):
        require(_int(fields, f"{key}_allocation_bytes") == allocation(used, offset),
                f"{key} allocation size disagrees with the contract")

    require(_int(fields, "clock_hz") == CLOCK_HZ, "clock_hz is not the configured fabric clock")
    require(_int(fields, "cpu_abi") == 4, "CPU counter ABI is not the coherent ABI")
    require(_int(fields, "dma_abi") == 1 and _int(fields, "dma_counter_abi") == 5,
            "DMA ABI is not the Phase 7 ABI")
    require(_int(fields, "dot8_instruction_abi") == 1 and _int(fields, "dot8_counter_abi") == 6,
            "DOT8 ABI is not the Phase 8 ABI")
    require(_int(fields, "npu_descriptor_abi") == 1 and _int(fields, "npu_counter_abi") == 1,
            "NPU ABI is not the Phase 9 ABI")

    a, b = expected_inputs(name, fields)
    for key, allocation_key in (("a_hex", "a_allocation_bytes"), ("b_hex", "b_allocation_bytes"),
                                ("output_hex", "c_allocation_bytes")):
        require(len(_bytes(fields, key)) == _int(fields, allocation_key),
                f"{key} length disagrees with {allocation_key}")
    require(_bytes(fields, "a_hex") == a, "A allocation differs from the seeded inputs")
    require(_bytes(fields, "b_hex") == b, "B allocation differs from the seeded inputs")
    output = _bytes(fields, "output_hex")
    require(output == expected_output(name, fields, a, b), "output differs from the independent signed oracle")

    cpu = {f"h{hart}_{event}": _hex64(fields, f"h{hart}_{event}")
           for hart in (0, 1) for event in CPU_EVENTS}
    require(cpu["h0_cycles"] > 0 and cpu["h0_retired"] > 0, "primary hart recorded no work")
    if got_method == "multicore":
        require(cpu["h1_cycles"] > 0 and cpu["h1_retired"] > 0, "secondary hart recorded no work")
    else:
        require(all(value == 0 for key, value in cpu.items()
                    if key.startswith("h1_") and key != "h1_cycles"),
                "inactive secondary hart recorded work events")
        require(cpu["h1_cycles"] == cpu["h0_cycles"],
                "secondary cycle counter disagrees with the common window")

    dot8 = {f"h{hart}_dot8_{event}": _hex64(fields, f"h{hart}_dot8_{event}")
            for hart in (0, 1) for event in DOT8_EVENTS}
    if got_method == "dot8" and dims["k"] >= 4:
        require(dot8["h0_dot8_accept"] > 0 and dot8["h0_dot8_retired"] > 0,
                "DOT8 method did not retire custom instructions")
    else:
        require(all(value == 0 for value in dot8.values()),
                "DOT8 counters are nonzero where no packed group is possible")

    npu = {key: _int(fields, key) for key in ("npu_active", "npu_status", "npu_error_code",
                                              "npu_bytes_read", "npu_bytes_written", "npu_tiles")}
    npu["npu_job_cycles"] = _hex64(fields, "npu_job_cycles")
    npu["npu_compute_cycles"] = _hex64(fields, "npu_compute_cycles")
    if got_method == "npu":
        require(npu["npu_active"] == 1, "NPU method did not record NPU activity")
        require(npu["npu_status"] & 0x2, "NPU method did not report DONE")
        require(npu["npu_status"] & 0x4 == 0 and npu["npu_error_code"] == 0, "NPU method reported an error")
        require(npu["npu_bytes_written"] == dims["outputs"] * 4,
                "NPU method wrote an unexpected byte count")
        if dims["k"] > 0:
            require(npu["npu_bytes_read"] > 0, "NPU method recorded no operand reads")
        else:
            require(npu["npu_bytes_read"] == 0, "NPU K=0 job read operands")
        require(npu["npu_tiles"] > 0 and npu["npu_job_cycles"] > 0, "NPU method recorded no tiles/cycles")
    else:
        require(npu["npu_active"] == 0, "non-NPU method recorded NPU activity")
        require(all(npu[key] == 0 for key in ("npu_status", "npu_error_code", "npu_bytes_read",
                                              "npu_bytes_written", "npu_tiles", "npu_job_cycles",
                                              "npu_compute_cycles")), "non-NPU method recorded NPU fields")

    return {"name": name, "method": got_method, "job": _int(fields, "job"),
            "k": dims["k"], "m": dims["m"], "n": dims["n"],
            "h0_cycles": cpu["h0_cycles"], "h0_retired": cpu["h0_retired"],
            "h1_cycles": cpu["h1_cycles"]}


def _parse_stop(line: str) -> dict[str, int]:
    require("\r" not in line and line.startswith("ASTERSTOP,"), "invalid ASTERSTOP line")
    require(line.endswith("\n") and line.count("\n") == 1, "incomplete ASTERSTOP line")
    fields: dict[str, int] = {}
    for item in line[len("ASTERSTOP,"):-1].split(","):
        require(item.count("=") == 1, "malformed ASTERSTOP field")
        key, value = item.split("=", 1)
        require(key in {"records", "device_transactions", "npu_transactions"} and key not in fields,
                "unknown or duplicate ASTERSTOP field")
        require(_DECIMAL.match(value) is not None, "noncanonical ASTERSTOP number")
        fields[key] = int(value, 10)
    require(set(fields) == {"records", "device_transactions", "npu_transactions"}, "incomplete ASTERSTOP")
    require(fields["npu_transactions"] <= fields["device_transactions"], "invalid transaction totals")
    return fields


def validate_stream(lines: Iterable[str], **kwargs) -> list[dict[str, int]]:
    results = []
    stops: list[dict[str, int]] = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("ASTERBENCH,"):
            results.append(validate_line(line if line.endswith("\n") else line + "\n", **kwargs))
        elif line.startswith("ASTERSTOP,"):
            stops.append(_parse_stop(line if line.endswith("\n") else line + "\n"))
        else:
            raise ValidationError("log contains a line that is neither a record nor ASTERSTOP")
    require(results, "no v8 records were provided")
    if stops:
        require(len(stops) == 1, "log must contain at most one ASTERSTOP")
        require(stops[0]["records"] == len(results), "ASTERSTOP record count disagrees with the log")
        if results[0]["method"] == "npu":
            require(stops[0]["npu_transactions"] > 0 and stops[0]["device_transactions"] > 0,
                    "NPU log contains no coherent device traffic")
        else:
            require(stops[0]["device_transactions"] == 0, "non-NPU log contains device traffic")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--kernel", choices=KERNELS)
    parser.add_argument("--jobs", type=int)
    args = parser.parse_args()
    try:
        results = validate_stream(sys.stdin, method=args.method, kernel=args.kernel, jobs=args.jobs)
    except ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for result in results:
        print(f"PASS: v8 {result['name']}/{result['method']} job={result['job']} "
              f"m={result['m']} n={result['n']} k={result['k']} "
              f"h0_cycles={result['h0_cycles']} h1_cycles={result['h1_cycles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
