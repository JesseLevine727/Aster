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
are riscv-arch-test (3.10.0). With
`--random N` they are N seeded constrained-random programs (scripts/rvgen.py),
and the run reports read-after-write hazard coverage. With `--kernels` they are
the CPU kernels of docs/cpu.md §7, built from their SoC sources, flags and
unmodified headers with only the shell's start-up and layout
(verification/core/kernels/); the shell ends each run at its AsterBench
record, Spike's stream is cut at the same store, and each kernel's window
instruction count and checksum must equal the retained Phase 17 baseline
record's; the run reports the window and, with --cpi-model, the trace-driven
CPI model of the Aster core pipeline over it.

Traps and CSRs (the Aster core from 18.3): Spike runs in the core's
configuration (the DUT's spike_isa and spike_args: machine mode only, no PMP,
no debug triggers, `wfi` a no-op) and with `-l`, whose exception lines become
trap records (scripts/lockstep.py); its instruction budget allows for the
traps (each trap ends one of its 5,000-instruction steps early, and Spike
counts the whole step). The `traps` suite (verification/core/traps) raises
every exception class, in lockstep, with the shell answering accesses outside
memory with d_rsp_error (+bus_error_traps).

`--interrupts SEED` runs the programs with the shell raising MEIP and MSIP at
random (+irq_random): each is built with ASTER_INTERRUPTS (the environment
enables the interrupts and its handler clears them), Spike runs the same
program uninterrupted, and the DUT's stream with each interrupt handler cut
out (from its first record, marked rvfi_intr, through its `mret`) must equal
Spike's: every instruction of the program executes exactly once and in order
with the same results, however the interrupts fell. The run reports which
instruction classes were interrupted and which came next (the instruction
killed and re-executed after the handler), and fails if any pair of
INTERRUPT_CLASSES never occurred. The `interrupts` suite holds self-checking
directed interrupt programs, run in the shell alone (+irq_device).

`--inject` instead proves the harness catches a deliberately corrupted run: it
corrupts the raw DUT trace text (every field, dropped, duplicated and extra
records, truncation) and the raw Spike log, re-parses both, and requires every
corruption to be rejected by a parser or the comparator. With `--dut aster`
it also corrupts a trapping, CSR-writing run's CSR values, trap causes and
trap records.

    run_core_tests.py --dut picorv32 --sim build/core_shell_picorv32/core_shell_picorv32 --spike spike
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
from dataclasses import dataclass
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cpi_model  # noqa: E402
import lockstep  # noqa: E402
import rvgen  # noqa: E402

ENV = ROOT / "verification/core/env"
TESTS = ROOT / "vendor/riscv-tests/isa"
ARCH_ENV = ROOT / "verification/core/arch_env"
DIRECTED = ROOT / "verification/core/directed"     # the "directed" suite
TRAPS = ROOT / "verification/core/traps"           # directed exceptions, in lockstep (18.3)
INTERRUPTS = ROOT / "verification/core/interrupts" # self-checking interrupt programs, shell only (18.3)
SPIKE_STEP = 5000                                  # Spike's INTERLEAVE: a trap ends its step early
ARCH_TESTS = ROOT / "vendor/riscv-arch-test/riscv-test-suite"
ENTRY = 0x80000000
# Shell memory: 96 KiB for riscv-tests (the v2 SRAM), 2 MiB for arch-test
# programs (matching env/link.ld and arch_env/link.ld).
MEMORY_BYTES = {False: 0x18000, True: 0x200000}
KERNEL_ENV = ROOT / "verification/core/kernels"
IO_PAGE = (0x20000000, 0x10000)   # the SoC register page, plain memory for the kernels
# In Spike the page's performance-counter 4 KiB is the aster_clock device
# (verification/core/spike/aster_clock.cc), so plain memory skips it.
CLOCK_PAGE = (0x20003000, 0x1000)
ASTER_CLOCK = ROOT / "build/spike/libaster_clock.so"
# --cpi-check: on a memory that answers on time, the Aster core's run takes the
# model's cycles plus this: its first instruction enters Execute in the shell's
# cycle 4 (fetch, F1, F2, Decode), and the shell counts the tohost store when its
# RVFI record appears, four cycles after the store entered Execute.
CPI_CHECK_OFFSET = 7
KERNEL_MAX_CYCLES = 30_000_000    # the longest run (coherent scalar Conv2D, two-port shell at +latency=1) needs 8.5 M
# The CPU set of docs/cpu.md §7: sources and the Makefile variable holding each
# one's SoC compile flags (read with `make print-VAR`, so they cannot drift).
# (sources, flags variable, make overrides, Phase 17 baseline capture); Dhrystone's
# vendor files use their own flags. Two Conv2D builds are listed: the gate's, the
# coherent SoC's scalar engine (docs/cpu.md §7), and the minimal top's, run as a
# cross-check outside the gate.
GATE_KERNELS = ("coremark", "dhrystone", "sort_search", "fft", "strided", "conv2d_scalar_coh", "reduction")
KERNELS = {
    "coremark": ([f"vendor/coremark/{name}.c" for name in
                  ("core_main", "core_list_join", "core_matrix", "core_state", "core_util")]
                 + ["software/benchmarks/coremark_port/core_portme.c"], "COREMARK_CFLAGS", [], "minimal_coremark"),
    "dhrystone": (["software/benchmarks/dhrystone_port/dhry_port.c"], "DHRY_CFLAGS", [], "minimal_dhrystone"),
    "sort_search": (["software/benchmarks/workload_sort_search.c"], "WORKLOAD_CFLAGS", [], "minimal_sort_search"),
    "fft": (["software/benchmarks/workload_fft.c"], "WORKLOAD_CFLAGS", [], "minimal_fft"),
    "strided": (["software/benchmarks/workload_strided.c"], "WORKLOAD_CFLAGS", [], "minimal_strided"),
    "conv2d": (["software/benchmarks/workload_conv2d.c"], "WORKLOAD_CFLAGS", [], "minimal_conv2d"),
    "conv2d_scalar_coh": (["software/benchmarks/workload_conv2d_engine.c", "software/benchmarks/xe_kernels.c",
                           "software/drivers/aster_npu.c"], "CONV_CFLAGS", ["CONV_ENGINE=scalar_coh"],
                          "conv2d_scalar_coh"),
    "reduction": (["software/benchmarks/workload_reduce.c"], "REDUCE_CFLAGS", ["REDUCE_WORKERS=1"], "reduce_scalar"),
}
assert set(GATE_KERNELS) <= set(KERNELS), "every gate kernel is a listed kernel"
DHRYSTONE_VENDOR = (["vendor/dhrystone/dhry_1.c", "vendor/dhrystone/dhry_2.c"], "DHRY_VENDOR_CFLAGS")
# A test that runs away (for example a failure that never reports) ends here;
# the longest rv32ui/rv32um test takes a few thousand cycles.
MAX_CYCLES = 200_000

# What each DUT implements, and the tests it cannot run with a reason.
DUTS = {
    "picorv32": {
        "march": "rv32im", "spike_isa": "rv32im", "suites": ("rv32ui", "rv32um", "directed"),
        # PicoRV32 reports full-word RVFI read masks on sub-word loads.
        "word_loads": True,
        # It fetches only the instruction it executes (no wrong-path fetches).
        "prefetches": False,
        "skip": {
            "rv32ui/fence_i": "PicoRV32 has no Zifencei (self-modifying code)",
            "rv32ui/ma_data": "misaligned accesses trap by design (CATCH_MISALIGN), as in the v1 core",
        },
        # riscv-arch-test suites under rv32i_m/; A, Zifencei and privilege need
        # instructions or CSRs PicoRV32 does not have.
        "arch_suites": ("I", "M"), "arch_isa": "RV32IM", "arch_params": {},
        "spike_args": [], "spike_traps": False, "shell_args": [], "interrupt_suites": (),
    },
    # The Aster core (docs/cpu.md), in the two-port shell. Milestones 18.1-18.3:
    # RV32IM, Zicsr, traps, interrupts and counters, with exact RVFI byte masks;
    # each later milestone widens the ISA here. Spike is configured as the core
    # is (docs/cpu.md §6): machine mode only, no PMP, no debug triggers, `wfi`
    # a no-op.
    "aster": {
        "march": "rv32im_zicsr", "spike_isa": "rv32im_zicsr_zicntr",
        "spike_args": ["--priv=m", "--pmpregions=0", "--triggers=0", "--wfi-as-nop"], "spike_traps": True,
        "shell_args": ["+bus_error_traps"],
        "suites": ("rv32ui", "rv32um", "rv32mi", "directed", "traps", "interrupts"),
        "interrupt_suites": ("rv32ui", "rv32um"),
        "word_loads": False,
        "prefetches": True,
        "skip": {
            "rv32ui/fence_i": "Zifencei comes in milestone 18.4",
            "rv32mi/breakpoint": "no debug triggers (Sdtrig is not in docs/cpu.md §3; Spike runs with --triggers=0)",
            "rv32mi/pmpaddr": "no PMP (docs/cpu.md §3; Spike runs with --pmpregions=0)",
        },
        "arch_suites": ("I", "M", "privilege"), "arch_isa": "RV32IMZicsr",
        "arch_params": {"hw_data_misaligned_support": "False"},
    },
}


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, **kwargs)


_ARCH_CASE = re.compile(r'RVTEST_CASE\(\s*\d+\s*,\s*"([^"]*)"')


def arch_case(test: Path, config: dict) -> list[str] | None:
    """The macros of the first RVTEST_CASE of an arch-test program whose
    conditions hold for the DUT, as riscof selects them (`check ISA:=regex(R)`
    on the DUT's ISA string, `check <parameter>:=<value>` on its parameters);
    None if no case applies."""
    for case in _ARCH_CASE.findall(test.read_text()):
        clauses = [clause.strip() for clause in case.removeprefix("//").split(";") if clause.strip()]
        applies = True
        for clause in clauses:
            check = re.fullmatch(r"check\s+(\w+):=(.*)", clause)
            if check and check.group(1) == "ISA":
                regex = re.fullmatch(r"regex\((.*)\)", check.group(2))
                applies &= bool(regex and re.match(regex.group(1), config["arch_isa"]))
            elif check:
                applies &= config["arch_params"].get(check.group(1)) == check.group(2)
        if applies:
            return [f"{name}={value}" for name, value in
                    (re.fullmatch(r"def\s+(\w+)=(\S+)", clause).groups() for clause in clauses
                     if clause.startswith("def "))]
    return None


def build(test: Path, march: str, out_dir: Path, prefix: str, arch: bool = False,
          defines: tuple = ()) -> tuple[Path, Path, dict]:
    """Build one test; returns the ELF, its flat binary, and its tohost/signature symbols."""
    out = out_dir / (test.parent.parent.name if arch else test.parent.name)
    out.mkdir(parents=True, exist_ok=True)
    elf, binary = out / f"{test.stem}.elf", out / f"{test.stem}.bin"
    if arch:
        # The 3.x test format: the macros come from the program's applicable
        # RVTEST_CASE (arch_case; the caller passes them). Its LA macro aligns
        # with RVC enabled; without -mno-relax the linker trims that padding to
        # a 2-byte c.nop, which a core without C cannot execute.
        env = [f"-I{ARCH_ENV}", f"-I{ARCH_TESTS / 'env'}", "-DXLEN=32",
               "-mno-relax", f"-T{ARCH_ENV / 'link.ld'}"]
    else:
        # Directed tests end the shell memory at their shell_memory_end symbol.
        # encoding.h (the CSR and cause names) comes with the vendored arch-test.
        layout = DIRECTED if test.parent in (DIRECTED, TRAPS, INTERRUPTS) else ENV
        env = [f"-I{ENV}", f"-I{TESTS / 'macros/scalar'}", f"-I{ARCH_TESTS / 'env'}", f"-T{layout / 'link.ld'}"]
    env += [f"-D{define}" for define in defines]
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


def suite_dir(suite: str) -> Path:
    """A test suite's directory: the riscv-tests suites, or the shell's own directed ones."""
    return {"directed": DIRECTED, "traps": TRAPS, "interrupts": INTERRUPTS}.get(suite, TESTS / suite)


def test_path(name: str) -> Path:
    suite, stem = name.split("/")
    return suite_dir(suite) / f"{stem}.S"


def has_signature(symbols: dict) -> bool:
    return symbols.get("end_signature", 0) > symbols.get("begin_signature", 0)


def execute(args, config: dict, elf: Path, binary: Path, symbols: dict, arch: bool = False,
            kernel: bool = False, shell_only: bool = False, shell_extra: tuple = ()) -> tuple[bool, str, str, str]:
    """Run the shell and Spike; returns (both exited 0, shell status line, trace text, Spike log text).

    Both also dump the begin_signature..end_signature words next to the ELF
    (.sig.dut, .sig.spike) when the program has that region.
    """
    trace, log = elf.with_suffix(".trace"), elf.with_suffix(".spike")
    for stale in (trace, log, elf.with_suffix(".sig.dut"), elf.with_suffix(".sig.spike")):
        stale.unlink(missing_ok=True)
    memory = symbols["shell_memory_end"] - ENTRY if "shell_memory_end" in symbols else MEMORY_BYTES[arch]
    command = [str(args.sim), f"+bin={binary}", f"+tohost={symbols['tohost']:x}", f"+trace={trace}",
               f"+max_cycles={args.max_cycles}", f"+mem_bytes={memory:x}"]
    regions = f"-m0x{ENTRY:08x}:0x{memory:x}"
    if kernel:
        console = elf.with_suffix(".console")
        console.unlink(missing_ok=True)
        command += ["+io_page", f"+console={console}", "+kernel_end"]
        below, above = CLOCK_PAGE[0] - IO_PAGE[0], IO_PAGE[0] + IO_PAGE[1] - CLOCK_PAGE[0] - CLOCK_PAGE[1]
        regions += (f",0x{IO_PAGE[0]:08x}:0x{below:x},0x{CLOCK_PAGE[0] + CLOCK_PAGE[1]:08x}:0x{above:x}"
                    f" --extlib={args.aster_clock} --device=aster_clock,0x{CLOCK_PAGE[0]:08x}")
    spike_command = [str(args.spike), f"--isa={config['spike_isa']}", *config["spike_args"], *regions.split(),
                     # the same bound as the shell: a DUT retires at most one
                     # instruction per cycle (the margin covers Spike's boot ROM)
                     f"--instructions={args.max_cycles + 64}", "--log-commits"]
    if config["spike_traps"] and not kernel:
        spike_command.append("-l")              # exception lines: the trap records
    if args.stall_seed is not None:
        command.append(f"+stall_seed={args.stall_seed}")
    command += config["shell_args"] + list(shell_extra) + args.shell_arg
    if has_signature(symbols):
        command += [f"+signature={elf.with_suffix('.sig.dut')}",
                    f"+sig_begin={symbols['begin_signature']:x}", f"+sig_end={symbols['end_signature']:x}"]
        spike_command += [f"+signature={elf.with_suffix('.sig.spike')}", "+signature-granularity=4"]
    shell = run(command, timeout=600)
    if shell_only:
        status = shell.stdout.strip() or f"(no status; exit {shell.returncode}) {shell.stderr.strip()}"
        return (shell.returncode == 0 and status.split()[:2] == ["SHELL", "PASS"], status,
                trace.read_text() if trace.exists() else "", "")
    if trace.exists():
        # Each trap ends one of Spike's steps early, and Spike counts the whole step.
        traps = sum(1 for line in trace.read_text().splitlines() if line.split()[3:4] == ["1"])
        spike_command = [item if not item.startswith("--instructions=")
                         else f"--instructions={args.max_cycles + 64 + SPIKE_STEP * (traps + 1)}"
                         for item in spike_command]
    if kernel:
        # A kernel spins after its record, so Spike is bounded by what the shell
        # retired (plus its boot ROM) rather than by a tohost store.
        retired = next((int(item.split("=", 1)[1]) for item in shell.stdout.split()
                        if item.startswith("retired=")), args.max_cycles)
        spike_command = [item if not item.startswith("--instructions=") else f"--instructions={retired + 64}"
                         for item in spike_command]
    with log.open("w") as stream:
        spike = subprocess.run(spike_command + [str(elf)], cwd=ROOT, stdout=subprocess.DEVNULL,
                               stderr=stream, timeout=600)
    status = shell.stdout.strip() or f"(no status; exit {shell.returncode}) {shell.stderr.strip()}"
    tokens = status.split()
    passed = shell.returncode == 0 and tokens[:2] == ["SHELL", "PASS"] and spike.returncode == 0
    if spike.returncode:
        status += f" [Spike exit {spike.returncode}]"
    return passed, status, trace.read_text() if trace.exists() else "", log.read_text()


def lockstep_check(trace_text: str, log_text: str, tohost: int, word_loads: bool,
                   end=None) -> tuple[bool, str]:
    try:
        dut = lockstep.parse_trace(trace_text.splitlines())
        reference = lockstep.parse_spike(log_text.splitlines(), ENTRY, tohost, end=end)
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


def trap_injections(lines: list[str]) -> dict[str, list[str]]:
    """Corrupted copies of a trace with trap records and CSR writes (the Aster
    core from 18.3): a CSR's value, a missing and an extra CSR write, a trap's
    mcause, mepc and mtval, and a dropped and an extra trap record."""
    def find(predicate) -> int:
        return next(i for i, line in enumerate(lines) if predicate(line.split()))

    def token(index: int, prefix: str) -> int:
        return next(j for j, item in enumerate(lines[index].split()) if item.startswith(prefix))

    def edit(index: int, position: int, value: str | None) -> list[str]:
        fields = lines[index].split()
        if value is None:
            del fields[position]
        else:
            fields[position] = value
        return lines[:index] + [" ".join(fields)] + lines[index + 1:]

    def flip(item: str, bit: int) -> str:
        name, value = item.split("=")
        return f"{name}={int(value, 16) ^ (1 << bit):08x}"

    write = find(lambda f: f[3] == "0" and any(item.startswith("c") for item in f[11:]))
    plain = find(lambda f: f[3] == "0" and len(f) == 11)
    trap = find(lambda f: f[3] == "1")
    csr = token(write, "c")
    return {
        "csr_value": edit(write, csr, flip(lines[write].split()[csr], 4)),
        "csr_missing": edit(write, csr, None),
        "csr_extra": lines[:plain] + [lines[plain] + " c340=00000000"] + lines[plain + 1:],
        "trap_cause": edit(trap, token(trap, "c342="), flip(lines[trap].split()[token(trap, "c342=")], 0)),
        "trap_epc": edit(trap, token(trap, "c341="), flip(lines[trap].split()[token(trap, "c341=")], 2)),
        "trap_tval": edit(trap, token(trap, "c343="), flip(lines[trap].split()[token(trap, "c343=")], 3)),
        "trap_dropped": _renumber(lines[:trap] + lines[trap + 1:]),
        "trap_extra": _renumber(lines[:trap + 1] + lines[trap:]),
    }


def spike_trap_injections(lines: list[str]) -> dict[str, list[str]]:
    """Corrupted copies of Spike's log: a CSR write's value, a trap's cause, epc and tval."""
    def first(regex):
        return next((i, m) for i, line in enumerate(lines) if (m := regex.search(line)))

    def edit(index: int, start: int, end: int, value: str) -> list[str]:
        line = lines[index]
        return lines[:index] + [line[:start] + value + line[end:]] + lines[index + 1:]

    csr, csr_match = first(lockstep._SPIKE_CSR)
    trap, trap_match = first(re.compile(r"exception (trap_\w+), epc 0x([0-9a-f]+)"))
    tval, tval_match = first(re.compile(r"tval 0x([0-9a-f]+)"))
    other = "trap_breakpoint" if trap_match.group(1) != "trap_breakpoint" else "trap_machine_ecall"
    return {
        "spike_csr_value": edit(csr, *csr_match.span(3), _flip(csr_match.group(3), 4)),
        "spike_trap_cause": edit(trap, *trap_match.span(1), other),
        "spike_trap_epc": edit(trap, *trap_match.span(2), _flip(trap_match.group(2), 2)),
        "spike_tval": edit(tval, *tval_match.span(1), _flip(tval_match.group(1), 3)),
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


# --- read-after-write hazard coverage -------------------------------------------------

# Operands each opcode reads, by consumer class: (rs1 class, rs2 class).
_READS = {0x33: ("alu", "alu"), 0x13: ("alu", None), 0x03: ("load-addr", None),
          0x23: ("store-addr", "store-data"), 0x63: ("branch", "branch"), 0x67: ("jalr", None)}
HAZARD_PRODUCERS = ("alu", "load", "link", "mul", "div", "csr")
HAZARD_CONSUMERS = ("alu", "muldiv", "load-addr", "store-addr", "store-data", "branch", "jalr", "csr")


def _producer(insn: int) -> str:
    opcode = insn & 0x7F
    if opcode == 0x33 and insn >> 25 == 1:
        return "mul" if (insn >> 12) & 7 < 4 else "div"
    # AMOs, lr and sc return their result from the memory, as a load does.
    return {0x03: "load", 0x2F: "load", 0x6F: "link", 0x67: "link", 0x73: "csr"}.get(opcode, "alu")


def required_bins(extensions: str) -> list[tuple[str, int, str]]:
    """The read-after-write pairs a random run must exercise.

    Every producer feeds every data consumer (ALU operands, multiply/divide
    operands, branch compare, store data) at distances 1-4 (4: the producer in
    W while the consumer reads the register file in Decode, the same-cycle
    write-through of docs/cpu.md §4). Address and
    jump-target consumers read rs1 through the same forwarding path, and are
    required from the producers that realistically make addresses: ALU and
    load results, and link values for loads and `jalr`. (A multiply result or a
    code address used as a store address is legal but not a distinct hazard.)
    """
    muldiv, zicsr = "m" in extensions.split(","), "zicsr" in extensions.split(",")
    producers = ["alu", "load", "link"] + (["mul", "div"] if muldiv else []) + (["csr"] if zicsr else [])
    data = ["alu", "branch", "store-data"] + (["muldiv"] if muldiv else []) + (["csr"] if zicsr else [])
    address = {"alu": ["load-addr", "store-addr", "jalr"], "load": ["load-addr", "store-addr", "jalr"],
               "link": ["load-addr", "jalr"]}
    return [(p, d, c) for p in producers for d in (1, 2, 3, 4) for c in data + address.get(p, [])]


def hazard_coverage(records: list) -> dict[tuple[str, int, str], int]:
    """Count (producer class, distance 1-4, consumer operand class) read-after-write pairs."""
    bins: dict[tuple[str, int, str], int] = {}
    writer: dict[int, tuple[int, str]] = {}
    for index, record in enumerate(records):
        if record.trap:
            continue
        opcode = record.insn & 0x7F
        reads = _READS.get(opcode)
        if opcode == 0x73 and (record.insn >> 12) & 7 in (1, 2, 3):
            reads = ("csr", None)                 # a CSR instruction's register source
        if reads:
            if opcode == 0x33 and record.insn >> 25 == 1:
                reads = ("muldiv", "muldiv")
            for register, consumer in zip(((record.insn >> 15) & 31, (record.insn >> 20) & 31), reads):
                if consumer and register and register in writer:
                    distance = index - writer[register][0]
                    if distance <= 4:
                        key = (writer[register][1], distance, consumer)
                        bins[key] = bins.get(key, 0) + 1
        if record.rd:
            writer[record.rd[0]] = (index, _producer(record.insn))
    return bins


def retired_check(status: str, trace_text: str) -> tuple[bool, str]:
    """The shell's own retired count must equal the non-trap records in its trace."""
    fields = dict(item.split("=", 1) for item in status.split()[2:] if "=" in item)
    lines = [line.split() for line in trace_text.splitlines() if line.strip()]
    if any(len(fields_) < 11 for fields_ in lines):
        return False, "the trace has a malformed line"
    records = sum(1 for fields_ in lines if fields_[3] == "0")
    if not fields.get("retired", "").isdigit():
        return False, "the shell reported no retired count"
    if int(fields["retired"]) != records:
        return False, f"the shell retired {fields['retired']} instructions but its trace holds {records}"
    return True, ""


def make_variable(name: str, overrides: list[str] = ()) -> list[str]:
    """A Makefile variable (with only these overrides) split as the recipe's shell would split it.

    The lookup runs with a minimal environment: a parent make exports its
    command-line variables (and MAKEFLAGS) to its recipes, and the flag inputs
    are `?=` defaults, so `make check COREMARK_ITERATIONS=10` or an exported
    shell variable would otherwise change a kernel.
    """
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL") if key in os.environ}
    result = subprocess.run(["make", "-s", "-f", "Makefile", f"print-{name}", *overrides], cwd=ROOT,
                            capture_output=True, text=True, check=True, env=environment)
    return shlex.split(result.stdout)


def build_kernel(name: str, out_dir: Path, prefix: str) -> tuple[Path, Path, dict]:
    """A CPU kernel with its SoC flags, against the shell platform, start-up and layout."""
    sources, flags_variable, overrides, _ = KERNELS[name]
    out = out_dir / "kernels" / name
    out.mkdir(parents=True, exist_ok=True)
    flags = make_variable(flags_variable, overrides)
    objects = []
    if name == "dhrystone":
        vendor_sources, vendor_variable = DHRYSTONE_VENDOR
        for source in vendor_sources:
            obj = out / (Path(source).stem + ".o")
            result = run([f"{prefix}gcc", *make_variable(vendor_variable), "-c", "-o", str(obj), source])
            if result.returncode:
                raise RuntimeError(f"build failed for {source}:\n{result.stderr}")
            objects.append(str(obj))
    elf, binary = out / f"{name}.elf", out / f"{name}.bin"
    result = run([f"{prefix}gcc", *flags, f"-T{KERNEL_ENV / 'link.ld'}", "-o", str(elf),
                  str(KERNEL_ENV / "start.S"), *sources, *objects, "-lgcc"])
    if result.returncode:
        raise RuntimeError(f"build failed for kernel {name}:\n{result.stderr}")
    run([f"{prefix}objcopy", "-O", "binary", str(elf), str(binary)], check=True)
    symbols = {line.split()[2]: int(line.split()[0], 16)
               for line in run([f"{prefix}nm", str(elf)], check=True).stdout.splitlines() if len(line.split()) == 3}
    return elf, binary, symbols


BASELINE = ROOT / "docs/results/phase17/baseline-56067a15815a"   # the bundle docs/cpu.md §7 names


def baseline_record(capture: str) -> dict:
    """The retained Phase 17 baseline record of a capture (sync1, first repeat)."""
    path = BASELINE / "records" / "sync1" / "r1" / f"{capture}.record"
    try:
        line = [line for line in path.read_text().splitlines() if line.startswith("ASTERBENCH,")][-1]
    except (OSError, IndexError):
        raise RuntimeError(f"no baseline record {path.relative_to(ROOT)}") from None
    fields = dict(item.split("=", 1) for item in line.split(",")[1:] if "=" in item)
    if "retired" not in fields or "checksum" not in fields:
        raise RuntimeError(f"baseline record {path.relative_to(ROOT)} lacks retired or checksum")
    return fields


def run_kernels(args, config, prefix: str) -> int:
    if not args.aster_clock.exists():
        print(f"FAIL: {args.aster_clock} is missing (make core-kernels builds it)")
        return 1
    names = [args.only] if args.only else list(KERNELS)
    unknown = [name for name in names if name not in KERNELS]
    if unknown:
        print(f"FAIL: unknown kernel {', '.join(unknown)} (known: {', '.join(KERNELS)})")
        return 1
    failures, rows = [], []
    for name in names:
        try:
            baseline = baseline_record(KERNELS[name][3])
            elf, binary, symbols = build_kernel(name, args.build_dir, prefix)
            passed, status, trace_text, log_text = execute(args, config, elf, binary, symbols,
                                                           kernel=True)
        except (RuntimeError, subprocess.SubprocessError) as error:
            print(f"FAIL: kernel {name}: {str(error).splitlines()[0]}")
            failures.append(name)
            continue
        counted, count_message = retired_check(status, trace_text)
        ok, message = lockstep_check(trace_text, log_text, symbols["tohost"], config["word_loads"],
                                     end=lockstep.KernelEnd())
        console = elf.with_suffix(".console")
        # The run ends at the record's newline, so the record is the console's last line.
        lines = console.read_text(errors="replace").splitlines() if console.exists() else []
        record = lines[-1] if lines and lines[-1].startswith("ASTERBENCH,") else ""
        fields = dict(item.split("=", 1) for item in status.split()[2:] if "=" in item)
        record_fields = dict(item.split("=", 1) for item in record.split(",")[1:] if "=" in item)
        problems = [] if count_message == "" else [count_message]
        if record_fields.get("status") != "PASS":
            problems.append("the console does not end with a passing AsterBench record")
        if "window_cycles" not in fields:
            problems.append("no measurement window")
        else:
            # Fidelity to the SoC build: the same window and result as the retained baseline.
            if int(fields["window_retired"]) != int(baseline["retired"], 16):
                problems.append(f"window retired {fields['window_retired']} != baseline {int(baseline['retired'], 16)}")
            if record_fields.get("checksum") != baseline["checksum"]:
                problems.append(f"checksum {record_fields.get('checksum')} != baseline {baseline['checksum']}")
        window = []
        if ok and "window_retired" in fields:
            # Spike's window, as the CPI model reads it, must be the shell's.
            try:
                reference = lockstep.parse_spike(log_text.splitlines(), ENTRY, symbols["tohost"],
                                                 end=lockstep.KernelEnd())
                window = cpi_model.window(reference)
                if len(window) != int(fields["window_retired"]):
                    problems.append(f"Spike's window holds {len(window)} instructions")
            except ValueError as error:
                problems.append(f"Spike's window: {error}")
        if args.cpi_check and window and "window_cycles" in fields:
            # The Aster core's measurement window takes exactly the model's cycles
            # (the window starts and ends mid-run, so no start or drain offset).
            expected = cpi_model.cycles(window, cpi_model.SEVEN_STAGE)
            if int(fields["window_cycles"]) != expected:
                problems.append(f"window cycles {fields['window_cycles']} != the CPI model's {expected}")
        passed = passed and counted and ok and not problems
        print(f"{'PASS' if passed else 'FAIL'}: kernel {name} {status.removeprefix('SHELL ')}; "
              f"lockstep: {message.splitlines()[0]}" + "".join(f"; {p}" for p in problems))
        if not passed:
            failures.append(name)
            if not ok:
                print(message)
            continue
        if not args.cpi_model:
            continue
        rows.append((name, int(fields["window_cycles"]), int(fields["window_retired"]),
                     cpi_model.cycles(window, cpi_model.SEVEN_STAGE), cpi_model.cycles(window, cpi_model.FIVE_STAGE),
                     len(window)))
    if rows and args.dut == "aster":
        # The Aster core against its own model: the measured CPI (and, with
        # --cpi-check, exact agreement); the speedups are against PicoRV32's runs.
        print(f"  {'kernel':18s} {'instructions':>12s} {'aster CPI':>13s} {'7-stage model':>13s}")
        for name, cycles, retired, seven, _, modelled in rows:
            print(f"  {name:18s} {retired:>12d} {cycles / retired:>13.3f} {seven / modelled:>13.3f}"
                  + ("" if name in GATE_KERNELS else "  (not in the gate)"))
        rows = []
    if rows:
        print(f"  {'kernel':18s} {'instructions':>12s} {args.dut + ' CPI':>13s} {'7-stage model':>13s} "
              f"{'speedup':>8s} {'5-stage model':>13s}")
        for name, cycles, retired, seven, five, modelled in rows:
            print(f"  {name:18s} {retired:>12d} {cycles / retired:>13.3f} {seven / modelled:>13.3f} "
                  f"{cycles / seven:>7.2f}x {five / modelled:>13.3f}" + ("" if name in GATE_KERNELS else "  (not in the gate)"))
        speedups = {name: cycles / seven for name, cycles, _, seven, _, _ in rows if name in GATE_KERNELS}
        if len(speedups) == len(GATE_KERNELS):
            geomean = math.prod(speedups.values()) ** (1 / len(speedups))
            lowest = min(speedups, key=speedups.get)
            print(f"  projected §7 gate (model, not a measurement): geometric mean {geomean:.3f}x, "
                  f"lowest {lowest} {speedups[lowest]:.3f}x")
    print(f"{'PASS' if not failures else 'FAIL'}: {args.dut} {len(names) - len(failures)}/{len(names)} "
          f"CPU kernels pass in lockstep with Spike and match the Phase 17 baseline")
    return 1 if failures else 0


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
    symbols: dict


# --interrupts: the instruction classes whose every pair (interrupted after,
# killed and re-executed after the handler) a run must produce.
INTERRUPT_CLASSES = ("alu", "load", "store", "branch", "jump", "mul", "div")
MRET = 0x30200073


def insn_class(record) -> str:
    if record.trap or record.insn is None:
        return "trap"
    opcode = record.insn & 0x7F
    if opcode == 0x33 and record.insn >> 25 == 1:
        return "mul" if (record.insn >> 12) & 7 < 4 else "div"
    return {0x03: "load", 0x23: "store", 0x63: "branch", 0x6F: "jump", 0x67: "jump", 0x73: "system",
            0x0F: "fence", 0x2F: "atomic"}.get(opcode, "alu")


def splice(records: list) -> tuple[list, list, str]:
    """The DUT's records without its interrupt handlers — each from its first
    record (marked intr, after a record that is not a trap) through the `mret`
    that returns from it — and, per interrupt, the classes of the record it
    followed and of the record after the handler; or an error."""
    main, pairs, index = [], [], 0
    while index < len(records):
        record = records[index]
        if record.intr and index > 0 and not records[index - 1].trap:
            end = next((j for j in range(index, len(records)) if records[j].insn == MRET), None)
            if end is None:
                return main, pairs, f"the interrupt handler at record {index} never returns"
            if end + 1 < len(records):
                pairs.append((insn_class(records[index - 1]), insn_class(records[end + 1])))
            index = end + 1
            continue
        main.append(record)
        index += 1
    return main, pairs, ""


def splice_check(trace_text: str, log_text: str, tohost: int) -> tuple[bool, str, list]:
    try:
        dut = lockstep.parse_trace(trace_text.splitlines())
        reference = lockstep.parse_spike(log_text.splitlines(), ENTRY, tohost)
    except ValueError as error:
        return False, f"rejected while parsing: {error}", []
    main, pairs, error = splice(dut)
    if error:
        return False, error, pairs
    if not pairs:
        return False, "no interrupt was taken", pairs
    ok, message = lockstep.compare(main, reference)
    return ok, f"{message} with {len(pairs)} interrupt handlers cut out", pairs


def run_program(args, config, source: Path, prefix: str, arch: bool = False) -> Outcome:
    """Build, run and check one program (the four conditions in the module docstring)."""
    shell_only = source.parent == INTERRUPTS
    spliced = args.interrupts is not None
    defines = ("ASTER_INTERRUPTS",) if spliced else ()
    if arch:
        defines += tuple(arch_case(source, config) or ())
    elf, binary, symbols = build(source, config["march"], args.build_dir, prefix, arch=arch, defines=defines)
    extra = ("+irq_device",) if shell_only else (f"+irq_random={args.interrupts}",) if spliced else ()
    passed, status, trace_text, log_text = execute(args, config, elf, binary, symbols, arch=arch,
                                                   shell_only=shell_only, shell_extra=extra)
    counted, count_message = retired_check(status, trace_text)
    if shell_only:
        # A self-checking interrupt program: it must also take at least the
        # interrupts it declares (shell_expect_interrupts).
        wanted = symbols.get("shell_expect_interrupts", 1)
        taken = next((int(item.split("=")[1]) for item in status.split() if item.startswith("interrupts=")), 0)
        ok = taken >= wanted
        message = f"self-checking, {taken} interrupts (at least {wanted} required)"
    elif spliced:
        if "mtvec_handler" in symbols:
            ok, message, pairs = False, "the program has its own trap handler, which --interrupts cannot use", []
        else:
            ok, message, pairs = splice_check(trace_text, log_text, symbols["tohost"])
        for pair in pairs:
            args.interrupt_pairs[pair] = args.interrupt_pairs.get(pair, 0) + 1
    else:
        ok, message = lockstep_check(trace_text, log_text, symbols["tohost"], config["word_loads"])
    signed, signature_message = (signature_check(elf) if has_signature(symbols) and not shell_only
                                 else (True, "no signature region" if not shell_only else "no Spike run"))
    summary = f"{status.removeprefix('SHELL ')}; lockstep: {message.splitlines()[0]}; {signature_message}"
    if not counted:
        summary += f"; {count_message}"
    # A directed test that defines shell_expect_fetch_errors must make a core
    # that fetches ahead fetch outside memory on its wrong path (the shell
    # counts those fetches); on a core that does not, it checks the program.
    faulted = True
    if "shell_expect_fetch_errors" in symbols and config["prefetches"]:
        faulted = "fetch_errors=0" not in status.split() and "fetch_errors=" in status
        if not faulted:
            summary += "; no fetch outside memory, which this test requires"
    fields = {key: int(value) for key, value in
              (item.split("=", 1) for item in status.split()[2:] if "=" in item) if value.isdigit()}
    timed = True
    if args.cpi_check and ok and not shell_only:
        records = lockstep.parse_spike(log_text.splitlines(), ENTRY, symbols["tohost"])
        expected = cpi_model.cycles(records, cpi_model.SEVEN_STAGE) + CPI_CHECK_OFFSET
        timed = fields.get("cycles") == expected
        summary += f"; {'cycles match' if timed else 'cycles differ from'} the CPI model ({expected})"
    return Outcome(passed and counted and ok and signed and faulted and timed, summary, message,
                   fields.get("cycles", 0), fields.get("retired", 0),
                   trace_text, log_text, symbols["tohost"], symbols)


def inject(args, config, prefix: str) -> int:
    test = test_path(args.only or "rv32ui/sh")
    run = run_program(args, config, test, prefix)
    tohost, trace_text, log_text = run.tohost, run.trace_text, run.log_text
    if not run.passed:
        print(f"FAIL: the uncorrupted run does not pass: {run.summary}")
        return 1
    trace_lines, log_lines = trace_text.splitlines(), log_text.splitlines()
    cases = {**trace_injections(trace_lines), **spike_injections(log_lines, tohost)}
    if any(line.split()[3:4] == ["1"] for line in trace_lines):     # traps and CSRs (18.3)
        cases.update({**trap_injections(trace_lines), **spike_trap_injections(log_lines)})
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


def interrupt_coverage(args) -> bool:
    """With --interrupts, report the interrupted/next class pairs; False if a required one is missing."""
    if args.interrupts is None:
        return True
    pairs = args.interrupt_pairs
    missing = [(a, b) for a in INTERRUPT_CLASSES for b in INTERRUPT_CLASSES if (a, b) not in pairs]
    total = sum(pairs.values())
    print(f"interrupt coverage: {total} interrupts; {len(INTERRUPT_CLASSES) ** 2 - len(missing)}/"
          f"{len(INTERRUPT_CLASSES) ** 2} (interrupted, next) class pairs"
          + (f"; missing {', '.join(f'{a}/{b}' for a, b in missing[:12])}" if missing else ""))
    return not (missing and args.require_coverage)


def run_random(args, config, prefix: str) -> int:
    out = args.build_dir / "random"
    out.mkdir(parents=True, exist_ok=True)
    # M if the DUT has it; with Zicsr, CSR instructions and traps in lockstep,
    # and under random interrupts only the CSR instructions an interrupt leaves
    # alone (the environment owns the trap vector and mscratch).
    extensions = "m" if "m" in config["march"][4:].split("_")[0] else ""
    if "zicsr" in config["march"]:
        extensions += ",zicsr" if args.interrupts is None else ",irqcsr"
    failures, total_cycles, total_retired, coverage = [], 0, 0, {}
    for seed in range(args.random_seed, args.random_seed + args.random):
        source = out / f"rvgen_{seed}.S"
        source.write_text(rvgen.Generator(seed, extensions).program(args.random_length))
        run = run_program(args, config, source, prefix)
        print(f"{'PASS' if run.passed else 'FAIL'}: random seed {seed} {run.summary}")
        if not run.passed:
            failures.append(seed)
            print(run.detail)
            continue
        total_cycles += run.cycles
        total_retired += run.retired
        for key, count in hazard_coverage(lockstep.parse_spike(run.log_text.splitlines(), ENTRY,
                                                               run.tohost)).items():
            coverage[key] = coverage.get(key, 0) + count
    wanted = required_bins(extensions)
    missing = [key for key in wanted if key not in coverage]
    print(f"coverage: {len(wanted) - len(missing)}/{len(wanted)} read-after-write bins "
          f"(producer x distance 1-4 x consumer operand)" +
          (f"; missing {', '.join(f'{p}/{d}/{c}' for p, d, c in missing[:12])}" if missing else ""))
    coverage_failed = bool(missing) and args.require_coverage
    coverage_failed = not interrupt_coverage(args) or coverage_failed
    mode = f", stall seed {args.stall_seed}" if args.stall_seed is not None else ""
    print(f"{'PASS' if not failures and not coverage_failed else 'FAIL'}: {args.dut} "
          f"{args.random - len(failures)}/{args.random} "
          f"random programs pass in lockstep with Spike (seeds {args.random_seed}-"
          f"{args.random_seed + args.random - 1}{mode}); {total_retired} instructions in {total_cycles} cycles"
          + ("; required coverage missed" if coverage_failed else ""))
    return 1 if failures or coverage_failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dut", choices=sorted(DUTS), required=True)
    parser.add_argument("--sim", type=Path, required=True)
    parser.add_argument("--spike", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/core_tests")
    parser.add_argument("--only", help="run one test, e.g. rv32ui/add (or I/add-01 with --arch)")
    parser.add_argument("--arch", action="store_true", help="run riscv-arch-test with signature comparison")
    parser.add_argument("--kernels", action="store_true", help="run the CPU kernels of docs/cpu.md §7")
    parser.add_argument("--random", type=int, metavar="N", help="run N constrained-random programs")
    parser.add_argument("--random-seed", type=int, default=1, help="seed of the first random program")
    parser.add_argument("--random-length", type=int, default=1500)
    parser.add_argument("--require-coverage", action="store_true",
                        help="fail a random run that misses a required hazard bin")
    parser.add_argument("--stall-seed", type=int, help="random memory back-pressure with this seed")
    parser.add_argument("--max-cycles", type=int, default=None,
                        help=f"shell cycle limit (default {MAX_CYCLES}; {KERNEL_MAX_CYCLES} with --kernels)")
    parser.add_argument("--aster-clock", type=Path, default=ASTER_CLOCK,
                        help="the Spike aster_clock plugin (for --kernels)")
    parser.add_argument("--cpi-check", action="store_true",
                        help="require each program's cycles to equal the seven-stage CPI model's (plus "
                             f"{CPI_CHECK_OFFSET}); only for the Aster core on a memory that answers on time")
    parser.add_argument("--cpi-model", action="store_true",
                        help="with --kernels, tabulate the CPI model of the Aster core pipeline against this "
                             "shell's DUT (the §7 baseline is PicoRV32 in the look-ahead shell)")
    parser.add_argument("--shell-arg", action="append", default=[], metavar="+PLUSARG",
                        help="extra plusarg for the shell (repeatable)")
    parser.add_argument("--expect-status", metavar="STATUS",
                        help="self-test: pass only if the shell reports this status (e.g. STORE_MISMATCH)")
    parser.add_argument("--inject", action="store_true",
                        help="prove the harness rejects a corrupted trace or reference log")
    parser.add_argument("--interrupts", type=int, metavar="SEED",
                        help="random interrupts (+irq_random=SEED), checked by cutting the handlers out")
    args = parser.parse_args()
    args.interrupt_pairs = {}
    if args.cpi_check and (args.dut != "aster" or args.stall_seed is not None or args.interrupts is not None):
        parser.error("--cpi-check is for the Aster core on a memory without back-pressure (no --stall-seed) "
                     "or interrupts")
    if args.interrupts is not None and not DUTS[args.dut]["interrupt_suites"]:
        parser.error(f"--interrupts needs a DUT that takes interrupts ({args.dut} does not)")
    if args.max_cycles is None:
        args.max_cycles = KERNEL_MAX_CYCLES if args.kernels else MAX_CYCLES
    config = DUTS[args.dut]
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")

    if args.inject:
        return inject(args, config, prefix)

    if args.expect_status:
        test = test_path(args.only or "rv32ui/sw")
        run = run_program(args, config, test, prefix)
        status = run.summary.split(";")[0]
        caught = status.split()[0] == args.expect_status
        print(f"{'PASS' if caught else 'FAIL'}: {test.parent.name}/{test.stem} with "
              f"{' '.join(args.shell_arg) or 'no shell arguments'}: shell reported {status!r}, "
              f"expected {args.expect_status}")
        return 0 if caught else 1

    if args.kernels:
        return run_kernels(args, config, prefix)

    if args.random is not None:
        if args.random < 1:
            parser.error("--random needs at least one program")
        return run_random(args, config, prefix)

    if args.arch:
        def arch_path(name: str) -> Path:
            suite, stem = name.split("/")
            return ARCH_TESTS / "rv32i_m" / suite / "src" / f"{stem}.S"
        suites = {suite: sorted((ARCH_TESTS / "rv32i_m" / suite / "src").glob("*.S"))
                  for suite in config["arch_suites"]}
        tests = [arch_path(args.only)] if args.only else [p for paths in suites.values() for p in paths]
    else:
        names = config["interrupt_suites"] if args.interrupts is not None else config["suites"]
        suites = {suite: sorted(suite_dir(suite).glob("*.S")) for suite in names}
        tests = [test_path(args.only)] if args.only else [p for paths in suites.values() for p in paths]
    empty = [suite for suite, paths in suites.items() if not paths]
    missing = [str(path) for path in tests if not path.is_file()]
    if (empty and not args.only) or missing:
        print(f"FAIL: {args.dut}: no programs found for {', '.join(empty + missing)}")
        return 1
    failures, skipped, total_cycles, total_retired = [], [], 0, 0
    for test in tests:
        name = f"{test.parent.parent.name if args.arch else test.parent.name}/{test.stem}"
        if name in config["skip"]:
            skipped.append(name)
            print(f"SKIP: {name} ({config['skip'][name]})")
            continue
        if args.arch and arch_case(test, config) is None:
            skipped.append(name)
            print(f"SKIP: {name} (no RVTEST_CASE applies to {config['arch_isa']} {config['arch_params']})")
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
    if ran == 0:
        print(f"FAIL: {args.dut}: no test ran ({len(skipped)} skipped)")
        return 1
    mode = f", stall seed {args.stall_seed}" if args.stall_seed is not None else ""
    mode += f", random interrupts seed {args.interrupts}" if args.interrupts is not None else ""
    what = "riscv-arch-test programs" if args.arch else "tests"
    what += " pass in lockstep and by signature"
    selfchecking = sum(1 for test in tests if test.parent == INTERRUPTS)
    if selfchecking:
        what += f" ({selfchecking} self-checking interrupt programs in the shell alone)"
    covered = interrupt_coverage(args)
    print(f"{'PASS' if not failures and covered else 'FAIL'}: {args.dut} {ran - len(failures)}/{ran} {what} "
          f"with Spike ({len(skipped)} skipped{mode}); {total_retired} instructions in {total_cycles} cycles"
          + ("" if covered else "; required interrupt coverage missed"))
    return 1 if failures or not covered else 0


if __name__ == "__main__":
    raise SystemExit(main())
