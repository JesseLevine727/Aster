#!/usr/bin/env python3
"""Two builds of the CPU shell, compared program by program (milestone 20.2).

20.2 restructures the Aster core's and caches' logic for timing, each change
claimed to keep every cycle. This runs a golden shell (built from a commit
before the change; make core-shell-equiv) and the current one on the same
programs with the same plusargs, in the modes the core's own suites use, and
requires each run to end the same way: the same status line (its cycles and
instructions retired), the same RVFI trace, record for record, and the same
console and signature where the program has them. The golden run must pass.

The programs: the CPU kernels, every suite the cached core runs (the
interrupt and coherence programs with their devices, as the test runner gives
them; only the cached core's own skips are left out, not the board's), and
constrained-random programs. The modes (each as scripts/run_core_tests.py
builds the shell's command line): plain; one cycle of memory latency; random
memory back-pressure (stall seeds), with latency 1, with three accesses in
flight and with long stalls; random interrupts spliced into the programs; and the random programs
plain, under back-pressure and under interrupts.

    compare_shells.py --golden GOLDEN_SHELL [--shell SHELL] --build-dir DIR [--only NAME] [--random N]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_core_tests as rct  # noqa: E402
import rvgen  # noqa: E402

CONFIG = rct.DUTS["aster_l1"]
# (name, stall seed, spliced interrupts seed, extra plusargs)
MODES = [("plain", None, None, []), ("latency1", None, None, ["+latency=1"]), ("stall5", 5, None, []),
         ("latency1-stall9", 9, None, ["+latency=1"]), ("inflight3-stall7", 7, None, ["+max_inflight=3"]),
         ("longstall3", 3, None, ["+long_stall"]), ("irq7", None, 7, [])]
# (name, first seed, stall seed, interrupts seed)
RANDOM_MODES = [("random", 1, None, None), ("random-stall11", 101, 11, None), ("random-irq7", 301, None, 7),
                ("random-irq7-stall13", 401, 13, 7)]


def mode_args(build_dir: Path, stall, interrupts, shell_arg: list[str]) -> SimpleNamespace:
    return SimpleNamespace(sim=None, spike=Path("/nonexistent/spike"), build_dir=build_dir, max_cycles=rct.MAX_CYCLES,
                           stall_seed=stall, interrupts=interrupts, shell_arg=list(shell_arg),
                           aster_clock=rct.ASTER_CLOCK, aster_dot8=rct.ASTER_DOT8, random_length=1500)


def prepare(args, kind: str, source, prefix: str):
    """Build one program as the runner does in this mode: (elf, binary, symbols, run options)."""
    if kind == "kernel":
        elf, binary, symbols = rct.build_kernel(source, args.build_dir, prefix)
        return elf, binary, symbols, {"kernel": True, "max_cycles": rct.KERNEL_MAX_CYCLES}
    shell_only = source.parent in (rct.INTERRUPTS, rct.SELFCHECK, rct.COHERENCE)
    spliced = args.interrupts is not None and not shell_only
    elf, binary, symbols = rct.build(source, CONFIG["march"], args.build_dir, prefix,
                                     defines=("ASTER_INTERRUPTS",) if spliced else ())
    extra = (("+irq_device",) if source.parent == rct.INTERRUPTS
             else (f"+{source.stem}={(args.stall_seed or 0) + 1}", f"+remote_area={symbols['remote_area']:x}",
                   *(("+lazy_snoops",) if args.stall_seed is not None else ()))
             if source.parent == rct.COHERENCE
             else (f"+irq_random={args.interrupts}",) if spliced else ())
    max_cycles = (rct.SELFCHECK_MAX_CYCLES if source.parent == rct.SELFCHECK
                  else rct.C_MAX_CYCLES if source.parent == rct.C_TESTS else None)
    return elf, binary, symbols, {"shell_extra": extra, "max_cycles": max_cycles}


def run_both(args, golden: Path, current: Path, elf: Path, binary: Path, symbols: dict, options: dict):
    outputs = []
    for shell in (golden, current):
        args.sim = shell
        passed, status, trace, _ = rct.execute(args, CONFIG, elf, binary, symbols, shell_only=True, **options)
        extras = {}
        for suffix in (".console", ".sig.dut"):
            path = elf.with_suffix(suffix)
            extras[suffix] = path.read_bytes() if path.exists() else None
            path.unlink(missing_ok=True)
        outputs.append((passed, status, trace, extras))
    return outputs


def compare_one(label: str, golden: Path, current: Path, args, kind: str, source, prefix: str) -> tuple[bool, str]:
    args = SimpleNamespace(**vars(args))         # each task its own copy (args.sim is set per shell)
    elf, binary, symbols, options = prepare(args, kind, source, prefix)
    (g_passed, g_status, g_trace, g_extras), (c_passed, c_status, c_trace, c_extras) = run_both(
        args, golden, current, elf, binary, symbols, options)
    if not g_passed or "cycles=" not in g_status or not g_trace:
        return False, f"{label}: the golden shell did not pass: {g_status[:200]}"
    problems = [] if c_passed else ["the current shell did not pass (or did not exit 0)"]
    if g_status != c_status:
        problems.append(f"status {g_status!r} vs {c_status!r}")
    if g_trace != c_trace:
        gt, ct = g_trace.splitlines(), c_trace.splitlines()
        first = next((i for i, (a, b) in enumerate(zip(gt, ct)) if a != b), min(len(gt), len(ct)))
        problems.append(f"RVFI record {first} of {len(gt)}/{len(ct)} differs")
    for suffix in g_extras:
        if g_extras[suffix] != c_extras[suffix]:
            problems.append(f"{suffix} differs")
    if problems:
        return False, f"{label}: " + "; ".join(problems)
    return True, f"{label}: {g_status.removeprefix('SHELL ')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--shell", type=Path, default=rct.ROOT / "build/aster_core/core_ports_aster_l1")
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--only")
    parser.add_argument("--random", type=int, default=40, help="random programs a random mode")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    golden, current = args.golden.resolve(), args.shell.resolve()

    sources = []
    for suite in CONFIG["suites"]:
        for path in sorted(rct.suite_dir(suite).glob("*.c" if suite == "c" else "*.S")):
            name = f"{suite}/{path.stem}"
            if name not in CONFIG["skip"] and (not args.only or name == args.only or name.endswith("/" + args.only)):
                sources.append((name, path))
    kernels = [k for k in rct.KERNELS if not args.only or k == args.only]
    tasks = []
    for mode, stall, irq, extra in MODES:
        out = args.build_dir / mode
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        margs = mode_args(out, stall, irq, extra)
        # (the spliced interrupts only in the suites the runner splices them into: the others own the trap vector)
        if irq is None:
            tasks += [(f"{mode} kernel {k}", margs, "kernel", k) for k in kernels]
        tasks += [(f"{mode} {name}", margs, "program", path) for name, path in sources
                  if irq is None or name.split("/")[0] in CONFIG["interrupt_suites"]]
    if not args.only:
        for mode, first, stall, irq in RANDOM_MODES:
            out = args.build_dir / mode
            shutil.rmtree(out, ignore_errors=True)
            (out / "random").mkdir(parents=True)
            margs = mode_args(out, stall, irq, [])
            # the extensions as the runner's random mode chooses them (run_core_tests.run_random)
            base = CONFIG["march"][4:].split("_")[0]
            extensions = ",".join(name for name in ("m", "a") if name in base)
            if "zicsr" in CONFIG["march"]:
                extensions += ",zicsr" if irq is None else ",irqcsr"
            if "zifencei" in CONFIG["march"]:
                extensions += ",zifencei"
            if CONFIG.get("dot8"):
                extensions += ",xasterdot8"
            for seed in range(first, first + args.random):
                source = out / "random" / f"rvgen_{seed}.S"
                source.write_text(rvgen.Generator(seed, extensions).program(margs.random_length))
                tasks.append((f"{mode} seed {seed}", margs, "program", source))

    failures = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = pool.map(lambda t: compare_one(t[0], golden, current, t[1], t[2], t[3], prefix), tasks)
        for ok, message in results:
            print(f"{'PASS' if ok else 'FAIL'}: {message}", flush=True)
            failures += not ok
    print(f"{'PASS' if not failures else 'FAIL'}: {len(tasks) - failures}/{len(tasks)} runs end the same in both "
          f"shells ({len(MODES)} modes x the kernels and every suite, {len(RANDOM_MODES)} random modes x "
          f"{args.random} programs), cycle for cycle and RVFI record for record")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
