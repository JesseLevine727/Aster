#!/usr/bin/env python3
"""Strict, independent AsterBench v10 validator for generic workloads.

One line per workload with a category, size, iteration count, seed, a
self-checked checksum and the common hardware counters. Earlier versions
(v2-v9) are unchanged.
"""

from __future__ import annotations

import argparse
import re
import sys

CATEGORIES = ("cpu", "memory", "dsp", "ml", "system")
STRING_FIELDS = {"name", "category", "status"}
DECIMAL_FIELDS = {"version", "size", "iterations", "param", "clock_hz", "l1", "sync_memory",
                  "line_words", "line_count", "memory_wait"}
HEX32_FIELDS = {"seed", "checksum"}
HEX64_FIELDS = {"cycles", "retired", "memory_transactions", "backing_transactions",
                "cache_accesses", "cache_misses", "dma_bytes", "accelerator_cycles"}
ALL_FIELDS = STRING_FIELDS | DECIMAL_FIELDS | HEX32_FIELDS | HEX64_FIELDS

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_HEX32 = re.compile(r"0x[0-9a-f]{8}\Z")
_HEX64 = re.compile(r"0x[0-9a-f]{16}\Z")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")


class ValidationError(ValueError):
    """A record violates the v10 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _int(fields: dict[str, str], key: str) -> int:
    require(_DECIMAL.match(fields[key]) is not None, f"field {key} is not a canonical decimal")
    return int(fields[key])


def _hex32(fields: dict[str, str], key: str) -> int:
    require(_HEX32.match(fields[key]) is not None, f"field {key} is not 8 hexadecimal digits")
    return int(fields[key], 16)


def _hex64(fields: dict[str, str], key: str) -> int:
    require(_HEX64.match(fields[key]) is not None, f"field {key} is not 16 hexadecimal digits")
    return int(fields[key], 16)


def _parse_fields(line: str) -> dict[str, str]:
    require(isinstance(line, str), "record is not text")
    require("\r" not in line, "record contains carriage return")
    require(len(line) <= 4096, "record exceeds v10 length bound")
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
    require(set(fields) == ALL_FIELDS, "record fields do not match the v10 schema exactly")
    return fields


def validate_line(line: str, *, name: str | None = None, category: str | None = None) -> dict[str, object]:
    fields = _parse_fields(line)
    require(_int(fields, "version") == 10, "version is not 10")
    require(_NAME.match(fields["name"]) is not None, "invalid workload name")
    require(fields["category"] in CATEGORIES, "unknown workload category")
    require(fields["status"] == "PASS", "record status is not PASS")
    if name is not None:
        require(fields["name"] == name, "record name differs from the requested workload")
    if category is not None:
        require(fields["category"] == category, "record category differs")

    size = _int(fields, "size")
    require(4 <= size <= 65536 and size % 4 == 0, "size must be 4..65536 and word aligned")
    require(_int(fields, "iterations") > 0, "iterations must be positive")
    require(_int(fields, "param") > 0, "param must be positive")
    require(_int(fields, "clock_hz") > 0, "clock_hz must be positive")
    require(_int(fields, "l1") in (0, 1) and _int(fields, "sync_memory") in (0, 1),
            "invalid cache configuration")
    for key in ("line_words", "line_count"):
        value = _int(fields, key)
        require(2 <= value <= 1024 and value & (value - 1) == 0, f"{key} must be a power of two, 2..1024")
    require(_int(fields, "sync_memory") <= _int(fields, "memory_wait") <= 1024,
            "memory_wait is out of range")

    cycles = _hex64(fields, "cycles")
    require(0 < _hex64(fields, "retired") <= cycles, "retired must be in (0, cycles]")
    require(0 < _hex64(fields, "memory_transactions") <= cycles, "memory_transactions out of range")
    require(0 < _hex64(fields, "backing_transactions") <= cycles, "backing_transactions out of range")
    require(_hex64(fields, "cache_misses") <= _hex64(fields, "cache_accesses") <= cycles,
            "cache counters out of range")
    return {"name": fields["name"], "category": fields["category"], "size": size,
            "iterations": _int(fields, "iterations"), "param": _int(fields, "param"), "seed": _hex32(fields, "seed"),
            "checksum": _hex32(fields, "checksum"), "cycles": cycles,
            "retired": _hex64(fields, "retired")}


def validate_stream(lines, **kwargs):
    results = []
    for line in lines:
        if not line.strip():
            continue
        results.append(validate_line(line if line.endswith("\n") else line + "\n", **kwargs))
    require(results, "no v10 records were provided")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--name")
    parser.add_argument("--category", choices=CATEGORIES)
    args = parser.parse_args()
    try:
        results = validate_stream(sys.stdin, name=args.name, category=args.category)
    except ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for result in results:
        print(f"PASS: v10 {result['name']} [{result['category']}] size={result['size']} "
              f"iterations={result['iterations']} cycles={result['cycles']} retired={result['retired']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
