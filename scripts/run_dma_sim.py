#!/usr/bin/env python3
"""Audit actual v5 ELF symbols/boot bytes, then run the DMA/CPU RTL scoreboard."""
import argparse
from pathlib import Path
import subprocess

from asterbench_dma import require
from coherent_elf import inspect_elf


def command(args):
    found = inspect_elf(args.elf.read_bytes(), profile="dma_benchmark", dma_size=args.size, dma_jobs=args.jobs)
    # Firmware text is interpreted as hex data only, never run as a host command.
    words = args.firmware.read_text().split()
    require(len(words) == 16384 and all(len(word) == 8 and all(c in "0123456789abcdefABCDEF" for c in word) for word in words),
            "expected complete 64 KiB canonical ROM words")
    image = b"".join(int(word, 16).to_bytes(4, "little") for word in words)
    require(image == found["image"], "ROM differs from audited actual ELF load bytes")
    symbols = found["symbols"]; kernel = symbols["aster_dma_cpu_memcpy"]
    invocation = [str(args.simulator), "+rom="+str(args.firmware), "+ram_fill=a5a5a5a5",
        "--kernel-start", str(kernel["address"]), "--kernel-end", str(kernel["address"]+kernel["size"]),
        "--results-addr", str(symbols["aster_dma_bench_results"]["address"]),
        "--source-base", str(symbols["aster_dma_bench_source"]["address"]),
        "--destination-base", str(symbols["aster_dma_bench_destination"]["address"]),
        "--ram-prefix", str(args.ram_prefix)]
    for key in ("size", "alignment", "harts", "jobs", "seed", "l1", "sync_memory", "memory_wait", "line_words", "line_count", "boots", "uart_seed"):
        invocation += ["--"+key.replace("_", "-"), str(getattr(args, key))]
    return invocation


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("simulator", "elf", "firmware", "ram-prefix"):
        p.add_argument("--"+key, type=Path, required=True)
    for key in ("size", "alignment", "harts", "jobs", "seed", "l1", "sync-memory", "memory-wait", "line-words", "line-count", "boots", "uart-seed"):
        p.add_argument("--"+key, type=lambda value: int(value, 0), required=True)
    args = p.parse_args()
    return subprocess.call(command(args))


if __name__ == "__main__":
    raise SystemExit(main())
