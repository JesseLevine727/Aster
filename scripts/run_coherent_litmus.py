#!/usr/bin/env python3
"""Resolve the real litmus observation symbols before running the RTL oracle."""
import argparse
import re
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator", "elf", "firmware", "nm"):
        p.add_argument("--"+key, required=True)
    for key in ("epochs", "steps", "seed"):
        p.add_argument("--"+key, type=lambda s: int(s, 0), required=True)
    a = p.parse_args()
    listing = subprocess.check_output([a.nm, "-S", "--defined-only", a.elf], text=True)
    args = {}
    for symbol, typ, expected, low, high in (("aster_litmus_trial", "bBdD", 32, 0x10008000, 0x1000b000),
            ("aster_litmus_summary", "bBdD", 2048, 0x10008000, 0x1000b000),
            ("aster_litmus_kernel", "tT", None, 0, 65536)):
        found = re.findall(rf"^([0-9a-fA-F]+) ([0-9a-fA-F]+) [{typ}] {symbol}$", listing, re.M)
        if len(found) != 1: raise ValueError("missing/ambiguous litmus symbol")
        address, size = (int(x, 16) for x in found[0])
        if not low <= address < address+size <= high or address % 4 or (expected is not None and size != expected):
            raise ValueError("invalid litmus symbol range")
        if expected is None: args.update({"kernel-start": address, "kernel-end": address+size})
        else: args[symbol.removeprefix("aster_litmus_")] = address
    cmd = [a.simulator, "+rom="+a.firmware, "+ram_fill=a5a5a5a5"]
    args.update(epochs=a.epochs, steps=a.steps, seed=a.seed)
    for key, value in args.items(): cmd.extend(["--"+key, str(value)])
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
