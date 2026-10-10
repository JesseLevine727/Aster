#!/usr/bin/env python3
"""20.5's board run (docs/tuning.md §7): the matrix's R programs on the PYNQ-Z1, each against its simulation.

The programs are the captured R (soc_dev) entries of matrix runs: each firmware image, as the matrix built it, runs
on the board's Phase 20 SoC through scripts/aster_board_remote.py, and must end as its simulation did (soc.md
§10.6):
  - the same status (tohost 1: PASS);
  - tohost at the same cycle: the board's latched tohost cycles (0x3F038) against the simulation's SOC line
    (its cycles are the same counter);
  - the same console, byte for byte (so every record and every counter in it is the simulation's).
The board's report also gives each program's largest console lag (tuning.md §7.1: at most 2 KiB, half the
4 KiB ring, or the console must grow), its load and run times, and hart 1's live retired count (read only).

  matrix_board.py RUN_DIR... --bitstream BIT --output DIR [--smoke | --all | --only ID...] [--host xilinx@10.0.0.82]

The board's sudo password comes from the environment variable ASTER_BOARD_SUDO, to sudo on stdin; it is never
stored, logged or put on a command line.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAGIC, MAIN_BYTES = 0x41535432, 0x18000            # "AST2", 96 KiB (aster_soc.sv)
CONSOLE_BYTES = 16384                              # the console's ring (aster_soc.sv, 16 KiB since 20.5)
LAG_LIMIT = CONSOLE_BYTES // 2                     # tuning.md §7.1: the reader at most half the ring behind
MAX_CYCLES = 2_000_000_000


def r_programs(runs: list[Path]) -> list[dict]:
    """Every captured R entry: its image, tohost, and the simulation's console and end."""
    out = []
    for run in runs:
        for e in json.loads((run / "manifest.json").read_text())["entries"]:
            if e["status"] != "captured" or e["sim"] != "soc_dev":
                continue
            binary = run / e["firmware_bin"]
            console = run / "console" / (e["id"].replace("/", "__") + ".console")
            out.append(dict(id=e["id"], family=e["family"], case=e["case"], binary=binary, elf=binary.with_suffix(".elf"),
                            console=console, sim=e["soc"], firmware_sha256=e["firmware_sha256"]))
    return out


def smoke(programs: list[dict]) -> list[dict]:
    """tuning.md §7.2's smoke set: the largest consoles, the fastest printers (console bytes over cycles), the gate
    workloads, and the first R entry of each family."""
    size = {p["id"]: p["console"].stat().st_size for p in programs}
    rate = {p["id"]: size[p["id"]] / max(1, p["sim"]["cycles"]) for p in programs}
    chosen = sorted(programs, key=lambda p: -size[p["id"]])[:10] + sorted(programs, key=lambda p: -rate[p["id"]])[:10]
    gates = ("coherence/reduce_fill/multicore/soc_dev/warm", "coherence/gemm_dot8_128x64x128/multicore/soc_dev/warm",
             "dsp/conv2d_direct_npu/npu_direct/soc_dev/cold", "ml/mnist_mlp_npu/npu/soc_dev/cold",
             "ml/cifar_cnn_npu_direct/npu_direct/soc_dev/cold", "ecg/ecg_pipeline_overlap/pipeline/soc_dev/cold",
             "cpu/coremark/scalar/soc_dev/cold", "npu_gemm/gemm_128x64x128/npu/soc_dev/warm")
    chosen += [p for p in programs if p["id"] in gates]
    for family in sorted({p["family"] for p in programs}):
        chosen.append(min((p for p in programs if p["family"] == family), key=lambda p: p["id"]))
    seen, out = set(), []
    for p in chosen:
        if p["id"] not in seen:
            seen.add(p["id"]); out.append(p)
    return out


def tohost(elf: Path) -> int:
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    text = subprocess.run([prefix + "nm", str(elf)], capture_output=True, text=True, check=True).stdout
    return next(int(l.split()[0], 16) for l in text.splitlines() if l.split()[-1] == "tohost")


def compare(program: dict, got: dict) -> list[str]:
    problems = []
    if got.get("status") != program["sim"]["status"]:
        problems.append(f"status {got.get('status')}, simulated {program['sim']['status']}")
    if got.get("tohost_cycles") != program["sim"]["cycles"]:
        problems.append(f"tohost at cycle {got.get('tohost_cycles')}, simulated {program['sim']['cycles']}")
    want = program["console"].read_text(errors="replace")
    if got.get("console") != want:
        problems.append(f"the console differs ({len(got.get('console', ''))} bytes against {len(want)})")
    if got.get("max_lag", 0) > LAG_LIMIT:
        problems.append(f"the console lagged {got['max_lag']} bytes, over {LAG_LIMIT}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="xilinx@10.0.0.82")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--smoke", action="store_true")
    group.add_argument("--all", action="store_true")
    group.add_argument("--only", nargs="+")
    parser.add_argument("--timeout", type=int, default=6 * 3600, help="seconds for the board's run")
    args = parser.parse_args()
    password = os.environ.get("ASTER_BOARD_SUDO")
    if not password:
        raise SystemExit("set ASTER_BOARD_SUDO to the board's sudo password (it is never stored)")
    programs = r_programs(args.runs)
    if args.smoke:
        programs = smoke(programs)
    elif args.only:
        programs = [p for p in programs if p["id"] in set(args.only)]
    if not programs:
        raise SystemExit("no R program selected")
    configs = {(p["sim"]["npu_config"], p["sim"]["soc_config"]) for p in programs}
    if len(configs) != 1:
        raise SystemExit(f"the programs' simulations have several configurations: {configs}")
    npu_config, soc_config = configs.pop()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = Path(tempfile.mkdtemp(prefix="matrix_board_"))
    manifest = []
    for n, p in enumerate(programs):
        if hashlib.sha256(p["binary"].read_bytes()).hexdigest() != p["firmware_sha256"]:
            raise SystemExit(f"{p['id']}: the firmware is not the run's")
        image = f"{n:05d}.bin"
        shutil.copyfile(p["binary"], stage / image)
        manifest.append(dict(kind="program", name=p["id"], image=image, tohost=tohost(p["elf"]), max_cycles=MAX_CYCLES))
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=1))
    design = dict(magic=MAGIC, main_bytes=MAIN_BYTES, npu_config=npu_config, soc_config=soc_config, page_bytes=0,
                  console_bytes=CONSOLE_BYTES)
    (stage / "design.json").write_text(json.dumps(design))
    shutil.copyfile(args.bitstream, stage / "aster_core.bit")
    shutil.copyfile(ROOT / "scripts/aster_board_remote.py", stage / "aster_board_remote.py")
    remote = "/home/xilinx/aster_matrix_board"
    subprocess.run(["ssh", args.host, f"rm -rf {remote} && mkdir -p {remote}"], check=True)
    subprocess.run(["scp", "-q", "-r", *(str(path) for path in stage.iterdir()), f"{args.host}:{remote}/"], check=True)
    # (the password to sudo on stdin only: never in argv, a file or a log; no tty, so nothing echoes it)
    run = subprocess.run(["ssh", args.host, f"cd {remote} && sudo -S -p '' python3 aster_board_remote.py"],
                         input=password + "\n", capture_output=True, text=True, timeout=args.timeout)
    (output / "remote.log").write_text(run.stdout + run.stderr)
    subprocess.run(["scp", "-q", f"{args.host}:{remote}/report.json", str(output / "report.json")], check=True)
    shutil.rmtree(stage, ignore_errors=True)
    report = json.loads((output / "report.json").read_text())
    problems = []
    if report.get("status") != "complete":
        problems.append(f"the board run is {report.get('status')}: {report.get('error')}")
    if run.returncode:
        problems.append(f"the board script exited {run.returncode}")
    if report.get("bitstream_sha256") != hashlib.sha256(args.bitstream.read_bytes()).hexdigest():
        problems.append("the board ran a different bitstream")
    clock = report.get("clock", {})
    if clock.get("mhz") != 100.0 or not 99.0 <= clock.get("measured_mhz", 0) <= 101.0:
        problems.append(f"the clock is not 100 MHz: {clock}")
    got = {r["name"]: r for r in report.get("programs", [])}
    rows = []
    for p in programs:
        r = got.get(p["id"])
        issues = compare(p, r) if r else ["not run"]
        rows.append(dict(id=p["id"], ok=not issues, issues=issues, max_lag=r and r.get("max_lag"),
                         load_seconds=r and r.get("load_seconds"), seconds=r and r.get("seconds"),
                         console_bytes=r and r.get("console_bytes"), tohost_cycles=r and r.get("tohost_cycles")))
    bad = [r for r in rows if not r["ok"]]
    lags = [r["max_lag"] for r in rows if r["max_lag"] is not None]
    summary = dict(programs=len(rows), matched=len(rows) - len(bad), max_lag=max(lags, default=None),
                   load_seconds=round(sum(r["load_seconds"] or 0 for r in rows), 1),
                   run_seconds=round(sum(r["seconds"] or 0 for r in rows), 1), clock=clock,
                   identity=report.get("identity"), bitstream_sha256=report.get("bitstream_sha256"),
                   problems=problems, rows=rows)
    (output / "comparison.json").write_text(json.dumps(summary, indent=1) + "\n")
    ok = not problems and not bad
    print(f"{'PASS' if ok else 'FAIL'}: {summary['matched']} of {summary['programs']} R programs end on the board as "
          f"simulated; console lag at most {summary['max_lag']} bytes (limit {LAG_LIMIT}); loading "
          f"{summary['load_seconds']} s, running {summary['run_seconds']} s; FCLK0 {clock.get('measured_mhz')} MHz")
    for p in problems:
        print(f"  {p}")
    for r in bad[:20]:
        print(f"  {r['id']}: {'; '.join(r['issues'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
