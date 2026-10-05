#!/usr/bin/env python3
"""The Aster core on the PYNQ-Z1 (milestone 18.7's feasibility run).

Programs: the CPU kernels of docs/cpu.md §7 and the self-checking programs
that need only memory (the riscv-tests suites, the directed, trap, selfcheck
and C programs; not the interrupt or coherence programs, whose devices the
board design does not have). Each runs in the CPU shell (the cached core,
tb_core_ports.cpp with +cache_model, on its default two-cycle memory without
back-pressure — the board's memory) and on the board design
(rtl/soc/aster_core_pynq.sv): in its Verilator simulation (--sim) or on the
board itself (--board). On the same program the board design must:
- end as the shell does: a kernel with the same console, byte for byte (its
  record — with ",status=PASS," and its checksums — included) and the same
  window, cycles and retired instructions; the others with tohost = 1;
- take exactly the shell's cycles: the window's for a kernel, and to the
  tohost store, with the same instructions retired, for the others (the
  board's counters start at the edge that starts the core, as the shell's).

    aster_board.py --sim BOARD_SIM [--only NAME]
    aster_board.py --board BITSTREAM --host xilinx@10.0.0.82 --output DIR [--only NAME]
    aster_board.py --report DIR/report.json [--only NAME]   # a board run's report, compared again

--board copies the bitstream, the images and scripts/aster_board_remote.py to
the board, runs it there as root (the sudo password from the environment
variable ASTER_BOARD_SUDO, piped to sudo -S; never stored), and compares its
JSON report. The shell is build/aster_core/core_ports_aster_l1 (make
core-aster-sim); the images are built as scripts/run_core_tests.py builds them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_core_tests as rct  # noqa: E402

SHELL = ROOT / "build/aster_core/core_ports_aster_l1"
CONFIG = rct.DUTS["aster_l1"]
SUITES = ("rv32ui", "rv32um", "rv32ua", "rv32mi", "directed", "traps", "selfcheck", "c")
# Programs that depend on the shell's memory ending where the program does
# (it is sized to each program; the board's is a fixed 128 KiB, so their
# fetches past the end find memory instead of faulting).
BOARD_SKIP = {
    "directed/wrongpath_fetch_fault": "fetches past the shell's memory end, which is memory on the board",
    "traps/fetch_traps": "expects a fetch fault past the shell's memory end, which is memory on the board",
}
KERNEL_CYCLES = 60_000_000
PROGRAM_CYCLES = 20_000_000
# The board's cycle count to the tohost store against the shell's: both count
# from the edge that starts the core (the board's clear at the start), so
# none.
CYCLE_OFFSET = 0


def programs(only: str | None) -> list[tuple[str, str]]:
    """(kind, name): kernels first, then the self-checking programs (as the
    runner lists them, without the cached core's skips)."""
    found = [("kernel", name) for name in rct.KERNELS]
    for suite in SUITES:
        for path in sorted(rct.suite_dir(suite).glob("*.c" if suite == "c" else "*.S")):
            name = f"{suite}/{path.stem}"
            if name not in CONFIG["skip"] and name not in BOARD_SKIP:
                found.append(("program", name))
    if only:
        found = [(kind, name) for kind, name in found if name == only or name.endswith("/" + only)]
    return found


def build(kind: str, name: str, out: Path, prefix: str) -> tuple[Path, Path, dict]:
    if kind == "kernel":
        return rct.build_kernel(name, out, prefix)
    return rct.build(rct.test_path(name), CONFIG["march"], out, prefix)


def shell_run(kind: str, elf: Path, binary: Path, symbols: dict) -> dict:
    memory = symbols["shell_memory_end"] - rct.ENTRY if "shell_memory_end" in symbols else rct.MEMORY_BYTES[False]
    memory = -(-memory // 16) * 16
    command = [str(SHELL), f"+bin={binary}", f"+tohost={symbols['tohost']:x}",
               f"+max_cycles={KERNEL_CYCLES if kind == 'kernel' else PROGRAM_CYCLES}", f"+mem_bytes={memory:x}",
               *CONFIG["shell_args"]]
    if kind == "kernel":
        command += ["+io_page", f"+console={elf.with_suffix('.shell.console')}", "+kernel_end"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
    out = fields(result.stdout.strip(), "SHELL")
    if kind == "kernel":
        out["console"] = elf.with_suffix(".shell.console").read_text(errors="replace")
    return out


def fields(status: str, tag: str) -> dict:
    tokens = status.split()
    if not tokens or tokens[0] != tag:
        return {"status": f"(no status) {status[-200:]}"}
    out = {"status": tokens[1]}
    for token in tokens[2:]:
        if "=" in token:
            key, value = token.split("=", 1)
            out[key] = int(value) if value.isdigit() else value
    return out


def board_sim_run(sim: Path, kind: str, elf: Path, binary: Path, symbols: dict) -> dict:
    command = [str(sim), f"+bin={binary}", f"+max_cycles={KERNEL_CYCLES if kind == 'kernel' else PROGRAM_CYCLES}",
               f"+console={elf.with_suffix('.board.console')}"]
    command += ["+kernel"] if kind == "kernel" else [f"+tohost={symbols['tohost']:x}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
    out = fields(result.stdout.strip(), "BOARD")
    out["console"] = elf.with_suffix(".board.console").read_text(errors="replace")
    return out


def compare(kind: str, name: str, shell: dict, board: dict) -> tuple[bool, str]:
    if shell.get("status") != "PASS":
        return False, f"the shell's run: {shell}"
    if board.get("status") != "PASS":
        return False, f"the board's run: {board}"
    if kind == "kernel":
        same = (board.get("window_cycles"), board.get("window_retired")) == \
               (shell.get("window_cycles"), shell.get("window_retired"))
        console = board.get("console") == shell.get("console")
        return same and console, (f"window {board.get('window_cycles')} cycles, {board.get('window_retired')} "
                                  f"instructions"
                                  + ("" if same else f"; the shell's {shell.get('window_cycles')}, "
                                                     f"{shell.get('window_retired')}")
                                  + ("; the same console" if console else "; the console differs from the shell's"))
    offset = shell["cycles"] - board["tohost_cycles"]
    same = offset == CYCLE_OFFSET and board.get("tohost_retired") == shell.get("retired")
    return same, (f"tohost at cycle {board['tohost_cycles']}, {board.get('tohost_retired')} instructions"
                  + ("" if same else f"; the shell's cycle {shell['cycles']}, {shell.get('retired')} instructions"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", type=Path, help="the board design's Verilator simulation")
    parser.add_argument("--board", type=Path, help="the bitstream, to run on the board")
    parser.add_argument("--report", type=Path, help="a board run's report.json, to compare again")
    parser.add_argument("--host", default="xilinx@10.0.0.82")
    parser.add_argument("--output", type=Path, help="the board run's evidence directory")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/aster_board")
    parser.add_argument("--only")
    args = parser.parse_args()
    if sum(map(bool, (args.sim, args.board, args.report))) != 1:
        parser.error("give one of --sim, --board or --report")
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    rows = programs(args.only)
    if not rows:
        print(f"FAIL: no program matches {args.only}")
        return 1
    args.build_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for kind, name in rows:
        elf, binary, symbols = build(kind, name, args.build_dir, prefix)
        built.append((kind, name, elf, binary, symbols, shell_run(kind, elf, binary, symbols)))
    if args.sim:
        results = {name: board_sim_run(args.sim, kind, elf, binary, symbols)
                   for kind, name, elf, binary, symbols, _ in built}
        where = "the board design in simulation"
    elif args.report:
        report = json.loads(args.report.read_text())
        results = {entry["name"]: entry for entry in report["programs"]}
        where = f"the board (its report {args.report.name})"
    else:
        results = run_on_board(args, built)
        where = "the board"
    failures = []
    for kind, name, _, _, _, shell in built:
        ok, message = compare(kind, name, shell, results.get(name, {"status": "(not run)"}))
        print(f"{'PASS' if ok else 'FAIL'}: {kind} {name}: {message}")
        if not ok:
            failures.append(name)
    print(f"{'PASS' if not failures else 'FAIL'}: {len(built) - len(failures)}/{len(built)} programs on {where} "
          f"end as in the CPU shell, cycle for cycle")
    return 1 if failures else 0


def run_on_board(args, built) -> dict:
    """Copy the bitstream, the images and the remote script to the board, run
    it there as root, and return its per-program results."""
    if not args.output:
        raise SystemExit("--board needs --output")
    password = os.environ.get("ASTER_BOARD_SUDO")
    if not password:
        raise SystemExit("set ASTER_BOARD_SUDO to the board's sudo password (it is never stored)")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = Path(tempfile.mkdtemp(prefix="aster_board_"))
    manifest = []
    for kind, name, elf, binary, symbols, _ in built:
        image = stage / (name.replace("/", "__") + ".bin")
        shutil.copyfile(binary, image)
        manifest.append({"kind": kind, "name": name, "image": image.name,
                         "tohost": 0 if kind == "kernel" else symbols["tohost"],
                         "max_cycles": KERNEL_CYCLES if kind == "kernel" else PROGRAM_CYCLES})
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=1))
    shutil.copyfile(args.board, stage / "aster_core.bit")
    shutil.copyfile(ROOT / "scripts/aster_board_remote.py", stage / "aster_board_remote.py")
    remote = "/home/xilinx/aster_core_board"
    subprocess.run(["ssh", args.host, f"rm -rf {remote} && mkdir -p {remote}"], check=True)
    subprocess.run(["scp", "-q", "-r", *(str(path) for path in stage.iterdir()), f"{args.host}:{remote}/"], check=True)
    # The password goes to sudo on stdin only (never in argv, a file or a log;
    # no tty, so nothing echoes it).
    run = subprocess.run(["ssh", args.host, f"cd {remote} && sudo -S -p '' python3 aster_board_remote.py"],
                         input=password + "\n", capture_output=True, text=True, timeout=3600)
    (output / "remote.log").write_text(run.stdout + run.stderr)
    subprocess.run(["scp", "-q", f"{args.host}:{remote}/report.json", str(output / "report.json")], check=True)
    report = json.loads((output / "report.json").read_text())
    shutil.rmtree(stage, ignore_errors=True)
    problems = []
    if run.returncode:
        problems.append(f"the board script exited {run.returncode}")
    if report.get("status") != "complete":
        problems.append(f"the board run is {report.get('status')}: {report.get('error')}")
    if report.get("bitstream_sha256") != hashlib.sha256(args.board.read_bytes()).hexdigest():
        problems.append("the board ran a different bitstream")
    clock = report.get("clock", {})
    if clock.get("mhz") != 100.0 or not 99.0 <= clock.get("measured_mhz", 0) <= 101.0:
        problems.append(f"the clock is not 100 MHz: {clock}")
    if problems:
        raise SystemExit("FAIL: " + "; ".join(problems))
    print(f"board: FCLK0 {clock['mhz']} MHz, measured {clock['measured_mhz']:.3f} MHz; "
          f"bitstream {report['bitstream_sha256'][:12]}")
    return {entry["name"]: entry for entry in report["programs"]}


if __name__ == "__main__":
    raise SystemExit(main())
