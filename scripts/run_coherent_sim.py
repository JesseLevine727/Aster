#!/usr/bin/env python3
"""Resolve real Phase 6 ELF symbols and run the coherent event/RAM scoreboard."""
import argparse
import re
import subprocess


def symbols(elf, nm, items, jobs):
    listing = subprocess.check_output([nm, "-S", "--defined-only", str(elf)], text=True)
    resolved = {}
    for name, kind, low, high, expected in (
        ("aster_coherent_kernel", "tT", 0, 65536, None),
        ("aster_coherent_results", "bBdD", 0x10008000, 0x1000b000, jobs*32),
        ("aster_coherent_output", "bBdD", 0x10000000, 0x10008000, items*4),
    ):
        matches = re.findall(rf"^([0-9a-fA-F]+) ([0-9a-fA-F]+) [{kind}] {name}$", listing, re.M)
        if len(matches) != 1:
            raise ValueError(f"missing/ambiguous symbol {name}")
        address, size = (int(v, 16) for v in matches[0])
        if address % 4 or not low <= address < address+size <= high or (expected is not None and size != expected):
            raise ValueError(f"invalid range/size for {name}")
        resolved[name] = {"address": address, "size": size}
    return resolved


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator", "elf", "firmware", "nm", "ram-prefix"):
        p.add_argument("--"+key, required=True)
    for key in ("kind", "items", "rounds", "workers", "harts", "jobs", "seed", "l1", "sync-memory",
                "memory-wait", "line-words", "line-count", "boots", "uart-seed"):
        p.add_argument("--"+key, type=lambda s: int(s, 0), required=True)
    args = p.parse_args()
    found = symbols(args.elf, args.nm, args.items, args.jobs)
    kernel = found["aster_coherent_kernel"]
    cmd = [args.simulator, "+rom="+args.firmware, "+ram_fill=a5a5a5a5",
           "--kernel-start", str(kernel["address"]), "--kernel-end", str(kernel["address"]+kernel["size"]),
           "--result-addr", str(found["aster_coherent_results"]["address"]),
           "--output-addr", str(found["aster_coherent_output"]["address"])]
    for key, value in vars(args).items():
        if key not in ("simulator", "elf", "firmware", "nm"):
            cmd += ["--"+key.replace("_", "-"), str(value)]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
