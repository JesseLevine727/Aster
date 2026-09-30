#!/usr/bin/env python3
"""Run riscv-tests and riscv-arch-test on a Phase 18 CPU-shell DUT in lockstep with Spike.

For every test: build the ELF with the shell environment, run the Verilator
shell (RVFI trace) and Spike (commit log), and require (1) the shell to exit 0
reporting PASS through tohost, with a retired count equal to its trace's; (2)
Spike to exit 0; (3) scripts/lockstep.py to find the DUT stream identical to
Spike's up to and including the tohost store (which also proves the pass value
Spike stored); and (4) the program's signature region (begin_signature to
end_signature: the test data) to be identical in the shell's memory and
Spike's at the end, which catches a store whose bus write differs from its
RVFI record. Traces, logs and signatures are deleted before each run, so a
stale file can never stand in for a missing one. With `--arch` the programs
are riscv-arch-test (3.10.0).

`--inject` instead proves the harness catches a deliberately corrupted run: it
corrupts the raw DUT trace text (every field, dropped, duplicated and extra
records, truncation) and the raw Spike log, re-parses both, and requires every
corruption to be rejected by a parser or the comparator.

    run_core_tests.py --dut picorv32 --sim build/core_shell_picorv32/core_shell_picorv32 --spike spike
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lockstep  # noqa: E402

ENV = ROOT / "verification/core/env"
TESTS = ROOT / "vendor/riscv-tests/isa"
ARCH_ENV = ROOT / "verification/core/arch_env"
ARCH_TESTS = ROOT / "vendor/riscv-arch-test/riscv-test-suite"
ENTRY = 0x80000000
# Shell memory: 96 KiB for riscv-tests (the v2 SRAM), 2 MiB for arch-test
# programs (matching env/link.ld and arch_env/link.ld).
MEMORY_BYTES = {False: 0x18000, True: 0x200000}
# A test that runs away (for example a failure that never reports) ends here;
# the longest rv32ui/rv32um test takes a few thousand cycles.
MAX_CYCLES = 200_000

# What each DUT implements, and the tests it cannot run with a reason.
DUTS = {
    "picorv32": {
        "march": "rv32im", "spike_isa": "rv32im", "suites": ("rv32ui", "rv32um"),
        # PicoRV32 reports full-word RVFI read masks on sub-word loads.
        "word_loads": True,
        "skip": {
            "rv32ui/fence_i": "PicoRV32 has no Zifencei (self-modifying code)",
            "rv32ui/ma_data": "misaligned accesses trap by design (CATCH_MISALIGN), as in the v1 core",
        },
        # riscv-arch-test suites under rv32i_m/; A, Zifencei and privilege need
        # instructions or CSRs PicoRV32 does not have.
        "arch_suites": ("I", "M"),
    },
}


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, **kwargs)


def build(test: Path, march: str, out_dir: Path, prefix: str, arch: bool = False) -> tuple[Path, Path, dict]:
    """Build one test; returns the ELF, its flat binary, and its tohost/signature symbols."""
    out = out_dir / (test.parent.parent.name if arch else test.parent.name)
    out.mkdir(parents=True, exist_ok=True)
    elf, binary = out / f"{test.stem}.elf", out / f"{test.stem}.bin"
    if arch:
        # The 3.x test format: every I/M/A test is TEST_CASE_1. Its LA macro
        # aligns with RVC enabled; without -mno-relax the linker trims that
        # padding to a 2-byte c.nop, which a core without C cannot execute.
        env = [f"-I{ARCH_ENV}", f"-I{ARCH_TESTS / 'env'}", "-DXLEN=32", "-DTEST_CASE_1=True",
               "-mno-relax", f"-T{ARCH_ENV / 'link.ld'}"]
    else:
        env = [f"-I{ENV}", f"-I{TESTS / 'macros/scalar'}", f"-T{ENV / 'link.ld'}"]
    result = run([f"{prefix}gcc", f"-march={march}", "-mabi=ilp32", "-nostdlib", "-nostartfiles", *env,
                  str(test), "-o", str(elf)])
    if result.returncode:
        raise RuntimeError(f"build failed for {test}:\n{result.stderr}")
    run([f"{prefix}objcopy", "-O", "binary", str(elf), str(binary)], check=True)
    symbols = {line.split()[2]: int(line.split()[0], 16)
               for line in run([f"{prefix}nm", str(elf)], check=True).stdout.splitlines() if len(line.split()) == 3}
    if symbols.get("_start", symbols.get("rvtest_entry_point")) != ENTRY:
        raise RuntimeError(f"{test}: the entry point is not at 0x{ENTRY:08x}")
    return elf, binary, symbols


def has_signature(symbols: dict) -> bool:
    return symbols.get("end_signature", 0) > symbols.get("begin_signature", 0)


def execute(args, isa: str, elf: Path, binary: Path, symbols: dict,
            arch: bool = False) -> tuple[bool, str, str, str]:
    """Run the shell and Spike; returns (both exited 0, shell status line, trace text, Spike log text).

    Both also dump the begin_signature..end_signature words next to the ELF
    (.sig.dut, .sig.spike) when the program has that region.
    """
    trace, log = elf.with_suffix(".trace"), elf.with_suffix(".spike")
    for stale in (trace, log, elf.with_suffix(".sig.dut"), elf.with_suffix(".sig.spike")):
        stale.unlink(missing_ok=True)
    memory = MEMORY_BYTES[arch]
    command = [str(args.sim), f"+bin={binary}", f"+tohost={symbols['tohost']:x}", f"+trace={trace}",
               f"+max_cycles={args.max_cycles}", f"+mem_bytes={memory:x}"]
    spike_command = [str(args.spike), f"--isa={isa}", f"-m0x{ENTRY:08x}:0x{memory:x}",
                     # the same bound as the shell: a DUT retires at most one
                     # instruction per cycle (the margin covers Spike's boot ROM)
                     f"--instructions={args.max_cycles + 64}", "--log-commits"]
    if args.stall_seed is not None:
        command.append(f"+stall_seed={args.stall_seed}")
    if has_signature(symbols):
        command += [f"+signature={elf.with_suffix('.sig.dut')}",
                    f"+sig_begin={symbols['begin_signature']:x}", f"+sig_end={symbols['end_signature']:x}"]
        spike_command += [f"+signature={elf.with_suffix('.sig.spike')}", "+signature-granularity=4"]
    shell = run(command, timeout=600)
    with log.open("w") as stream:
        spike = subprocess.run(spike_command + [str(elf)], cwd=ROOT, stdout=subprocess.DEVNULL,
                               stderr=stream, timeout=600)
    status = shell.stdout.strip() or f"(no status; exit {shell.returncode}) {shell.stderr.strip()}"
    tokens = status.split()
    passed = shell.returncode == 0 and tokens[:2] == ["SHELL", "PASS"] and spike.returncode == 0
    if spike.returncode:
        status += f" [Spike exit {spike.returncode}]"
    return passed, status, trace.read_text() if trace.exists() else "", log.read_text()


def lockstep_check(trace_text: str, log_text: str, tohost: int, word_loads: bool) -> tuple[bool, str]:
    try:
        dut = lockstep.parse_trace(trace_text.splitlines())
        reference = lockstep.parse_spike(log_text.splitlines(), ENTRY, tohost)
    except ValueError as error:
        return False, f"rejected while parsing: {error}"
    return lockstep.compare(dut, reference, word_loads=word_loads)


# --- fault injection on the raw text ------------------------------------------------

def _trace_fields(line: str) -> list[str]:
    return line.split()


def _renumber(lines: list[str]) -> list[str]:
    first = int(lines[0].split()[0])
    return [" ".join([str(first + i)] + line.split()[1:]) for i, line in enumerate(lines)]


def _flip(text: str, bit: int, width: int = 8) -> str:
    return f"{int(text, 16) ^ (1 << bit):0{width}x}"


def trace_injections(lines: list[str]) -> dict[str, list[str]]:
    """Corrupted copies of the DUT trace, one per field or record-level fault."""
    middle = len(lines) // 2

    def first(predicate) -> int:
        return next(i for i in range(middle, len(lines) - 1) if predicate(_trace_fields(lines[i])))

    def edit(index: int, column: int, value: str) -> list[str]:
        fields = _trace_fields(lines[index])
        fields[column] = value
        return lines[:index] + [" ".join(fields)] + lines[index + 1:]

    writes = first(lambda f: f[4] != "0")
    access = first(lambda f: f[7] != "0" or f[8] != "0")
    store = first(lambda f: f[8] != "0")
    store_fields = _trace_fields(lines[store])
    wmask = int(store_fields[8], 16)
    lane = (wmask & -wmask).bit_length() - 1
    return {
        "pc": edit(middle, 1, _flip(lines[middle].split()[1], 2)),
        "insn": edit(middle, 2, _flip(lines[middle].split()[2], 20)),
        "trap": edit(middle, 3, "1"),
        "rd": edit(writes, 4, str(int(_trace_fields(lines[writes])[4]) ^ 1)),
        "rd_value": edit(writes, 5, _flip(_trace_fields(lines[writes])[5], 0)),
        "mem_addr": edit(access, 6, _flip(_trace_fields(lines[access])[6], 2)),
        "wmask_lane": edit(store, 8, f"{(wmask << 1) & 0xf or wmask >> 1:x}"),
        "wmask_shape": edit(store, 8, "5"),
        "store_data": edit(store, 10, _flip(store_fields[10], 8 * lane)),
        "dropped": lines[:middle] + lines[middle + 1:],
        "duplicated": lines[:middle + 1] + lines[middle:],
        "missing": _renumber(lines[:middle] + lines[middle + 1:]),
        "extra": _renumber(lines[:middle + 1] + lines[middle:]),
        "truncated": lines[:-1],
        "no_tohost_store": lines[:-1] + [" ".join(_trace_fields(lines[-1])[:8] + ["0", "00000000", "00000000"])],
    }


def spike_injections(lines: list[str], tohost: int) -> dict[str, list[str]]:
    """Corrupted copies of Spike's log: the reference parser must read each field too."""
    # Commit lines of the reference stream: from the entry to the tohost store
    # (Spike keeps logging its spin loop after that).
    length = len(lockstep.parse_spike(lines, ENTRY, tohost))
    commits = [i for i, line in enumerate(lines) if lockstep._SPIKE.match(line.strip())]
    start = next(n for n, i in enumerate(commits) if int(lockstep._SPIKE.match(lines[i].strip()).group(1), 16) == ENTRY)
    commits = commits[start:start + length]
    middle = commits[len(commits) // 2]

    def first(predicate):
        for i in commits:
            match = predicate(lines[i]) if i >= middle else None
            if match:
                return i, match
        raise ValueError("no commit to corrupt")

    def edit(index: int, start: int, end: int, value: str) -> list[str]:
        line = lines[index]
        return lines[:index] + [line[:start] + value + line[end:]] + lines[index + 1:]

    reg, reg_match = first(lambda line: next((m for m in lockstep._SPIKE_REG.finditer(line)
                                              if m.group(1) != "0"), None))
    store, store_match = first(lambda line: next((m for m in lockstep._SPIKE_MEM.finditer(line)
                                                  if m.group(2) is not None), None))
    pc_match = lockstep._SPIKE.match(lines[middle])
    value, data = reg_match.group(2), store_match.group(2)
    return {
        "spike_rd_value": edit(reg, *reg_match.span(2), _flip(value, 0, len(value))),
        "spike_store_data": edit(store, *store_match.span(2), _flip(data, 0, len(data))),
        "spike_mem_addr": edit(store, *store_match.span(1), _flip(store_match.group(1), 2)),
        "spike_pc": edit(middle, *pc_match.span(1), _flip(pc_match.group(1), 2)),
    }


def retired_check(status: str, trace_text: str) -> tuple[bool, str]:
    """The shell's own retired count must equal the non-trap records in its trace."""
    fields = dict(item.split("=", 1) for item in status.split()[2:] if "=" in item)
    records = sum(1 for line in trace_text.splitlines() if line.strip() and line.split()[3] == "0")
    if "retired" not in fields:
        return False, "the shell reported no retired count"
    if int(fields["retired"]) != records:
        return False, f"the shell retired {fields['retired']} instructions but its trace holds {records}"
    return True, ""


def signature_check(elf: Path) -> tuple[bool, str]:
    dut_path, spike_path = elf.with_suffix(".sig.dut"), elf.with_suffix(".sig.spike")
    if not dut_path.exists() or not spike_path.exists():
        return False, "a signature was not written"
    dut, spike = dut_path.read_text().split(), spike_path.read_text().split()
    if not spike:
        return False, "Spike's signature is empty"
    for index, (ours, theirs) in enumerate(zip(dut, spike)):
        if ours != theirs:
            return False, f"signature word {index} differs: dut {ours}, spike {theirs}"
    if len(dut) != len(spike):
        return False, f"signature lengths differ: dut {len(dut)} words, spike {len(spike)}"
    return True, f"{len(dut)} signature words match"


@dataclass
class Outcome:
    passed: bool
    summary: str        # one line: shell status; lockstep; signature
    detail: str         # the full lockstep report
    cycles: int
    retired: int
    trace_text: str
    log_text: str
    tohost: int


def run_program(args, config, source: Path, prefix: str, arch: bool = False) -> Outcome:
    """Build, run and check one program (the four conditions in the module docstring)."""
    elf, binary, symbols = build(source, config["march"], args.build_dir, prefix, arch=arch)
    passed, status, trace_text, log_text = execute(args, config["spike_isa"], elf, binary, symbols, arch=arch)
    counted, count_message = retired_check(status, trace_text)
    ok, message = lockstep_check(trace_text, log_text, symbols["tohost"], config["word_loads"])
    signed, signature_message = (signature_check(elf) if has_signature(symbols)
                                 else (True, "no signature region"))
    summary = f"{status.removeprefix('SHELL ')}; lockstep: {message.splitlines()[0]}; {signature_message}"
    if not counted:
        summary += f"; {count_message}"
    fields = dict(item.split("=", 1) for item in status.split()[2:] if "=" in item)
    return Outcome(passed and counted and ok and signed, summary, message,
                   int(fields.get("cycles", 0)), int(fields.get("retired", 0)),
                   trace_text, log_text, symbols["tohost"])


def inject(args, config, prefix: str) -> int:
    test = TESTS / f"{args.only or 'rv32ui/sh'}.S"
    run = run_program(args, config, test, prefix)
    tohost, trace_text, log_text = run.tohost, run.trace_text, run.log_text
    if not run.passed:
        print(f"FAIL: the uncorrupted run does not pass: {run.summary}")
        return 1
    trace_lines, log_lines = trace_text.splitlines(), log_text.splitlines()
    cases = {**trace_injections(trace_lines), **spike_injections(log_lines, tohost)}
    missed = []
    for name, corrupted in cases.items():
        spike_side = name.startswith("spike_")
        caught, message = lockstep_check("\n".join(trace_lines if spike_side else corrupted),
                                         "\n".join(corrupted if spike_side else log_lines),
                                         tohost, config["word_loads"])
        caught = not caught
        print(f"{'PASS' if caught else 'FAIL'}: injected {name:16s} -> "
              f"{message.splitlines()[0] if caught else 'NOT DETECTED'}")
        if not caught:
            missed.append(name)
    print(f"{'PASS' if not missed else 'FAIL'}: {len(cases) - len(missed)}/{len(cases)} injected faults "
          f"detected on {test.parent.name}/{test.stem}")
    return 1 if missed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dut", choices=sorted(DUTS), required=True)
    parser.add_argument("--sim", type=Path, required=True)
    parser.add_argument("--spike", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/core_tests")
    parser.add_argument("--only", help="run one test, e.g. rv32ui/add (or I/add-01 with --arch)")
    parser.add_argument("--arch", action="store_true", help="run riscv-arch-test with signature comparison")
    parser.add_argument("--stall-seed", type=int, help="random memory back-pressure with this seed")
    parser.add_argument("--max-cycles", type=int, default=MAX_CYCLES)
    parser.add_argument("--inject", action="store_true",
                        help="prove the harness rejects a corrupted trace or reference log")
    args = parser.parse_args()
    config = DUTS[args.dut]
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")

    if args.inject:
        return inject(args, config, prefix)

    if args.arch:
        def arch_path(name: str) -> Path:
            suite, stem = name.split("/")
            return ARCH_TESTS / "rv32i_m" / suite / "src" / f"{stem}.S"
        tests = ([arch_path(args.only)] if args.only else
                 [path for suite in config["arch_suites"]
                  for path in sorted((ARCH_TESTS / "rv32i_m" / suite / "src").glob("*.S"))])
    else:
        tests = ([TESTS / f"{args.only}.S"] if args.only else
                 [path for suite in config["suites"] for path in sorted((TESTS / suite).glob("*.S"))])
    failures, skipped, total_cycles, total_retired = [], [], 0, 0
    for test in tests:
        name = f"{test.parent.parent.name if args.arch else test.parent.name}/{test.stem}"
        if name in config["skip"]:
            skipped.append(name)
            print(f"SKIP: {name} ({config['skip'][name]})")
            continue
        run = run_program(args, config, test, prefix, arch=args.arch)
        print(f"{'PASS' if run.passed else 'FAIL'}: {name} {run.summary}")
        if not run.passed:
            failures.append(name)
            print(run.detail)
        else:
            total_cycles += run.cycles
            total_retired += run.retired
    ran = len(tests) - len(skipped)
    mode = f", stall seed {args.stall_seed}" if args.stall_seed is not None else ""
    what = "riscv-arch-test programs" if args.arch else "tests"
    what += " pass in lockstep and by signature"
    print(f"{'PASS' if not failures else 'FAIL'}: {args.dut} {ran - len(failures)}/{ran} {what} "
          f"with Spike ({len(skipped)} skipped{mode}); {total_retired} instructions in {total_cycles} cycles")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
