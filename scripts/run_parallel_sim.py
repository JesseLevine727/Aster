#!/usr/bin/env python3
"""Resolve actual ELF kernel bounds, then invoke the parallel RTL scoreboard."""
import argparse
import re
import subprocess


def kernel_bounds(elf, nm):
    listing = subprocess.check_output([nm, "-S", "--defined-only", str(elf)], text=True)
    matches = re.findall(r"^([0-9a-fA-F]+) ([0-9a-fA-F]+) [tT] aster_parallel_kernel$", listing, re.M)
    if len(matches) != 1:
        raise ValueError("missing/ambiguous measured kernel symbol")
    begin, size = [int(x, 16) for x in matches[0]]
    if not 0 < size <= 65536-begin:
        raise ValueError("kernel is outside ROM")
    return begin, begin+size


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator", "elf", "firmware", "nm"):
        p.add_argument("--"+key, required=True)
    for key in ("jobs", "words", "rounds", "workers", "harts", "seed", "l1", "sync-memory",
                "memory-wait", "line-words", "line-count"):
        p.add_argument("--"+key, type=lambda s: int(s, 0), required=True)
    args = p.parse_args()
    begin, end = kernel_bounds(args.elf, args.nm)
    cmd = [args.simulator, "+rom="+args.firmware, "+ram_fill=a5a5a5a5",
           "--kernel-start", str(begin), "--kernel-end", str(end)]
    for key, value in vars(args).items():
        if key not in ("simulator", "elf", "firmware", "nm"):
            cmd += ["--"+key.replace("_", "-"), str(value)]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
