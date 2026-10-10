#!/usr/bin/env python3
"""Planted bugs in the L1 caches' narrow tags (TAG_SPAN, milestone 20.3): each
drops one kept tag bit (IB-16 at TAG_SPAN 17, IB = log2 of the capacity: 12 at
4 KiB) from one of the three narrow compares, the data cache's lookup and snoop
hit and the instruction cache's lookup, and must be caught on every seed by the
caches' unit tests built at TAG_SPAN 17 (`make core-aster-l1-unit`'s span
builds: their pages set every kept bit, the data cache's snoops too). The
unmutated caches must pass the same seeds. 20.5: at each capacity given
(--cache-bytes; 2, 4 and 8 KiB by default), the tests built at that size.

Usage: l1_span_mutants.py <build-dir> [--seeds N] [--jobs N] [--cache-bytes 2048,4096,8192]
"""
import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl/aster_core"
SPAN = 17

# (cache, the narrow compare as written, its tag side, its address side)
SITES = {
    "l1d-lookup": ("d", "tag_ram[s1_addr[IB-1:4]] == s1_addr[TAG_SPAN-1:IB]",
                   "tag_ram[s1_addr[IB-1:4]]", "s1_addr"),
    "l1d-snoop": ("d", "tag_ram[snoop_line[p][IB-1:4]] == snoop_line[p][TAG_SPAN-1:IB]",
                  "tag_ram[snoop_line[p][IB-1:4]]", "snoop_line[p]"),
    "l1i-lookup": ("i", "tag_ram[s1_addr[IB-1:4]] == s1_addr[TAG_SPAN-1:IB]",
                   "tag_ram[s1_addr[IB-1:4]]", "s1_addr"),
}


def dropped(tag: str, addr: str, bit: int, ib: int) -> str:
    """The compare without address bit `bit` (tag bit bit - ib; the tag's lowest address bit is ib)."""
    i, tb = bit - ib, SPAN - ib
    tag_parts, addr_parts = [], []
    if i < tb - 1:
        tag_parts.append(f"{tag}[{tb - 1}:{i + 1}]")
        addr_parts.append(f"{addr}[{SPAN - 1}:{bit + 1}]")
    if i > 0:
        tag_parts.append(f"{tag}[{i - 1}:0]")
        addr_parts.append(f"{addr}[{bit - 1}:{ib}]")
    return "{" + ", ".join(tag_parts) + "} == {" + ", ".join(addr_parts) + "}"


def build(out: Path, cache: str, source: Path, cache_bytes: int) -> Path:
    """The cache's span-17 unit test at that capacity, with `source` as its RTL."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    common = ["verilator", "--cc", "--exe", "--build", "--assert", "-Wno-fatal", "-GTAG_SPAN=17", f"-GCACHE_BYTES={cache_bytes}",
              "-CFLAGS", f"-DL1_CACHE_BYTES={cache_bytes}u", "--Mdir", str(out / "obj"),
              "-o", str(out / "sim")]
    if cache == "d":
        rtl = [RTL / "aster_core_pkg.sv", RTL / "aster_l1_ram.sv", RTL / "aster_l1i.sv", source,
               ROOT / "verification/core/l1/l1d_unit.sv", ROOT / "verification/core/l1/tb_l1d.cpp"]
        cmd = common + ["--top-module", "l1d_unit", "--prefix", "Vl1d_unit", "-GSNOOPS=3", *map(str, rtl),
                        "-CFLAGS", "-DL1D_SNOOPS=3", "-CFLAGS", "-DL1D_SPAN17"]
    else:
        rtl = [RTL / "aster_l1_ram.sv", source, ROOT / "verification/core/l1/tb_l1i.cpp"]
        cmd = common + ["--top-module", "aster_l1i", "--prefix", "Vaster_l1i", *map(str, rtl), "-CFLAGS", "-DL1I_SPAN17"]
    log = out / "build.log"
    with log.open("w") as f:
        if subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode != 0:
            raise RuntimeError(f"build failed: {log}")
    return out / "sim"


def run(sim: Path, seeds: int) -> list[bool]:
    """Each seed's outcome: True if it passed."""
    passed = []
    for seed in range(1, seeds + 1):
        r = subprocess.run([str(sim), str(seed), "1000000"], capture_output=True, text=True)
        passed.append(r.returncode == 0 and "PASS" in r.stdout and "%Error" not in r.stdout + r.stderr)
    return passed


def one(name: str, cache: str, text: str, out: Path, seeds: int, cache_bytes: int) -> str:
    src = (RTL / ("aster_l1d.sv" if cache == "d" else "aster_l1i.sv")).read_text()
    if name == "control":
        source_text = src
    else:
        site, bit = name.rsplit("-bit", 1)
        _, anchor, tag, addr = SITES[site]
        if src.count(anchor) != 1:
            return f"{name}: ANCHOR matches {src.count(anchor)} times"
        source_text = src.replace(anchor, dropped(tag, addr, int(bit), cache_bytes.bit_length() - 1))
    work = out / name
    work.mkdir(parents=True, exist_ok=True)
    source = work / ("aster_l1d.sv" if cache == "d" else "aster_l1i.sv")
    source.write_text(source_text)
    try:
        sim = build(work / "build", cache, source, cache_bytes)
    except RuntimeError as e:
        return f"{name}: BUILD FAILED ({e})"
    passed = run(sim, seeds)
    if name == "control":
        return f"{name}: {'PASS' if all(passed) else 'FAIL'} ({sum(passed)} of {seeds} seeds pass)"
    caught = seeds - sum(passed)
    return f"{name}: {'CAUGHT' if caught == seeds else 'MISSED'} ({caught} of {seeds} seeds fail)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--cache-bytes", default="2048,4096,8192", help="the capacities (20.5)")
    args = parser.parse_args()
    sizes = [int(b) for b in args.cache_bytes.split(",")]
    if any(b not in (2048, 4096, 8192) for b in sizes) or len(set(sizes)) != len(sizes):
        parser.error("--cache-bytes: each of 2048, 4096 and 8192 at most once")
    jobs = []
    for b in sizes:
        ib = b.bit_length() - 1
        jobs += [(b, "control-d", "d"), (b, "control-i", "i")]
        jobs += [(b, f"{site}-bit{bit}", SITES[site][0]) for site in SITES for bit in range(ib, SPAN)]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = {}
        for b, n, c in jobs:
            out = args.build_dir.resolve() / f"c{b}"
            futures[pool.submit(one, "control" if n.startswith("control") else n, c, "",
                                out / n if n.startswith("control") else out, args.seeds, b)] = (b, n)
        for f in concurrent.futures.as_completed(futures):
            results[futures[f]] = f.result()
    for b, n, _ in jobs:
        line = results[(b, n)]
        print(f"{b // 1024} KiB " + (line if not n.startswith("control") else f"{n}: {line.split(': ', 1)[1]}"))
    mutants = [results[(b, n)] for b, n, _ in jobs if not n.startswith("control")]
    controls_ok = all("PASS" in results[(b, n)] for b, n, _ in jobs if n.startswith("control"))
    caught = sum(": CAUGHT" in r for r in mutants)
    ok = controls_ok and caught == len(mutants)
    print(f"{'PASS' if ok else 'FAIL'}: planted bugs in the caches' narrow tags: {caught} of {len(mutants)} caught on every "
          f"one of {args.seeds} seeds by the TAG_SPAN 17 unit tests at {', '.join(f'{b // 1024}' for b in sizes)} KiB; "
          f"the unmutated caches pass them")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
