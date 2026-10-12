#!/usr/bin/env python3
"""20.5's board run (docs/tuning.md §7): the matrix's R programs on the PYNQ-Z1, each against its simulation.

The programs are the captured R (soc_dev) entries of matrix runs: each firmware image, as the matrix built it, runs
on the board's Phase 20 SoC through scripts/aster_board_remote.py, and must end as its simulation did (soc.md
§10.6):
  - the same status (tohost 1: PASS);
  - tohost at the same cycle: the board's latched tohost cycles (0x3F038) against the simulation's SOC line
    (its cycles are the same counter);
  - the same console, byte for byte (so every record and every counter in it is the simulation's);
  - with --reference (scripts/matrix_board_reference.py: each program simulated as the board runs it), hart 0's
    instructions retired at tohost (0x3F050) and the window's cycles and retired count (0x3F020, 0x3F028) as
    simulated (tuning.md §7.2).
The board's report also gives each program's largest console lag (tuning.md §7.1: at most half the ring, 8 KiB
of the 16 KiB console), its load and run times, and hart 1's live retired count (read only).

  matrix_board.py RUN_DIR... --bitstream BIT --output DIR [--smoke | --all | --only ID...] [--host xilinx@10.0.0.82]
                  [--reference FILE] [--resume | --recompare]

--recompare compares the board's report already fetched into DIR again (with --reference, say), without the board.

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
import time
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


REFERENCED = ("tohost_retired", "window_cycles", "window_retired")


def compare(program: dict, got: dict, reference: dict | None = None) -> list[str]:
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
    if reference is not None:
        for key in REFERENCED:
            if got.get(key) is None or got.get(key) != reference.get(key):
                problems.append(f"{key} {got.get(key)}, simulated {reference.get(key)}")
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
    again = parser.add_mutually_exclusive_group()
    again.add_argument("--resume", action="store_true",
                       help="the board's run of these programs already started (by an earlier call): wait for it, "
                            "then fetch and compare its report")
    again.add_argument("--recompare", action="store_true",
                       help="compare the report already in --output again, without the board")
    parser.add_argument("--reference", type=Path, help="scripts/matrix_board_reference.py's output for these programs")
    args = parser.parse_args()
    password = os.environ.get("ASTER_BOARD_SUDO")
    if not password and not args.recompare:
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
    reference = None
    if args.reference:
        ref = json.loads(args.reference.read_text())
        if ref["problems"]:
            raise SystemExit(f"--reference: {len(ref['problems'])} programs not simulated as captured")
        reference = {r["id"]: r for r in ref["rows"]}
        missing = [p["id"] for p in programs if p["id"] not in reference]
        if missing:
            raise SystemExit(f"--reference: {len(missing)} programs not in it ({missing[:3]})")
    if args.recompare:
        return summarize(programs, output, args.bitstream, 0, reference)
    output.mkdir(parents=True, exist_ok=args.resume)
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
    if args.resume:                                      # (the board's run must be of these very programs)
        listed = subprocess.run(["ssh", args.host, f"cat {remote}/manifest.json"], capture_output=True, text=True,
                                check=True).stdout
        if [(m["name"], m["image"]) for m in json.loads(listed)] != [(m["name"], m["image"]) for m in manifest]:
            raise SystemExit("--resume: the board's run is not of these programs")
    else:
        subprocess.run(["ssh", args.host, f"rm -rf {remote} && mkdir -p {remote}"], check=True)
        subprocess.run(["scp", "-q", "-r", *(str(path) for path in stage.iterdir()), f"{args.host}:{remote}/"],
                       check=True)
    # (the password to sudo on stdin only: never in argv, a file or a log; no tty, so nothing echoes it.) The run is
    # detached on the board, in a session of its own with its output to the board's remote.log, so a dropped ssh
    # session cannot end it (20.5's full board run outlasts one); this script polls the report until the run is
    # complete or failed, or the timeout passes (the partial report is still fetched, below).
    if not args.resume:
        subprocess.run(["ssh", args.host, f"cd {remote} && sudo -S -p '' sh -c 'setsid nohup python3 "
                        f"aster_board_remote.py > remote.log 2>&1 < /dev/null &'"],
                       input=password + "\n", capture_output=True, text=True, check=True, timeout=120)
    deadline, status = time.time() + args.timeout, "running"
    probe = f"cd {remote} && python3 -c \"import json; print(json.load(open('report.json'))['status'])\""
    while status == "running" and time.time() < deadline:
        time.sleep(60)
        seen = subprocess.run(["ssh", args.host, probe], capture_output=True, text=True).stdout.strip()
        status = seen or status                          # (a report not yet written, or mid-write: polled again)
    returncode = 0 if status == "complete" else status
    subprocess.run(["scp", "-q", f"{args.host}:{remote}/remote.log", str(output / "remote.log")], check=False)
    subprocess.run(["scp", "-q", f"{args.host}:{remote}/report.json", str(output / "report.json")], check=True)
    shutil.rmtree(stage, ignore_errors=True)
    return summarize(programs, output, args.bitstream, returncode, reference)


def summarize(programs: list[dict], output: Path, bitstream: Path, returncode, reference: dict | None) -> int:
    """The board's report (output/report.json) against the programs' simulations: comparison.json."""
    report = json.loads((output / "report.json").read_text())
    problems = []
    if report.get("status") != "complete":
        problems.append(f"the board run is {report.get('status')}: {report.get('error')}")
    if returncode:
        problems.append(f"the board script exited {returncode}")
    if report.get("bitstream_sha256") != hashlib.sha256(bitstream.read_bytes()).hexdigest():
        problems.append("the board ran a different bitstream")
    clock = report.get("clock", {})
    if clock.get("mhz") != 100.0 or not 99.0 <= clock.get("measured_mhz", 0) <= 101.0:
        problems.append(f"the clock is not 100 MHz: {clock}")
    got = {r["name"]: r for r in report.get("programs", [])}
    rows = []
    for p in programs:
        r = got.get(p["id"])
        issues = compare(p, r, reference and reference[p["id"]]) if r else ["not run"]
        rows.append(dict(id=p["id"], ok=not issues, issues=issues, max_lag=r and r.get("max_lag"),
                         tohost_retired=r and r.get("tohost_retired"),
                         load_seconds=r and r.get("load_seconds"), seconds=r and r.get("seconds"),
                         console_bytes=r and r.get("console_bytes"), tohost_cycles=r and r.get("tohost_cycles")))
    bad = [r for r in rows if not r["ok"]]
    lags = [r["max_lag"] for r in rows if r["max_lag"] is not None]
    summary = dict(programs=len(rows), matched=len(rows) - len(bad), max_lag=max(lags, default=None),
                   load_seconds=round(sum(r["load_seconds"] or 0 for r in rows), 1),
                   run_seconds=round(sum(r["seconds"] or 0 for r in rows), 1), clock=clock,
                   identity=report.get("identity"), bitstream_sha256=report.get("bitstream_sha256"),
                   referenced=list(REFERENCED) if reference else [], problems=problems, rows=rows)
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
