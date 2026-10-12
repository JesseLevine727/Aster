#!/usr/bin/env python3
"""Copy a timing result into a retained, checksummed evidence folder (--phase picks the results folder, phase18 by
default).

Vivado out-of-context results (scripts/timing/vivado_ooc.tcl) live in git-ignored directories. This copies the files
a timing claim rests on into docs/results/<phase>/<name>/fpga/NAME/ and writes SHA256SUMS over the folder: from each
Vivado directory (--fpga NAME=DIR), summary.txt, timing_summary.rpt, utilization.rpt, clock.xdc, and named_paths.rpt
when the run named paths.

A README.md already in the folder is kept (and checksummed); write it after retaining, then re-run with --rehash.

    retain.py aster-core-timing --fpga core=build/timing/fpga/aster
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RESULTS = ROOT / "docs/results/phase18"
FPGA_FILES = ("summary.txt", "timing_summary.rpt", "utilization.rpt", "clock.xdc")
FPGA_OPTIONAL = ("named_paths.rpt",)         # when the run named paths (OOC_NAMED_PATHS)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs(values: list[str]) -> list[tuple[str, Path]]:
    out = []
    for value in values:
        name, _, path = value.partition("=")
        if not name or not path:
            raise SystemExit(f"expected NAME=PATH, got {value!r}")
        out.append((name, Path(path)))
    return out


def retain_fpga(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in FPGA_FILES:
        shutil.copy2(source / name, target / name)
    for name in FPGA_OPTIONAL:
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)


def rehash(folder: Path) -> None:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (folder / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(folder)}\n" for p in files))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="folder under docs/results/<phase>/")
    parser.add_argument("--phase", default="phase18", help="the results folder's phase (default phase18)")
    parser.add_argument("--fpga", action="append", default=[], metavar="NAME=DIR")
    parser.add_argument("--rehash", action="store_true", help="only rewrite SHA256SUMS")
    args = parser.parse_args()
    folder = RESULTS.parent / args.phase / args.name
    if not args.rehash:
        for name, source in pairs(args.fpga):
            retain_fpga(source, folder / "fpga" / name)
    rehash(folder)
    print(f"retained {folder.relative_to(ROOT)}: "
          f"{sum(1 for p in folder.rglob('*') if p.is_file())} files, SHA256SUMS written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
