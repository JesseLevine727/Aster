#!/usr/bin/env python3
"""Audit real v6 ELF/ROM and invoke the independent RTL scoreboard (not a shell)."""
import argparse
from pathlib import Path
import subprocess

import asterbench_dot8 as bench
from coherent_elf import inspect_elf


def command(args):
    found = inspect_elf(args.elf.read_bytes(), profile="dot8_benchmark", dot8_name=args.workload, dot8_k=args.k, dot8_jobs=args.jobs)
    words = args.firmware.read_text().split()
    bench.require(len(words) == 16384 and all(len(w) == 8 and all(c in "0123456789abcdefABCDEF" for c in w) for w in words), "complete canonical ROM required")
    image = b"".join(int(w,16).to_bytes(4,"little") for w in words)
    bench.require(image == found["image"], "actual ROM differs from audited ELF load bytes")
    invocation = [str(args.simulator), "+rom="+str(args.firmware), "+ram_fill=a5a5a5a5", "--ram-prefix", str(args.ram_prefix)]
    for method in ("scalar", "custom"):
        kernel = found["symbols"][f"aster_dot8_{method}_{args.workload}"]
        invocation += ["--"+method+"-start", str(kernel["address"]), "--"+method+"-end", str(kernel["address"]+kernel["size"])]
    invocation += ["--kind", str(bench.NAMES.index(args.workload))]
    for key in ("k", "alignment", "harts", "jobs", "seed", "l1", "sync_memory", "memory_wait", "line_words", "line_count", "boots", "uart_seed"):
        invocation += ["--"+key.replace("_","-"), str(getattr(args,key))]
    return invocation


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator", "elf", "firmware", "ram-prefix"): p.add_argument("--"+key, type=Path, required=True)
    p.add_argument("--workload", choices=bench.NAMES, required=True)
    for key in ("k", "alignment", "harts", "jobs", "seed", "l1", "sync-memory", "memory-wait", "line-words", "line-count", "boots", "uart-seed"):
        p.add_argument("--"+key, type=lambda value: int(value,0), required=True)
    return subprocess.call(command(p.parse_args()))


if __name__ == "__main__": raise SystemExit(main())
