#!/usr/bin/env python3
"""Independent checksum oracle for the generic AsterBench v10 workloads.

Recomputes each workload's expected checksum from its size, iteration count,
parameter and seed, and compares it against the emitted record. This is the
host reference the structural validator intentionally does not carry.
"""
from __future__ import annotations

import argparse
import bisect
import math
import sys

import asterbench_v10 as bench

MASK = 0xFFFFFFFF


def round_half_away(value: float) -> int:
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def fft_twiddles(count: int):
    rows = []
    for k in range(count // 2):
        angle = 2.0 * math.pi * k / count
        rows.append((round_half_away(math.cos(angle) * 32767), round_half_away(-math.sin(angle) * 32767)))
    return rows


def fft_checksum(size: int, iterations: int, param: int, seed: int) -> int:
    count = param
    twiddles = fft_twiddles(count)
    checksum = 0
    for _ in range(iterations):
        real = [((seed ^ ((i * 0x1021) & MASK)) & 0xFFFF) - 32768 for i in range(count)]
        imag = [0] * count
        j = 0
        for i in range(1, count):
            bit = count >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                real[i], real[j] = real[j], real[i]
                imag[i], imag[j] = imag[j], imag[i]
        length = 2
        while length <= count:
            half = length >> 1
            step = count // length
            for start in range(0, count, length):
                for k in range(half):
                    wr, wi = twiddles[k * step]
                    xr, xi = real[start + k], imag[start + k]
                    yr, yi = real[start + k + half], imag[start + k + half]
                    tr = (wr * yr - wi * yi) >> 15
                    ti = (wr * yi + wi * yr) >> 15
                    real[start + k] = (xr + tr) >> 1
                    imag[start + k] = (xi + ti) >> 1
                    real[start + k + half] = (xr - tr) >> 1
                    imag[start + k + half] = (xi - ti) >> 1
            length <<= 1
        for value in real:
            checksum = ((checksum * 33) ^ (value & MASK)) & MASK
        for value in imag:
            checksum = ((checksum * 33) ^ (value & MASK)) & MASK
    return checksum


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


def signed8(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def conv2d_checksum(size: int, iterations: int, param: int, seed: int) -> int:
    height, width, kernel_size = 32, 32, param
    out_h, out_w = height - kernel_size + 1, width - kernel_size + 1
    checksum = 0
    for _ in range(iterations):
        image = [signed8((seed ^ ((i * 0x1021) & MASK)) & 0xFF) for i in range(height * width)]
        kernel = [signed8((seed ^ ((i * 0x9E3779B9) & MASK)) & 0xFF)
                  for i in range(kernel_size * kernel_size)]
        for oy in range(out_h):
            for ox in range(out_w):
                total = 0
                for ky in range(kernel_size):
                    for kx in range(kernel_size):
                        total += image[(oy + ky) * width + (ox + kx)] * kernel[ky * kernel_size + kx]
                checksum = ((checksum * 33) ^ (total & MASK)) & MASK
    return checksum


def reduce_checksum(size: int, iterations: int, param: int, seed: int) -> int:
    words = size // 4
    checksum = 0
    for _ in range(iterations):
        total = 0
        for index in range(words):
            total = (total + (seed ^ ((index * 0x1021) & MASK))) & MASK
        checksum = ((checksum * 33) ^ total) & MASK
    return checksum


def expected_checksum(name: str, size: int, iterations: int, param: int, seed: int) -> int:
    if name == "strided":
        return strided_checksum(size, iterations, param, seed)
    if name == "sort_search":
        return sort_checksum(size, iterations, seed)
    if name == "fft":
        return fft_checksum(size, iterations, param, seed)
    if name == "conv2d":
        return conv2d_checksum(size, iterations, param, seed)
    if name in ("reduce_parallel", "reduce_scalar"):
        return reduce_checksum(size, iterations, param, seed)
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
