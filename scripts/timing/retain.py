#!/usr/bin/env python3
"""Copy a Phase 18 timing result into a retained, checksummed evidence folder.

Vivado out-of-context results (scripts/timing/vivado_ooc.tcl) and LibreLane
runs (scripts/run_asic.py) live in git-ignored directories. This copies the
files a timing claim rests on into docs/results/phase18/<name>/ and writes
SHA256SUMS over the folder:

- from each Vivado directory (--fpga NAME=DIR): summary.txt, timing_summary.rpt,
  utilization.rpt, clock.xdc, and named_paths.rpt when the run named paths,
  under fpga/NAME/;
- from each LibreLane run (--asic NAME=RUN): resolved.json, final/metrics.json,
  the post-route STA summary.rpt, each corner's worst register-to-register
  setup path verbatim (flip-flop to flip-flop; port paths, such as a reset
  input, are skipped; the full max.rpt files are several MB and their SHA-256
  goes in asic/NAME/max_rpt.sha256), and the sky130_summary.py JSON, under
  asic/NAME/.

A README.md already in the folder is kept (and checksummed); write it after
retaining, then re-run with --rehash.

    retain.py picorv32-baseline-v2 --fpga core=build/timing/fpga/picorv32 \\
        --asic core=asic/sky130/runs/p18-sdc-picorv32-delay-1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "timing"))
import sky130_summary  # noqa: E402

RESULTS = ROOT / "docs/results/phase18"
FPGA_FILES = ("summary.txt", "timing_summary.rpt", "utilization.rpt", "clock.xdc")
FPGA_OPTIONAL = ("named_paths.rpt",)         # when the run named paths (OOC_NAMED_PATHS)
WORST_PATH_CORNERS = ("nom_tt_025C_1v80", "max_tt_025C_1v80", "nom_ss_100C_1v60", "max_ss_100C_1v60")


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


def retain_asic(run: Path, target: Path) -> None:
    sta = sorted(run.glob("*-openroad-stapostpnr"))
    if not sta:
        raise SystemExit(f"{run}: no post-route STA step")
    sta = sta[-1]
    (target / "final").mkdir(parents=True, exist_ok=True)
    (target / sta.name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(run / "resolved.json", target / "resolved.json")
    shutil.copy2(run / "final/metrics.json", target / "final/metrics.json")
    shutil.copy2(sta / "summary.rpt", target / sta.name / "summary.rpt")
    worst = ["# Worst register-to-register setup path per corner, copied verbatim from each",
             "# corner's max.rpt (the first path from a flip-flop to a flip-flop).", ""]
    hashes = []
    for corner in WORST_PATH_CORNERS:
        report = sta / corner / "max.rpt"
        text = report.read_text()
        start = 0
        while True:
            start = text.index("Startpoint:", start)
            end = text.index("\n", text.index("slack (", start))
            block = text[start:end]
            header = block[:block.index("Path Group")]        # start- and endpoint descriptions
            if header.count("flip-flop") == 2:
                break
            start = end
        worst += [f"===== {corner} =====", block, ""]
        hashes.append(f"{sha256(report)}  {sta.name}/{corner}/max.rpt")
    (target / sta.name / "worst_paths.txt").write_text("\n".join(worst))
    (target / "max_rpt.sha256").write_text("\n".join(hashes) + "\n")
    summary = sky130_summary.summarize(target)
    (target / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def rehash(folder: Path) -> None:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (folder / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(folder)}\n" for p in files))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="folder under docs/results/phase18/")
    parser.add_argument("--fpga", action="append", default=[], metavar="NAME=DIR")
    parser.add_argument("--asic", action="append", default=[], metavar="NAME=RUN")
    parser.add_argument("--rehash", action="store_true", help="only rewrite SHA256SUMS")
    args = parser.parse_args()
    folder = RESULTS / args.name
    if not args.rehash:
        for name, source in pairs(args.fpga):
            retain_fpga(source, folder / "fpga" / name)
        for name, run in pairs(args.asic):
            retain_asic(run, folder / "asic" / name)
    rehash(folder)
    print(f"retained {folder.relative_to(ROOT)}: "
          f"{sum(1 for p in folder.rglob('*') if p.is_file())} files, SHA256SUMS written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
