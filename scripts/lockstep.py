#!/usr/bin/env python3
"""Compare a Phase 18 CPU-shell RVFI trace with Spike's commit log.

Both streams are normalized to one record per retired instruction:
PC, instruction word, destination-register write (x0 writes omitted), and the
memory access (byte address and size; store data). Spike's log starts in its
boot ROM; records before the program entry are skipped, and the stream ends
with Spike's first store to `tohost`. The DUT trace must equal that stream
exactly, record for record and in length, so it also ends with the tohost
store. The first difference is reported with the surrounding records.

The DUT trace is checked for internal consistency as it is parsed: RVFI order
numbers are consecutive, byte masks are contiguous and naturally aligned, a
byte address (when the DUT reports one) agrees with its mask, an x0
destination carries no value, and only an AMO both reads and writes memory,
with equal masks. Masks and data use riscv-formal's aligned layout: bit i of a
mask is byte i of the aligned 32-bit word containing the access.

    lockstep.py --trace dut.trace --spike-log spike.log --tohost 0x80001000 [--entry 0x80000000]
"""

from __future__ import annotations

import argparse
import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

_SPIKE = re.compile(r"core\s+\d+:\s+\d+\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)(.*)$")
_SPIKE_REG = re.compile(r"\bx(\d+)\s+0x([0-9a-f]+)")
_SPIKE_MEM = re.compile(r"\bmem\s+0x([0-9a-f]+)(?:\s+0x([0-9a-f]+))?")
_LOAD_SIZE = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2}   # funct3 of LB, LH, LW, LBU, LHU


@dataclass(frozen=True)
class Retired:
    pc: int
    insn: int
    rd: tuple[int, int] | None          # (register, value)
    mem: tuple[int, int] | None         # (byte address, size) for loads and stores
    store: int | None                   # store data, masked to the access size
    trap: bool = False

    def describe(self) -> str:
        parts = [f"pc=0x{self.pc:08x}", f"insn=0x{self.insn:08x}"]
        if self.trap:
            parts.append("TRAP")
        if self.rd:
            parts.append(f"x{self.rd[0]}=0x{self.rd[1]:08x}")
        if self.mem:
            parts.append(f"mem=0x{self.mem[0]:08x}/{self.mem[1]}")
        if self.store is not None:
            parts.append(f"store=0x{self.store:x}")
        return " ".join(parts)


def _lane(mask: int, addr: int, line: str) -> tuple[int, int]:
    """Byte lane and size of an RVFI mask, which must be one naturally aligned access."""
    lane = (mask & -mask).bit_length() - 1
    size = bin(mask).count("1")
    if size not in (1, 2, 4) or mask != ((1 << size) - 1) << lane or lane % size:
        raise ValueError(f"mask 0x{mask:x} is not one naturally aligned access: {line.strip()!r}")
    if addr & 3 and addr & 3 != lane:
        raise ValueError(f"address 0x{addr:08x} disagrees with mask 0x{mask:x}: {line.strip()!r}")
    return lane, size


def parse_trace(lines) -> list[Retired]:
    """One RVFI record per line: order pc insn trap rd rd_wdata addr rmask wmask rdata wdata."""
    records, first_order = [], None
    for line in lines:
        if not line.strip():
            continue
        f = line.split()
        if len(f) != 11:
            raise ValueError(f"malformed trace line: {line.strip()!r}")
        order = int(f[0])
        first_order = order if first_order is None else first_order
        if order != first_order + len(records):
            raise ValueError(f"RVFI order {order} after {len(records)} records from {first_order}: "
                             "a record was dropped or duplicated")
        pc, insn, trap, rd, rd_wdata = int(f[1], 16), int(f[2], 16), f[3] != "0", int(f[4]), int(f[5], 16)
        addr, rmask, wmask, wdata = int(f[6], 16), int(f[7], 16), int(f[8], 16), int(f[10], 16)
        if rd == 0 and rd_wdata:
            raise ValueError(f"rd_wdata must be 0 when rd is x0: {line.strip()!r}")
        mem = store = None
        if rmask:
            _lane(rmask, addr, line)
        if rmask and wmask and (insn & 0x7F != 0x2F or rmask != wmask):
            raise ValueError(f"a record both reads and writes memory, which only an AMO does "
                             f"(with equal masks): {line.strip()!r}")
        if wmask or rmask:   # an AMO reports both masks; its store side is compared
            lane, size = _lane(wmask or rmask, addr, line)
            mem = ((addr & ~3) + lane, size)
            if wmask:
                store = (wdata >> (8 * lane)) & ((1 << (8 * size)) - 1)
        records.append(Retired(pc, insn, (rd, rd_wdata) if rd else None, mem, store, trap))
    return records


CONSOLE = 0x20000000
WINDOW_CONTROLS = (0x20003038, 0x20003080)


class KernelEnd:
    """The end of a CPU-kernel run, as the shells' observer defines it
    (verification/core/shell_common.h): the console store of the newline that
    ends the first line beginning with 'A' (the AsterBench record) after the
    measurement window closed (a store of 2 to a window-control word)."""

    def __init__(self):
        self.window_open = self.window_closed = False
        self.line_start, self.record_line = True, False

    def __call__(self, record: Retired) -> bool:
        if record.store is None or record.trap:
            return False
        word = record.mem[0] & ~3
        if word in WINDOW_CONTROLS and record.mem[1] == 4:
            if record.store == 1 and not self.window_open:
                self.window_open = True
            elif record.store == 2 and self.window_open:
                self.window_closed = True
        if word == CONSOLE and record.mem[0] == CONSOLE:
            character = chr(record.store & 0xFF)
            if self.line_start:
                self.record_line = self.window_closed and character == "A"
            self.line_start = character == "\n"
            return self.record_line and character == "\n"
        return False


def parse_spike(lines, entry: int, tohost: int, end=None) -> list[Retired]:
    """Spike's commits from `entry` up to and including its first store to
    `tohost`, or the record for which `end(record)` holds (KernelEnd)."""
    records, started = [], False
    for line in lines:
        match = _SPIKE.match(line.strip())
        if not match:
            continue
        pc, insn, rest = int(match.group(1), 16), int(match.group(2), 16), match.group(3)
        started = started or pc == entry
        if not started:
            continue
        rd = mem = store = None
        reg = _SPIKE_REG.search(rest)
        if reg and int(reg.group(1)) != 0:
            rd = (int(reg.group(1)), int(reg.group(2), 16))
        accesses = list(_SPIKE_MEM.finditer(rest))
        stores = [a for a in accesses if a.group(2) is not None]
        if stores:                           # store (an AMO also prints its load first)
            store = int(stores[-1].group(2), 16)
            mem = (int(stores[-1].group(1), 16), len(stores[-1].group(2)) // 2)
        elif accesses:                       # load: width from the instruction's funct3
            mem = (int(accesses[0].group(1), 16), _LOAD_SIZE.get((insn >> 12) & 7, 4))
        records.append(Retired(pc, insn, rd, mem, store))
        if store is not None and mem[0] & ~3 == tohost & ~3:
            return records
        if end is not None and end(records[-1]):
            return records
    if not started:
        raise ValueError(f"Spike never reached the entry 0x{entry:08x}")
    raise ValueError(f"Spike's log has no store to tohost 0x{tohost:08x}"
                     + (" and no kernel end" if end is not None else ""))


def word_granular_loads(records: list[Retired]) -> list[Retired]:
    """Compare loads by word address only.

    For a DUT whose RVFI read mask covers the whole word on sub-word loads
    (PicoRV32), the byte offset is not in the trace. It is still implied: the
    instruction word and every earlier register write are compared, so
    rs1 + imm is identical, and the loaded value is checked through rd.
    """
    return [dataclasses.replace(r, mem=(r.mem[0] & ~3, 0)) if r.mem and r.store is None else r
            for r in records]


def compare(dut: list[Retired], spike: list[Retired], context: int = 3,
            word_loads: bool = False) -> tuple[bool, str]:
    """`spike` is the reference stream ending with its tohost store (parse_spike)."""
    if word_loads:
        dut, spike = word_granular_loads(dut), word_granular_loads(spike)
    if not dut:
        return False, "DUT trace is empty"
    for index, record in enumerate(dut):
        if index >= len(spike):
            return False, (f"DUT retired record {index} after Spike's tohost store "
                           f"(Spike retired {len(spike)}): {record.describe()}")
        reference = spike[index]
        if record != reference:
            fields = [name for name in ("pc", "insn", "trap", "rd", "mem", "store")
                      if getattr(record, name) != getattr(reference, name)]
            window = range(max(0, index - context), index)
            lines = [f"mismatch at retired instruction {index} in {', '.join(fields)}",
                     f"  dut  : {record.describe()}", f"  spike: {reference.describe()}",
                     "  preceding (matching) records:"]
            lines += [f"    {i}: {dut[i].describe()}" for i in window]
            return False, "\n".join(lines)
    if len(dut) < len(spike):
        return False, (f"DUT trace ended after {len(dut)} records; Spike retired {len(spike)} "
                       f"up to its tohost store (next: {spike[len(dut)].describe()})")
    return True, f"{len(dut)} retired instructions match Spike"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--spike-log", type=Path, required=True)
    parser.add_argument("--tohost", type=lambda text: int(text, 0), required=True)
    parser.add_argument("--entry", type=lambda text: int(text, 0), default=0x80000000)
    parser.add_argument("--word-loads", action="store_true",
                        help="compare loads by word address (DUT reports full-word read masks)")
    args = parser.parse_args()
    try:
        dut = parse_trace(args.trace.read_text().splitlines())
        spike = parse_spike(args.spike_log.read_text().splitlines(), args.entry, args.tohost)
    except ValueError as error:
        print(f"FAIL: {error}")
        return 1
    ok, message = compare(dut, spike, word_loads=args.word_loads)
    print(("PASS: " if ok else "FAIL: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
