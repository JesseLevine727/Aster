#!/usr/bin/env python3
"""The Phase 20 SoC's two-hart programs (milestone 20.2; docs/soc.md §10.3).

Builds each program with the two-hart runtime (start_multicore_aster.S) and
runs it on the device builds of the SoC's simulation (soc_dev, and soc_dev_w3
with three added answer cycles), where tb_soc.cpp checks every load against
the memory checker, every sc against the reservation rule and every snoop.
Each run must end with tohost = 1 and the testbench's PASS:

- litmus_v2.c, 26 two-hart shapes x LITMUS_TRIALS trials, its counts read
  back from main memory and classified by scripts/litmus.py (nothing RVWMO
  forbids; LRSC_PEER's sc never fails without a write to its word);
- soc_reset.c, hart 1 reset at random points, with several seeds; the
  testbench's coverage must show resets that caught answers owed, an AMO
  before its write, a refill and a reservation;
- smp_runtime.c, the runtime's dispatch and join (its round trip printed);
- soc_devices.c, the devices (hart control, timer, interrupts, counters,
  word-only pages, the NPU and its interrupt).

Each hart's RVFI trace (the reset stress's first seed, the dispatch and join,
the devices, and litmus at 200 trials) is checked for internal consistency as
lockstep.py checks a trace (soc.md §10.2: order numbers consecutive, masks
well formed, x0 written with nothing, only AMOs both read and write, traps
complete), hart 1's in a segment for each time it was released.

    soc_tests.py --sim-dir build/aster_soc --build-dir DIR --cflags "..." [--only NAME] [--trials N]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lockstep  # noqa: E402

RUNTIME = ["software/runtime/start_multicore_aster.S", "software/runtime/aster_trap.S",
           "verification/core/firmware/exit.c"]
LINK = "verification/core/firmware/link_multicore.ld"
SIMS = ("soc_dev", "soc_dev_w3")


def build(name: str, sources: list[str], defines: list[str], out: Path, cflags: str, prefix: str) -> tuple[Path, dict]:
    out.mkdir(parents=True, exist_ok=True)
    elf = out / f"{name}.elf"
    command = [f"{prefix}gcc", *cflags.split(), *defines, "-Isoftware/runtime", "-Isoftware/drivers", f"-T{LINK}",
               "-Wl,--no-warn-rwx-segments", "-o", str(elf), *RUNTIME, *sources]
    subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run([f"{prefix}objcopy", "-O", "binary", str(elf), str(elf.with_suffix(".bin"))], check=True)
    symbols = {}
    for line in subprocess.run([f"{prefix}nm", str(elf)], capture_output=True, text=True, check=True).stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    return elf, symbols


def run(sim: Path, elf: Path, symbols: dict, extra: list[str]) -> tuple[dict, str]:
    console = elf.with_suffix(f".{sim.name}.console")
    command = [str(sim), f"+bin={elf.with_suffix('.bin')}", f"+tohost={symbols['tohost']:x}", f"+console={console}",
               "+max_cycles=400000000", *extra]
    result = subprocess.run(command, capture_output=True, text=True, timeout=7200)
    line = next((l for l in result.stdout.splitlines() if l.startswith("SOC ")), "")
    fields = {"status": line.split()[1] if len(line.split()) > 1 else f"(no status) {result.stderr[-300:]}"}
    for token in line.split()[2:]:
        key, _, value = token.partition("=")
        fields[key] = int(value) if value.isdigit() else value
    if result.stderr.strip():
        fields["stderr"] = result.stderr.strip()[-300:]
    return fields, console.read_text(errors="replace") if console.exists() else ""


def trace_records(trace: Path, resets_allowed: bool) -> int:
    """lockstep.py's consistency checks on one hart's trace, in a segment for each release: a new segment
    starts only where the order numbers start again at 0 (the core's reset), so a record dropped or
    repeated anywhere else still fails; hart 0's trace must be one segment. Returns the records checked
    (raises ValueError on an inconsistency)."""
    total, segment = 0, []
    for line in trace.read_text().splitlines():
        if not line.strip():
            continue
        if int(line.split()[0]) == 0 and segment:
            if not resets_allowed:
                raise ValueError(f"{trace.name}: hart 0's order numbers start again")
            total += len(lockstep.parse_trace(segment))
            segment = []
        segment.append(line)
    if segment and int(segment[0].split()[0]) != 0:
        raise ValueError(f"{trace.name}: the trace does not start at order 0")
    return total + (len(lockstep.parse_trace(segment)) if segment else 0)


def traced(sim: Path, elf: Path, symbols: dict, extra: list[str]) -> tuple[dict, str, str]:
    """A run with both harts' traces, checked; the third value describes the check (or what failed)."""
    traces = [elf.with_suffix(f".{sim.name}.trace{h}") for h in range(2)]
    fields, console = run(sim, elf, symbols, [*extra, f"+trace={traces[0]}", f"+trace1={traces[1]}"])
    try:
        counts = [trace_records(t, h == 1) if t.exists() else 0 for h, t in enumerate(traces)]
        note = f"RVFI consistent: {counts[0]} + {counts[1]} records"
    except ValueError as error:
        fields["status"] = "RVFI_INCONSISTENT"
        note = str(error)
    for t in traces:
        t.unlink(missing_ok=True)
    return fields, console, note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--cflags", required=True)
    parser.add_argument("--only")
    parser.add_argument("--trials", type=int, default=2000, help="litmus trials a shape")
    parser.add_argument("--reset-seeds", type=int, default=4)
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    failures = []

    def report(ok: bool, text: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}: {text}", flush=True)
        if not ok:
            failures.append(text)

    def wanted(name: str) -> bool:
        return not args.only or args.only == name

    for sim_name in SIMS:
        sim = args.sim_dir / sim_name
        out = args.build_dir / sim_name
        where = f"on {sim_name}"
        if wanted("litmus"):
            elf, symbols = build("litmus", ["software/tests/litmus_v2.c"], [f"-DLITMUS_TRIALS={args.trials}"], out,
                                 args.cflags, prefix)
            sig = elf.with_suffix(f".{sim_name}.sig")
            fields, _ = run(sim, elf, symbols, [f"+signature={sig}", f"+sig_begin={symbols['begin_signature']:x}",
                                                f"+sig_end={symbols['end_signature']:x}"])
            classified = subprocess.run([sys.executable, str(ROOT / "scripts/litmus.py"), "--signature", str(sig),
                                         "--trials", str(args.trials), "--platform", "rtl"], capture_output=True, text=True)
            (out / f"litmus.{sim_name}.log").write_text(classified.stdout + classified.stderr)
            ok = fields["status"] == "PASS" and classified.returncode == 0
            elf200, symbols200 = build("litmus200", ["software/tests/litmus_v2.c"], ["-DLITMUS_TRIALS=200"], out,
                                       args.cflags, prefix)
            fields200, _, note200 = traced(sim, elf200, symbols200, [])
            ok = ok and fields200["status"] == "PASS"
            report(ok, f"litmus {where}: 26 shapes x {args.trials} trials, nothing forbidden ({out}/litmus.{sim_name}.log); "
                       f"loads checked {fields.get('loads')}, sc {fields.get('sc')} ({fields.get('sc_failed')} failed), "
                       f"AMOs {fields.get('amos')}, snoops {fields.get('snoops')}; at 200 trials {note200}"
                       + ("" if ok else f"; {fields} {fields200}"))
        if wanted("reset"):
            totals = {k: 0 for k in ("resets", "resets_owed", "resets_amo", "resets_refill", "resets_resv",
                                     "releases_early", "loads", "sc", "amos")}
            ok = True
            for seed in range(1, args.reset_seeds + 1):
                elf, symbols = build(f"soc_reset_s{seed}", ["software/tests/soc_reset.c"],
                                     ["-DRESET_ROUNDS=1000", f"-DRESET_SEED={0x2020c0de + seed}u"], out, args.cflags, prefix)
                if seed == 1:
                    fields, console, reset_note = traced(sim, elf, symbols, [])
                else:
                    fields, console = run(sim, elf, symbols, [])
                ok = ok and fields["status"] == "PASS" and " PASS" in console
                if fields["status"] != "PASS" or " PASS" not in console:
                    print(f"  seed {seed}: {fields} {console.strip()}")
                for key in totals:
                    totals[key] += int(fields.get(key, 0))
            covered = all(totals[k] > 0 for k in ("resets_owed", "resets_amo", "resets_refill", "resets_resv"))
            report(ok and covered, f"hart 1's reset stress {where}: {args.reset_seeds} seeds x 1,000 rounds, "
                                   f"{totals['resets']} resets (caught: answers owed {totals['resets_owed']}, an AMO "
                                   f"before its write {totals['resets_amo']}, a refill {totals['resets_refill']}, a "
                                   f"reservation {totals['resets_resv']}; released with answers due "
                                   f"{totals['releases_early']}), {totals['loads']} loads checked, {totals['sc']} sc, "
                                   f"{totals['amos']} AMOs; seed 1 {reset_note}" + ("" if covered else "; a coverage bin is empty"))
        if wanted("smp"):
            elf, symbols = build("smp_runtime", ["software/runtime/aster_smp.c", "software/tests/smp_runtime.c"], [], out,
                                 args.cflags, prefix)
            fields, console, note = traced(sim, elf, symbols, [])
            ok = fields["status"] == "PASS" and " PASS" in console
            match = re.search(r"fewest=(\d+) most=(\d+) mean=(\d+)", console)
            report(ok, f"the runtime's dispatch and join {where}: a round trip "
                       + (f"{match.group(1)}-{match.group(2)} cycles, mean {match.group(3)}" if match else "(no timing)")
                       + f"; {note}" + ("" if ok else f"; {fields} {console.strip()}"))
        if wanted("devices"):
            elf, symbols = build("soc_devices", ["software/tests/soc_devices.c"], [], out, args.cflags, prefix)
            fields, console, note = traced(sim, elf, symbols, [])
            ok = fields["status"] == "PASS" and "SOC DEVICES PASS" in console
            report(ok, f"the devices {where}: hart control, timer, interrupts, counters, word-only pages, the NPU"
                       f" ({fields.get('npu_jobs')} job checked); {note}" + ("" if ok else f"; {fields} {console.strip()}"))
    print(f"{'PASS' if not failures else 'FAIL'}: the Phase 20 SoC's two-hart programs"
          + (f" ({len(failures)} failed)" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
