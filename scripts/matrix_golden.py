#!/usr/bin/env python3
"""20.4's RTL change (328891a: the data cache's DCACHE parameter and the NPU's 32-bit port adapter) leaves R, the
default build, as it was: every R (soc_dev) firmware of matrix runs, replayed on the SoC simulation built from
GOLDEN (20.3's final RTL), must print the same console byte for byte (every record, every counter, cycle for
cycle) and end with the same SOC line.

The golden tree is exported with git archive and its soc_dev simulation built there, as core-shell-equiv does.

  matrix_golden.py RUN_DIR... [--golden-rev e6e2b98] [--jobs N] [--out build/matrix/golden]
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_CYCLES = 2_000_000_000


def build_golden(rev: str) -> Path:
    tree = ROOT / "build/golden" / rev
    sim = tree / "build/aster_soc/soc_dev"
    if not sim.exists():
        tree.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(["git", "archive", rev], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
        subprocess.run(["make", "-s", "-C", str(tree), str(sim)], check=True, capture_output=True)
    return sim


def tohost(elf: Path, prefix: str) -> int:
    out = subprocess.run([prefix + "nm", str(elf)], capture_output=True, text=True, check=True).stdout
    return next(int(l.split()[0], 16) for l in out.splitlines() if l.split()[-1] == "tohost")


def replay(sim: Path, run: Path, entry: dict, out: Path, prefix: str) -> str | None:
    """None if the golden simulation's console and SOC line are the run's; else what differs."""
    binary = run / entry["firmware_bin"]
    name = entry["id"].replace("/", "__")
    console = out / (name + ".console")
    result = subprocess.run([str(sim), f"+bin={binary}", f"+tohost={tohost(binary.with_suffix('.elf'), prefix):x}",
                             f"+console={console}", f"+max_cycles={MAX_CYCLES}"], capture_output=True, text=True)
    soc = next((l for l in result.stdout.splitlines() if l.startswith("SOC ")), "")
    want = (run / "console" / (name + ".console")).read_bytes()
    got = console.read_bytes() if console.exists() else b""
    if got != want:
        return f"{entry['id']}: the console differs ({len(got)} bytes against {len(want)})"
    status = soc.split()[1] if len(soc.split()) > 1 else "(none)"
    if status != entry["soc"]["status"] or f"cycles={entry['soc']['cycles']}" not in soc:
        return f"{entry['id']}: the SOC line differs: {soc!r}, the run's {entry['soc']}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--golden-rev", default="e6e2b98")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    parser.add_argument("--out", type=Path, default=ROOT / "build/matrix/golden")
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    sim = build_golden(args.golden_rev)
    args.out.mkdir(parents=True, exist_ok=True)
    work = []
    for run in args.runs:
        manifest = json.loads((run / "manifest.json").read_text())
        work += [(run, e) for e in manifest["entries"] if e["status"] == "captured" and e["sim"] == "soc_dev"]
    with ThreadPoolExecutor(args.jobs) as pool:
        differ = [d for d in pool.map(lambda w: replay(sim, w[0], w[1], args.out, prefix), work) if d]
    print(f"{'PASS' if not differ else 'FAIL'}: {len(work) - len(differ)} of {len(work)} R firmware images give "
          f"the same console and end on the golden SoC ({args.golden_rev}) as on the current one")
    for d in differ[:20]:
        print(f"  {d}")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
