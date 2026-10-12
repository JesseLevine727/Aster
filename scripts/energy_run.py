#!/usr/bin/env python3
"""Phase 21 (docs/phase21.md §3.1-3.3): one R program of the final capture run on the routed final image's netlist
in xsim (verification/energy/tb_energy.sv, energy_run.tcl), with a SAIF file for each counting interval, and checked
against its references (§3.2):
  - its firmware is the capture's (the manifest entry's firmware_sha256);
  - 20.5's board reference: tohost's cycle, hart 0's instructions retired at tohost, and the register page's
    window (its cycles and instructions);
  - the capture's console, byte for byte (so every record and counter in it).
A run that differs is reported FAIL and its activity is not used.

  energy_run.py ID --out DIR [--no-saif] [--snapshot build/energy/xsim/energy_snap]
writes DIR/run.json (the verdict, the intervals, the wall time) and, with SAIF, DIR/saif/interval*.saif.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import matrix_board  # noqa: E402

XSIM_DIR = ROOT / "build/energy/xsim"
VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", "/home/elfo/Documents/2025.1/Vivado/bin"))
REFERENCE = ROOT / "docs/results/phase20/board-20.5/reference.json.xz"
CAPTURES = {"cpu": "f5-cpu", "coherence": "f5-coherence", "dsp": "f5-dsp", "ml": "f5-ml", "ecg": "f5-ecg",
            "memory": "f5-memory", "npu_gemm": "f5-npu_gemm", "dma": "f5-dma"}
REFERENCED = ("tohost_cycles", "tohost_retired", "window_cycles", "window_retired")


def entry_of(ident: str, capture: Path | None = None) -> tuple[dict, Path]:
    run = capture or ROOT / "build/matrix" / CAPTURES[ident.split("/")[0]]
    for e in json.loads((run / "manifest.json").read_text())["entries"]:
        if e["id"] == ident:
            return e, run
    raise SystemExit(f"{ident}: not in {run}")


def reference_of(ident: str, reference: Path) -> dict:
    data = json.loads(lzma.open(reference).read() if reference.suffix == ".xz" else reference.read_text())
    row = next((r for r in data["rows"] if r["id"] == ident), None)
    if row is None:
        raise SystemExit(f"{ident}: not in the reference {reference}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-saif", action="store_true", help="the run alone: its speed and its reproduction")
    parser.add_argument("--snapshot", default="energy_snap")
    parser.add_argument("--capture", type=Path, help="the matrix run holding the entry (default: its f5 family)")
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()
    entry, run = entry_of(args.id, args.capture)
    binary = run / entry["firmware_bin"]
    data = binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["firmware_sha256"]:
        raise SystemExit(f"{args.id}: the firmware is not the capture's")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data += b"\0" * (-len(data) % 4)
    (out / "image.hex").write_text("".join(f"{int.from_bytes(data[i:i + 4], 'little'):08x}\n"
                                          for i in range(0, len(data), 4)))
    tohost = matrix_board.tohost(binary.with_suffix(".elf"))
    env = dict(os.environ)
    if args.no_saif:
        env.pop("ENERGY_SAIF_DIR", None)
    else:
        (out / "saif").mkdir(exist_ok=True)
        env["ENERGY_SAIF_DIR"] = str(out / "saif")
    command = [str(VIVADO_BIN / "xsim"), args.snapshot, "-tclbatch", str(ROOT / "verification/energy/energy_run.tcl"),
               "-testplusarg", f"image={out / 'image.hex'}", "-testplusarg", f"tohost={tohost:x}",
               "-testplusarg", f"out={out / 'run'}", "-log", str(out / "xsim.log")]
    started = time.time()
    result = subprocess.run(command, cwd=XSIM_DIR, env=env, capture_output=True, text=True)
    wall = time.time() - started
    log = result.stdout
    (out / "xsim.out").write_text(log + result.stderr)
    intervals = [dict(n=int(m[0]), start_ps=int(m[1]), end_ps=int(m[2]))
                 for m in re.findall(r"ENERGY_INTERVAL (\d+) from=(\d+) ps to=(\d+) ps", log)]
    board_line = (out / "run.result").read_text().strip() if (out / "run.result").exists() else ""
    got = {"status": board_line.split()[1] if len(board_line.split()) > 1 else "(none)"}
    for token in board_line.split()[2:]:
        key, _, value = token.partition("=")
        got[key] = int(value, 16) if key == "tohost" else int(value)
    want = reference_of(args.id, args.reference)
    problems = []
    if got["status"] != want["status"]:
        problems.append(f"status {got['status']}, the reference {want['status']}")
    problems += [f"{k} {got.get(k)}, the reference {want.get(k)}" for k in REFERENCED if got.get(k) != want.get(k)]
    console = (out / "run.console").read_bytes().decode("latin-1") if (out / "run.console").exists() else ""
    expected = (run / "console" / (args.id.replace("/", "__") + ".console")).read_text(errors="replace")
    if console != expected:
        problems.append(f"the console differs ({len(console)} bytes against {len(expected)})")
    cycles = got.get("cycles", 0)
    summary = dict(id=args.id, ok=not problems and result.returncode == 0, problems=problems, board=got,
                   intervals=intervals, saif=not args.no_saif, wall_seconds=round(wall, 1),
                   cycles_per_second=round(cycles / wall, 1) if wall else None,
                   firmware_sha256=entry["firmware_sha256"], tohost=tohost,
                   snapshot=args.snapshot, xsim_returncode=result.returncode)
    (out / "run.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"{'PASS' if summary['ok'] else 'FAIL'} {args.id}: {len(intervals)} intervals, {cycles} cycles in "
          f"{wall:.0f} s ({summary['cycles_per_second']} cycles/s)" + "".join(f"\n  {p}" for p in problems))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
