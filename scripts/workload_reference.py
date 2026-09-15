#!/usr/bin/env python3
"""Independent checksum oracle for the generic AsterBench v10 workloads.

Recomputes each workload's expected checksum from its size, iteration count,
parameter and seed, and compares it against the emitted record. This is the
host reference the structural validator intentionally does not carry.
"""
from __future__ import annotations

import argparse
import bisect
import sys

import asterbench_v10 as bench

MASK = 0xFFFFFFFF


def strided_checksum(size: int, iterations: int, param: int, seed: int) -> int:
    words = size // 4
    total = 0
    for index in range(0, words, param):
        total = (total + ((seed ^ (index * 0x1021)) & MASK)) & MASK
    return (total * iterations) & MASK


def sort_generate(index: int, seed: int) -> int:
    value = (seed ^ (index * 0x9E3779B9)) & MASK
    value ^= value >> 16
    value = (value * 0x7FEB352D) & MASK
    value ^= value >> 15
    value = (value * 0x846CA68B) & MASK
    value ^= value >> 16
    return value & MASK


def sort_checksum(size: int, iterations: int, seed: int) -> int:
    count = size // 4
    checksum = 0
    for _ in range(iterations):
        values = sorted(sort_generate(index, seed) for index in range(count))
        for value in values:
            checksum = ((checksum * 33) ^ value) & MASK
        found = bisect.bisect_left(values, values[count // 3])
        checksum = ((checksum * 33) ^ found) & MASK
    return checksum


def expected_checksum(name: str, size: int, iterations: int, param: int, seed: int) -> int:
    if name == "strided":
        return strided_checksum(size, iterations, param, seed)
    if name == "sort_search":
        return sort_checksum(size, iterations, seed)
    raise bench.ValidationError(f"no checksum reference for workload {name!r}")


def verify(record: dict, name: str | None = None) -> dict:
    require = bench.require
    if name is not None:
        require(record["name"] == name, "record name differs from the requested workload")
    expected = expected_checksum(record["name"], record["size"], record["iterations"],
                                 record["param"], record["seed"])
    require(record["checksum"] == expected,
            f"checksum {record['checksum']:#010x} disagrees with independent oracle {expected:#010x}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify"])
    parser.add_argument("--name")
    args = parser.parse_args()
    try:
        records = [bench.validate_line(line if line.endswith("\n") else line + "\n")
                   for line in sys.stdin if line.strip()]
        require = bench.require
        require(records, "no v10 records were provided")
        for record in records:
            verify(record, args.name)
    except bench.ValidationError as error:
        sys.stderr.write(f"FAIL: {error}\n")
        return 1
    for record in records:
        print(f"PASS: v10 {record['name']} checksum={record['checksum']:#010x} "
              f"matches independent oracle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
