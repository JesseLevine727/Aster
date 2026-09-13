#!/usr/bin/env python3
"""Audit actual v5 ELF/ROM before the AXI/bit-serial DMA scoreboard."""
import argparse
from pathlib import Path
import re
import subprocess

from asterbench_dma import integer, require
from coherent_elf import inspect_elf


def command(args):
    integer(args.size,0,8192); integer(args.alignment,0,2); integer(args.jobs,1,8); integer(args.boots,1,16); integer(args.seed,0,0xffffffff)
    found = inspect_elf(args.elf.read_bytes(), profile="dma_benchmark", dma_size=args.size, dma_jobs=args.jobs)
    raw = args.firmware.read_bytes(); require(re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}",raw),"incomplete/noncanonical ROM")
    image = b"".join(int(word,16).to_bytes(4,"little") for word in raw.splitlines())
    require(image == found["image"],"ROM differs from actual ELF loads")
    symbols = found["symbols"]; kernel = symbols["aster_dma_cpu_memcpy"]
    values = [args.simulator,args.firmware,args.size,args.alignment,args.jobs,args.boots,args.seed,kernel["address"],
              kernel["address"]+kernel["size"],symbols["aster_dma_bench_results"]["address"],
              symbols["aster_dma_bench_source"]["address"],symbols["aster_dma_bench_destination"]["address"]]
    return list(map(str,values))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator","elf","firmware"): p.add_argument("--"+key,type=Path,required=True)
    for key in ("size","alignment","jobs","boots","seed"): p.add_argument("--"+key,type=lambda s:int(s,0),required=True)
    args = p.parse_args()
    return subprocess.call(command(args))


if __name__ == "__main__":
    raise SystemExit(main())
