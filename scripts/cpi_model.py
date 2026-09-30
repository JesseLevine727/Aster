#!/usr/bin/env python3
"""Trace-driven CPI model of the Aster core pipeline (docs/cpu.md §4).

Reads a Spike commit log (the reference stream of a CPU kernel run in the core
shell), keeps the kernel's measurement window — from the store of 1 to a
window-control word to the store of 2, as the shells count it — and computes
the cycles at which each instruction occupies Decode and enters Execute under
the pipeline's rules:

- instructions occupy Decode and enter Execute one at a time, in order; an
  instruction may occupy Decode from the cycle its predecessor enters
  Execute, once the fetch unit has it: sequential instructions stream one per
  cycle (the three-entry buffer covers Decode's stalls, so none is late);
- an operand is ready for Execute one cycle after its producer entered
  Execute for ALU and link results, three cycles after for loads, AMOs, `lr`,
  `sc` and multiplies (forwarded from W), two after for Xasterdot8 (from M2);
- the iterative divider holds Execute for DIVIDE_CYCLES;
- `jal` and backward branches (predicted taken) redirect from Decode in their
  first cycle there, whether or not they then wait for operands; a branch
  whose direction differs from the static prediction, and `jalr`, redirect
  from Execute as they leave it. The redirect's target reaches Decode
  `decode_redirect + 1` cycles after the Decode cycle, or `execute_redirect`
  cycles after the Execute cycle.

For the seven-stage pipeline these are the rules the 18.1 RTL implements
(rtl/aster_core), and on a memory that answers on time the RTL's cycle count
equals the model's plus a fixed start and drain (checked for every program by
scripts/run_core_tests.py --cpi-check).

The five-stage rules of the 29 September specification are modelled alongside
for comparison. The model assumes the memory answers every access on time (the
one-cycle shell SRAM, no cache misses) and a fetch per cycle; it is an
estimate, not a simulation of the RTL.

    cpi_model.py build/core_tests/kernels/kernels/fft/fft.spike [...more logs]

scripts/run_core_tests.py --kernels runs it over every kernel it checks.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lockstep  # noqa: E402

DIVIDE_CYCLES = 33


@dataclass(frozen=True)
class Pipeline:
    name: str
    load_ready: int        # cycles after the producer enters Execute until a consumer may
    mul_ready: int
    dot8_ready: int
    decode_redirect: int   # penalty in cycles
    execute_redirect: int


SEVEN_STAGE = Pipeline("seven-stage (approved 30 Sep)", load_ready=3, mul_ready=3, dot8_ready=2,
                       decode_redirect=2, execute_redirect=4)
FIVE_STAGE = Pipeline("five-stage (29 Sep)", load_ready=2, mul_ready=3, dot8_ready=2,
                      decode_redirect=1, execute_redirect=2)

# Operands each opcode reads: rs1, rs2 (SYSTEM: see _reads).
_READS = {0x33: (True, True), 0x13: (True, False), 0x03: (True, False), 0x23: (True, True),
          0x63: (True, True), 0x67: (True, False), 0x2F: (True, True), 0x0B: (True, True)}


def _reads(insn: int) -> tuple[bool, bool]:
    opcode = insn & 0x7F
    if opcode == 0x73:              # csrrw/csrrs/csrrc read rs1; the csrr*i forms hold an immediate there
        return (insn >> 12) & 7 in (1, 2, 3), False
    return _READS.get(opcode, (False, False))


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >> (bits - 1) & 1 else value


def branch_offset(insn: int) -> int:
    imm = ((insn >> 31) & 1) << 12 | ((insn >> 7) & 1) << 11 | ((insn >> 25) & 0x3F) << 5 | ((insn >> 8) & 0xF) << 1
    return _signed(imm, 13)


def window(records: list[lockstep.Retired]) -> list[lockstep.Retired]:
    """The records after the window-opening store up to and including the closing one
    (word stores to a window-control word, as the shells count them)."""
    start = end = None
    for index, record in enumerate(records):
        if (record.store is not None and not record.trap and record.mem and record.mem[1] == 4
                and record.mem[0] & ~3 in lockstep.WINDOW_CONTROLS):
            if record.store == 1 and start is None:
                start = index + 1
            elif record.store == 2 and start is not None:
                end = index + 1
                break
    if start is None or end is None:
        raise ValueError("no measurement window (stores of 1 and 2 to a window-control word)")
    return records[start:end]


def cycles(records: list[lockstep.Retired], pipeline: Pipeline) -> int:
    """Cycles from the first instruction's entry into Execute to the last one's, plus one."""
    ready = [0] * 32                  # cycle from which each register can be consumed in Execute
    execute = 0                       # cycle at which the current instruction enters Execute
    earliest = 0                      # earliest cycle the next instruction may enter Execute
    available = 0                     # cycle from which the fetch unit can give the next one to Decode
    decode_free = 0                   # cycle from which Decode is free (the previous one entered Execute)
    first = None
    for index, record in enumerate(records):
        insn = record.insn
        opcode = insn & 0x7F
        rs1, rs2 = (insn >> 15) & 31, (insn >> 20) & 31
        reads = _reads(insn)
        operand_ready = max([ready[r] for r, used in ((rs1, reads[0]), (rs2, reads[1])) if used and r] or [0])
        decode = max(available, decode_free)
        execute = max(decode + 1, earliest, operand_ready)
        decode_free = execute
        first = execute if first is None else first
        muldiv = opcode == 0x33 and insn >> 25 == 1
        divide = muldiv and (insn >> 12) & 7 >= 4
        occupancy = DIVIDE_CYCLES if divide else 1
        if record.rd:
            if opcode in (0x03, 0x2F):
                latency = pipeline.load_ready
            elif muldiv and not divide:
                latency = pipeline.mul_ready
            elif opcode == 0x0B:
                latency = pipeline.dot8_ready
            else:
                latency = occupancy
            ready[record.rd[0]] = execute + latency
        earliest = execute + occupancy
        following = records[index + 1] if index + 1 < len(records) else None
        taken = following is not None and following.pc != record.pc + 4
        from_decode = decode + 1 + pipeline.decode_redirect
        from_execute = execute + occupancy - 1 + pipeline.execute_redirect
        if opcode == 0x6F:                                    # jal
            available = from_decode
        elif opcode == 0x67:                                  # jalr
            available = from_execute
        elif opcode == 0x63 and taken != (branch_offset(insn) < 0):   # mispredicted (backward predicted taken)
            available = from_execute
        elif opcode == 0x63 and taken:                        # backward, taken as predicted
            available = from_decode
        else:
            available += 1
    return execute - (first or 0) + 1


def model(log: Path) -> dict:
    records = lockstep.parse_spike(log.read_text().splitlines(), 0x80000000, _tohost(log),
                                   end=lockstep.KernelEnd())
    inside = window(records)
    return {"log": str(log), "instructions": len(inside),
            **{p.name: cycles(inside, p) for p in (SEVEN_STAGE, FIVE_STAGE)}}


def _tohost(log: Path) -> int:
    """The tohost address, from the ELF next to the log."""
    import os
    import subprocess
    nm = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-") + "nm"
    symbols = subprocess.run([nm, str(log.with_suffix(".elf"))],
                             capture_output=True, text=True, check=True).stdout
    return next(int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(" tohost"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", type=Path, nargs="+")
    args = parser.parse_args()
    for log in args.logs:
        result = model(log)
        n = result["instructions"]
        print(f"{log.stem:12s} {n:>9d} instructions  " + "  ".join(
            f"{p.name.split()[0]} CPI {result[p.name] / n:.3f}" for p in (SEVEN_STAGE, FIVE_STAGE)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
