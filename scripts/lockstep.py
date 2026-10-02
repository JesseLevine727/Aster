#!/usr/bin/env python3
"""Compare a Phase 18 CPU-shell RVFI trace with Spike's commit log.

Both streams are normalized to one record per retired instruction:
PC, instruction word, destination-register write (x0 writes omitted), the
memory access (byte address and size; store data), and the CSRs written with
their values. Spike's log starts in its boot ROM; records before the program
entry are skipped, and the stream ends with Spike's first store to `tohost`.
The DUT trace must equal that stream exactly, record for record and in
length, so it also ends with the tohost store. The first difference is
reported with the surrounding records.

Traps (docs/cpu.md §6): `--log-commits` writes no record for a trapping
instruction, so Spike is also run with `-l`, whose `exception <cause>, epc
<pc>` line (and the `tval` line after it) becomes a trap record: its PC, the
instruction `-l` printed at that PC (none for a fetch fault), and mepc, mcause
and mtval as the CSRs written. A DUT trap record (trap field 1) is compared on
the same: its other CSR writes (mstatus) are not in Spike's log.

Values that legitimately differ are left out by name (docs/cpu.md §6): the
value a CSR instruction reads from mcycle(h), cycle(h), time(h), marchid or
mip (Spike's mip has MTIP set by its CLINT; the shell has no timer), the value
written to mip, and Spike's debug-trigger tcontrol write.

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
_SPIKE_REG = re.compile(r"(?:^|\s)x(\d+)\s+0x([0-9a-f]+)")
_SPIKE_MEM = re.compile(r"\bmem\s+0x([0-9a-f]+)(?:\s+0x([0-9a-f]+))?")
_SPIKE_CSR = re.compile(r"(?:^|\s)c(\d+)_(\w+)\s+0x([0-9a-f]+)")
_SPIKE_FETCH = re.compile(r"core\s+\d+:\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)")      # -l: an instruction about to execute
_SPIKE_TRAP = re.compile(r"core\s+\d+:\s+exception\s+(.+?),\s+epc\s+0x([0-9a-f]+)")   # "interrupt #7" too
_SPIKE_INTERRUPT = re.compile(r"interrupt\s+#(\d+)$")
_SPIKE_TVAL = re.compile(r"core\s+\d+:\s+tval\s+0x([0-9a-f]+)")
_DUT_CSR = re.compile(r"c([0-9a-f]+)=([0-9a-f]+)$")
_LOAD_SIZE = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2}   # funct3 of LB, LH, LW, LBU, LHU

MEPC, MCAUSE, MTVAL, MIP = 0x341, 0x342, 0x343, 0x344
TRAP_CSRS = (MEPC, MCAUSE, MTVAL)               # what Spike's log tells of a trap
# Spike's exception names (riscv/trap.h) and their mcause values.
TRAP_CAUSES = {"trap_instruction_address_misaligned": 0, "trap_instruction_access_fault": 1,
               "trap_illegal_instruction": 2, "trap_breakpoint": 3, "trap_load_address_misaligned": 4,
               "trap_load_access_fault": 5, "trap_store_address_misaligned": 6, "trap_store_access_fault": 7,
               "trap_user_ecall": 8, "trap_supervisor_ecall": 9, "trap_machine_ecall": 11,
               "trap_instruction_page_fault": 12, "trap_load_page_fault": 13, "trap_store_page_fault": 15}
# CSRs whose value a read may legitimately differ in (docs/cpu.md §6).
READ_ALLOWLIST = {0xB00, 0xB80, 0xC00, 0xC80, 0xC01, 0xC81, 0xF12, MIP}
WRITE_VALUE_ALLOWLIST = {MIP}
# Spike's writes left out by name: its debug-trigger tcontrol update on mret
# (absent with --triggers=0), and writes to the hardware performance monitor's
# event selectors, which the core hardwires to zero and reports no write to
# (their reads still compare; Spike logs no write to its counters either).
SPIKE_CSR_IGNORED = re.compile(r"tcontrol|mhpmevent\d+")


@dataclass(frozen=True)
class Retired:
    pc: int
    insn: int | None                    # None: a fetch fault's or an interrupt's trap record
    rd: tuple[int, int | None] | None   # (register, value); value None when allowlisted
    mem: tuple[int, int] | None         # (byte address, size) for loads and stores
    store: int | None                   # store data, masked to the access size
    trap: bool = False
    csrs: tuple[tuple[int, int | None], ...] = ()     # (CSR, value written), sorted
    intr: bool = dataclasses.field(default=False, compare=False)   # DUT only: a handler's first record

    def describe(self) -> str:
        parts = [f"pc=0x{self.pc:08x}", "insn=none" if self.insn is None else f"insn=0x{self.insn:08x}"]
        if self.trap:
            parts.append("TRAP")
        if self.rd:
            parts.append(f"x{self.rd[0]}=" + ("(allowlisted)" if self.rd[1] is None else f"0x{self.rd[1]:08x}"))
        if self.mem:
            parts.append(f"mem=0x{self.mem[0]:08x}/{self.mem[1]}")
        if self.store is not None:
            parts.append(f"store=0x{self.store:x}")
        parts += [f"csr{csr:03x}=" + ("(allowlisted)" if value is None else f"0x{value:08x}") for csr, value in self.csrs]
        return " ".join(parts)


def csr_read(insn: int | None) -> int | None:
    """The CSR a Zicsr instruction names, or None."""
    if insn is None or insn & 0x7F != 0x73 or (insn >> 12) & 7 in (0, 4):
        return None
    return insn >> 20


def normalized(record: Retired) -> Retired:
    """Leave out the allowlisted values (docs/cpu.md §6)."""
    rd = record.rd
    if rd and csr_read(record.insn) in READ_ALLOWLIST:
        rd = (rd[0], None)
    csrs = tuple((csr, None if csr in WRITE_VALUE_ALLOWLIST else value) for csr, value in record.csrs)
    return dataclasses.replace(record, rd=rd, csrs=csrs)


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
    """One RVFI record per line: order pc insn trap rd rd_wdata addr rmask wmask rdata wdata,
    then c<csr>=<value> per CSR written and `intr` for a handler's first record."""
    records, first_order = [], None
    for line in lines:
        if not line.strip():
            continue
        f = line.split()
        if len(f) < 11:
            raise ValueError(f"malformed trace line: {line.strip()!r}")
        csrs, intr = {}, False
        for token in f[11:]:
            match = _DUT_CSR.match(token)
            if token == "intr" and not intr:
                intr = True
            elif match and int(match.group(1), 16) not in csrs:
                csrs[int(match.group(1), 16)] = int(match.group(2), 16)
            else:
                raise ValueError(f"malformed trace token {token!r}: {line.strip()!r}")
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
        if trap:
            if mem or rd or set(TRAP_CSRS) - set(csrs):
                raise ValueError(f"a trap record must write no register or memory, and must write mepc, mcause "
                                 f"and mtval: {line.strip()!r}")
            csrs = {csr: csrs[csr] for csr in TRAP_CSRS}      # Spike's log tells nothing more of a trap
            if csrs[MCAUSE] == 1:
                insn = None                                   # a fetch fault fetched no instruction
        records.append(Retired(pc, insn, (rd, rd_wdata) if rd else None, mem, store, trap,
                               tuple(sorted(csrs.items())), intr))
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
    `tohost`, or the record for which `end(record)` holds (KernelEnd); with
    `-l`, its traps as trap records."""
    records, started = [], False
    fetched = None                      # (pc, insn) of the last instruction -l printed
    for line in lines:
        text = line.strip()
        match = _SPIKE.match(text)
        if not match:
            fetch = _SPIKE_FETCH.match(text)
            trap = _SPIKE_TRAP.match(text)
            tval = _SPIKE_TVAL.match(text)
            if fetch:
                fetched = (int(fetch.group(1), 16), int(fetch.group(2), 16) & 0xFFFFFFFF)
            elif trap:
                epc = int(trap.group(2), 16)
                started = started or epc == entry
                if not started:
                    continue
                interrupt = _SPIKE_INTERRUPT.match(trap.group(1))
                if interrupt:
                    cause, insn = (1 << 31) | int(interrupt.group(1)), None
                elif trap.group(1) in TRAP_CAUSES:
                    cause = TRAP_CAUSES[trap.group(1)]
                    insn = fetched[1] if fetched and fetched[0] == epc and cause != 1 else None
                else:
                    raise ValueError(f"unknown exception in Spike's log: {text!r}")
                records.append(Retired(epc, insn, None, None, None, True,
                                       ((MEPC, epc), (MCAUSE, cause), (MTVAL, 0))))
            elif tval and records and records[-1].trap and dict(records[-1].csrs)[MTVAL] == 0:
                csrs = dict(records[-1].csrs)
                csrs[MTVAL] = int(tval.group(1), 16)
                records[-1] = dataclasses.replace(records[-1], csrs=tuple(sorted(csrs.items())))
            continue
        pc, insn, rest = int(match.group(1), 16), int(match.group(2), 16), match.group(3)
        started = started or pc == entry
        if not started:
            continue
        rd = mem = store = None
        reg = _SPIKE_REG.search(rest)
        if reg and int(reg.group(1)) != 0:
            rd = (int(reg.group(1)), int(reg.group(2), 16))
        csrs = tuple(sorted((int(c.group(1)), int(c.group(3), 16)) for c in _SPIKE_CSR.finditer(rest)
                            if not SPIKE_CSR_IGNORED.fullmatch(c.group(2))))
        accesses = list(_SPIKE_MEM.finditer(rest))
        stores = [a for a in accesses if a.group(2) is not None]
        if stores:                           # store (an AMO also prints its load first)
            store = int(stores[-1].group(2), 16)
            mem = (int(stores[-1].group(1), 16), len(stores[-1].group(2)) // 2)
        elif accesses:                       # load: width from the instruction's funct3
            mem = (int(accesses[0].group(1), 16), _LOAD_SIZE.get((insn >> 12) & 7, 4))
        records.append(Retired(pc, insn, rd, mem, store, csrs=csrs))
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
    # rvfi_intr marks a trap handler's first record: with no interrupt in the
    # stream (lockstep, or interrupt handlers cut out), exactly the records after
    # a trap record.
    for index, record in enumerate(dut):
        if record.intr != (index > 0 and dut[index - 1].trap):
            return False, (f"record {index} is {'' if record.intr else 'not '}marked as a trap handler's first "
                           f"(rvfi_intr) {'without' if record.intr else 'after'} a trap record: {record.describe()}")
    dut, spike = [normalized(r) for r in dut], [normalized(r) for r in spike]
    if not dut:
        return False, "DUT trace is empty"
    for index, record in enumerate(dut):
        if index >= len(spike):
            return False, (f"DUT retired record {index} after Spike's tohost store "
                           f"(Spike retired {len(spike)}): {record.describe()}")
        reference = spike[index]
        if record != reference:
            fields = [name for name in ("pc", "insn", "trap", "rd", "mem", "store", "csrs")
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
