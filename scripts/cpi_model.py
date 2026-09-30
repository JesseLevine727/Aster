#!/usr/bin/env python3
"""Trace-driven CPI model of the Aster core pipeline (docs/cpu.md §4).

Reads a Spike commit log (the reference stream of a CPU kernel run in the core
shell), keeps the kernel's measurement window — from the store of 1 to a
window-control word to the store of 2, as the shells count it — and computes
the cycle at which each instruction enters Execute under the pipeline's rules:

- one instruction enters Execute per cycle, in order;
- an operand is ready for Execute one cycle after its producer entered
  Execute for ALU and link results, three cycles after for loads, AMOs, `lr`,
  `sc` and multiplies (forwarded from W), two after for Xasterdot8 (from M2);
- the iterative divider holds Execute for DIVIDE_CYCLES;
- `jal` and backward branches (predicted taken) redirect from Decode; a
  branch whose direction differs from the static prediction, and `jalr`,
  redirect from Execute; each redirect delays the next instruction by its
  penalty.

The five-stage rules of the 29 September specification are modelled alongside
for comparison. The model assumes the memory answers every access on time (the
one-cycle shell SRAM, no cache misses) and a fetch per cycle; it is an
estimate, not a simulation of the RTL.

    cpi_model.py build/core_tests/kernels/fft/fft.spike [...more logs]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lockstep  # noqa: E402

WINDOW_CONTROLS = (0x20003038, 0x20003080)
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

# Operands each opcode reads: rs1, rs2.
_READS = {0x33: (True, True), 0x13: (True, False), 0x03: (True, False), 0x23: (True, True),
          0x63: (True, True), 0x67: (True, False), 0x2F: (True, True), 0x0B: (True, True),
          0x73: (True, False)}


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >> (bits - 1) & 1 else value


def branch_offset(insn: int) -> int:
    imm = ((insn >> 31) & 1) << 12 | ((insn >> 7) & 1) << 11 | ((insn >> 25) & 0x3F) << 5 | ((insn >> 8) & 0xF) << 1
    return _signed(imm, 13)


def window(records: list[lockstep.Retired]) -> list[lockstep.Retired]:
    """The records after the window-opening store up to and including the closing one."""
    start = end = None
    for index, record in enumerate(records):
        if record.store is not None and record.mem and record.mem[0] & ~3 in WINDOW_CONTROLS:
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
    first = None
    for index, record in enumerate(records):
        insn = record.insn
        opcode = insn & 0x7F
        rs1, rs2 = (insn >> 15) & 31, (insn >> 20) & 31
        reads = _READS.get(opcode, (False, False))
        operand_ready = max([ready[r] for r, used in ((rs1, reads[0]), (rs2, reads[1])) if used and r] or [0])
        execute = max(earliest, operand_ready)
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
        if opcode == 0x6F:                                    # jal
            earliest += pipeline.decode_redirect
        elif opcode == 0x67:                                  # jalr
            earliest += pipeline.execute_redirect
        elif opcode == 0x63:                                  # conditional branch, backward predicted taken
            predicted = branch_offset(insn) < 0
            if taken and predicted:
                earliest += pipeline.decode_redirect
            elif taken != predicted:
                earliest += pipeline.execute_redirect
    return execute - (first or 0) + 1


def model(log: Path) -> dict:
    records = lockstep.parse_spike(log.read_text().splitlines(), 0x80000000, _tohost(log))
    inside = window(records)
    return {"log": str(log), "instructions": len(inside),
            **{p.name: cycles(inside, p) for p in (SEVEN_STAGE, FIVE_STAGE)}}


def _tohost(log: Path) -> int:
    """The tohost address, from the ELF next to the log."""
    import subprocess
    symbols = subprocess.run(["riscv32-unknown-elf-nm", str(log.with_suffix(".elf"))],
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
