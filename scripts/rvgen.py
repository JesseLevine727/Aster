#!/usr/bin/env python3
"""Seeded constrained-random RV32 programs for Phase 18 lockstep (docs/cpu.md §6.4).

Each program is an assembly file for the CPU-shell environment
(verification/core/env/riscv_test.h): it randomizes the register file and a
data region, runs a random instruction stream, and reports pass through
tohost. It checks nothing itself: correctness is the lockstep comparison with
Spike of every retired instruction, including every register value.

The stream is built to stress a pipeline, not to compute anything:
- sources are drawn mostly from recently written registers, so read-after-write
  distances of 1-4 (forwarding, load-use, multiply-use, and the register
  file's same-cycle write-through) are dense;
- loads and stores of every width stay inside the data region, naturally
  aligned, through a reserved base register or through an address that was
  just computed or loaded (address hazards);
- forward branches skip random blocks, bounded backward loops use a reserved
  counter register, and `jal`/`jalr` call and return from local leaf blocks;
- M operations draw operands from the corner values (0, 1, -1, INT_MIN,
  INT_MAX) as well as random ones;
- with A (`--ext m,a`, the Aster core from 18.4): every AMO on aligned words
  of the data region, through an address just computed or loaded; lr.w/sc.w
  pairs with work between them, an sc to another word, and an sc with no lr
  (whatever Spike's reservation does at its step boundaries, the runner hands
  the shell Spike's sc outcomes); pointers read from memory by an AMO or lr
  and used as load, store and AMO addresses, and AMO, lr and sc results used
  as data;
- with Zifencei (`--ext ...,zifencei`, the Aster core from 18.4): fence.i
  (no code is rewritten: the directed smc test does that) and fence in its
  forms;
- with Zicsr (`--ext m,zicsr`, the Aster core from 18.3): CSR instructions in
  every form — read-modify-writes of mtval, mcause, mepc and mie, mstatus.MPIE
  set and cleared, reads of minstret(h), misa and mhartid (never a value
  Spike legitimately differs in: mcycle, mip), their results used as data —
  and exceptions: ecall, ebreak, illegal words, misaligned loads and stores,
  data-port errors (0x4000_0000, outside memory) and misaligned jump and
  branch targets. The program's trap handler resumes after the trapping
  instruction and changes no register. mstatus.MIE is never set (a trap
  entry clears MPIE again), so no interrupt is enabled: the program runs in
  lockstep with Spike, whose CLINT holds MTIP high;
- under random interrupts (`--ext m,irqcsr`; scripts/run_core_tests.py
  --interrupts): only the CSR instructions an interrupt handler leaves alone
  and Spike agrees on — mie written with MEIE and MSIE in any combination
  (never MTIE), mcountinhibit, and reads of those, misa and mhartid — and no
  exceptions (the environment owns the trap vector).

    rvgen.py --seed 7 --length 2000 --ext m,zicsr -o prog.S
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

DATA_BYTES = 4096
BASE, COUNTER, LINK = 31, 30, 1      # data-region base, loop counter, return address
FREE = [r for r in range(1, 32) if r not in (BASE, COUNTER, LINK, 3)]   # x3 is TESTNUM (gp)
CORNERS = [0, 1, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 2, 0xFFFFFFFE]

ALU_R = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"]
ALU_I = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
SHIFT_I = ["slli", "srli", "srai"]
BRANCH = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]
LOADS = [("lb", 1), ("lh", 2), ("lw", 4), ("lbu", 1), ("lhu", 2)]
STORES = [("sb", 1), ("sh", 2), ("sw", 4)]
MUL = ["mul", "mulh", "mulhsu", "mulhu"]
DIV = ["div", "divu", "rem", "remu"]
CSR_WRITABLE = ["mtval", "mcause", "mepc", "mie"]          # mie: harmless while mstatus.MIE stays clear
CSR_READABLE = ["minstret", "minstreth", "misa", "mhartid", "mstatus", "mtval", "mcause", "mepc", "mie"]
IRQ_CSR_READABLE = ["mie", "mcountinhibit", "misa", "mhartid"]
AMOS = ["amoswap.w", "amoadd.w", "amoxor.w", "amoand.w", "amoor.w", "amomin.w", "amomax.w", "amominu.w", "amomaxu.w"]
ILLEGAL = [0x00000000, 0xFFFFFFFF, 0x00000001, 0x02051513, 0x7C002073]
FENCES = ["fence", "fence rw, rw", "fence r, w", "fence w, r", "fence.tso"]
TRAP_HANDLER = [
    "    .align 2",
    "    .global mtvec_handler",
    "mtvec_handler:",                    # resume after the trapping instruction, changing no register
    "    csrrw sp, mscratch, sp",
    "    sw t0, 0(sp)",
    "    csrr t0, mepc",
    "    addi t0, t0, 4",
    "    csrw mepc, t0",
    "    lw t0, 0(sp)",
    "    csrrw sp, mscratch, sp",
    "    mret",
]


class Generator:
    def __init__(self, seed: int, ext: str):
        self.rng = random.Random(seed)
        self.ext = ext
        self.recent: list[int] = []          # most recently written registers, newest last
        self.lines: list[str] = []
        self.label = 0
        self.leaves: list[str] = []          # leaf blocks called by jal/jalr, emitted after the stream
        self.pinned: set[int] = set()        # registers that must survive until a pending use

    def fresh(self) -> str:
        self.label += 1
        return f"L{self.label}"

    def src(self) -> int:
        """A source register: usually one written in the last few instructions."""
        if self.recent and self.rng.random() < 0.7:
            return self.rng.choice(self.recent[-3:])
        return self.rng.choice(FREE + [0])

    def dst(self, allow_x0: bool = True) -> int:
        choices = [r for r in FREE if r not in self.pinned]
        reg = self.rng.choice(choices) if self.rng.random() < 0.97 or not allow_x0 else 0
        self.recent = (self.recent + [reg])[-8:] if reg else self.recent
        return reg

    def emit(self, text: str) -> None:
        self.lines.append(f"    {text}")

    def imm12(self) -> int:
        return self.rng.choice([0, 1, -1, 2047, -2048, self.rng.randint(-2048, 2047)])

    # -- instruction classes --------------------------------------------------
    def alu(self) -> None:
        kind = self.rng.random()
        if kind < 0.45:
            op = self.rng.choice(ALU_R)
            s1, s2 = self.src(), self.src()
            self.emit(f"{op} x{self.dst()}, x{s1}, x{s2}")
        elif kind < 0.8:
            op = self.rng.choice(ALU_I)
            s1 = self.src()
            self.emit(f"{op} x{self.dst()}, x{s1}, {self.imm12()}")
        elif kind < 0.9:
            op = self.rng.choice(SHIFT_I)
            s1 = self.src()
            self.emit(f"{op} x{self.dst()}, x{s1}, {self.rng.randint(0, 31)}")
        elif kind < 0.95:
            self.emit(f"lui x{self.dst()}, 0x{self.rng.randint(0, 0xFFFFF):x}")
        else:
            self.emit(f"auipc x{self.dst()}, 0x{self.rng.randint(0, 0xFFFFF):x}")

    def memory(self) -> None:
        if self.rng.random() < 0.55:
            op, size = self.rng.choice(LOADS)
            offset = self.rng.randrange(0, DATA_BYTES, size)
            self.emit(f"{op} x{self.dst()}, {offset - 2048}(x{BASE})")
        else:
            op, size = self.rng.choice(STORES)
            offset = self.rng.randrange(0, DATA_BYTES, size)
            self.emit(f"{op} x{self.src()}, {offset - 2048}(x{BASE})")

    def fillers(self) -> None:
        """0-3 ALU instructions, so a pending use lands 1-4 instructions after its producer."""
        for _ in range(self.rng.randint(0, 3)):
            self.alu()

    def address_hazard(self) -> None:
        """A load or store whose base register was just computed (ALU) or loaded."""
        base = self.dst(allow_x0=False)
        self.pinned.add(base)
        if self.rng.random() < 0.6:
            delta = self.rng.randrange(-1024, 1024, 4)
            self.emit(f"addi x{base}, x{BASE}, {delta}")
        else:                                  # pointer chase: store the base, load it back
            delta, slot = 0, self.rng.randrange(-2048, 2048, 4)
            self.emit(f"sw x{BASE}, {slot}(x{BASE})")
            self.read_pointer(base, slot)
        self.fillers()
        if self.rng.random() < 0.55:
            op, size = self.rng.choice(LOADS)
            offset = self.rng.randrange(-1024, 1024, size)
            rd = base if self.rng.random() < 0.25 else self.dst()   # rd == rs1: the load replaces its base
            self.emit(f"{op} x{rd}, {offset}(x{base})")
        else:
            op, size = self.rng.choice(STORES)
            offset = self.rng.randrange(-1024, 1024, size)
            self.emit(f"{op} x{self.src()}, {offset}(x{base})")
        self.pinned.discard(base)

    def muldiv(self) -> None:
        if self.rng.random() < 0.1:        # the signed-overflow case: INT_MIN / -1
            dividend = self.dst(allow_x0=False)
            self.pinned.add(dividend)
            divisor = self.dst(allow_x0=False)
            self.pinned.discard(dividend)
            self.emit(f"li x{dividend}, 0x80000000")
            self.emit(f"li x{divisor}, -1")
            self.emit(f"{self.rng.choice(DIV)} x{self.dst()}, x{dividend}, x{divisor}")
            return
        if self.rng.random() < 0.3:        # load a corner operand first
            reg = self.dst()
            self.emit(f"li x{reg}, 0x{self.rng.choice(CORNERS):x}")
        op = self.rng.choice(MUL if self.rng.random() < 0.6 else DIV)
        s1, s2, rd = self.src(), self.src(), self.dst()
        self.emit(f"{op} x{rd}, x{s1}, x{s2}")
        if rd and self.rng.random() < 0.2:     # the result as data, 1-4 instructions later
            self.pinned.add(rd)
            self.fillers()
            self.use_as_data(rd)
            self.pinned.discard(rd)

    def atomic(self) -> None:
        """An AMO, an lr.w/sc.w pair, or a lone sc.w, on an aligned word of the data region."""
        if self.rng.random() < 0.3:            # on the base's word, right behind the last result
            s2 = self.src()
            self.emit(f"{self.rng.choice(AMOS)} x{self.dst()}, x{s2}, (x{BASE})")
            return
        base = self.dst(allow_x0=False)
        self.pinned.add(base)
        offset = self.rng.randrange(-2048, 2048, 4)
        if self.rng.random() < 0.7:
            self.emit(f"addi x{base}, x{BASE}, {offset}")
        else:                                  # the address comes from memory
            slot = self.rng.randrange(-2048, 2048, 4)
            self.emit(f"addi x{base}, x{BASE}, {offset}")
            self.emit(f"sw x{base}, {slot}(x{BASE})")
            self.read_pointer(base, slot)
        self.fillers()
        kind = self.rng.random()
        if kind < 0.55:
            s2, rd = self.src(), self.dst()
            self.emit(f"{self.rng.choice(AMOS)} x{rd}, x{s2}, (x{base})")
        elif kind < 0.9:
            self.emit(f"lr.w x{self.dst()}, (x{base})")
            self.fillers()
            if self.rng.random() < 0.2:        # to another word: it fails
                other = self.dst(allow_x0=False)
                self.emit(f"addi x{other}, x{base}, 4")
                base = other
            s2, rd = self.src(), self.dst()
            self.emit(f"sc.w x{rd}, x{s2}, (x{base})")
        else:                                  # no lr just before
            s2, rd = self.src(), self.dst()
            self.emit(f"sc.w x{rd}, x{s2}, (x{base})")
        self.pinned.discard(base)
        if rd and self.rng.random() < 0.35:    # the result as data, 1-4 instructions later
            self.pinned.add(rd)
            self.fillers()
            self.use_as_data(rd)
            self.pinned.discard(rd)

    def read_pointer(self, reg: int, slot: int) -> None:
        """Read the pointer stored at slot(BASE) into reg: a load, or (with A) an
        AMO that leaves it unchanged or an lr."""
        if "a" not in self.ext.split(",") or self.rng.random() < 0.5:
            self.emit(f"lw x{reg}, {slot}(x{BASE})")
            return
        self.emit(f"addi x{reg}, x{BASE}, {slot}")
        self.emit(self.rng.choice([f"amoor.w x{reg}, x0, (x{reg})", f"amoadd.w x{reg}, x0, (x{reg})",
                                   f"amoxor.w x{reg}, x0, (x{reg})", f"lr.w x{reg}, (x{reg})"]))

    def irq_csr(self) -> None:
        """Under random interrupts: mie (MEIE and MSIE only), mcountinhibit, reads."""
        kind = self.rng.random()
        if kind < 0.4:
            reg = self.dst(allow_x0=False)
            self.emit(f"li x{reg}, 0x{self.rng.choice([0x000, 0x008, 0x800, 0x808, 0x808]):x}")
            self.emit(f"csrrw x{self.dst()}, mie, x{reg}")
        elif kind < 0.6:
            op = self.rng.choice(["csrrwi", "csrrsi", "csrrci"])
            self.emit(f"{op} x{self.dst()}, mcountinhibit, {self.rng.randint(0, 31)}")
        else:
            reg = self.dst(allow_x0=False)
            self.pinned.add(reg)
            self.emit(f"csrr x{reg}, {self.rng.choice(IRQ_CSR_READABLE)}")
            self.fillers()
            self.use_as_data(reg)
            self.pinned.discard(reg)
        # Leave the interrupts enabled most of the time.
        if self.rng.random() < 0.7:
            reg = self.dst(allow_x0=False)
            self.emit(f"li x{reg}, 0x808")
            self.emit(f"csrw mie, x{reg}")

    def csr(self) -> None:
        kind = self.rng.random()
        if kind < 0.45:                        # a read-modify-write, register or immediate source
            csr = self.rng.choice(CSR_WRITABLE)
            if self.rng.random() < 0.6:
                s1 = self.src()
                self.emit(f"{self.rng.choice(['csrrw', 'csrrs', 'csrrc'])} x{self.dst()}, {csr}, x{s1}")
            else:
                op = self.rng.choice(["csrrwi", "csrrsi", "csrrci"])
                self.emit(f"{op} x{self.dst()}, {csr}, {self.rng.randint(0, 31)}")
        elif kind < 0.55:                      # mstatus.MPIE (bit 7) set or cleared; MIE never
            reg = self.dst(allow_x0=False)
            self.emit(f"li x{reg}, 0x80")
            self.emit(f"{self.rng.choice(['csrrs', 'csrrc'])} x{self.dst()}, mstatus, x{reg}")
        else:                                  # a read, its value used as data
            reg = self.dst(allow_x0=False)
            self.pinned.add(reg)
            self.emit(f"csrr x{reg}, {self.rng.choice(CSR_READABLE)}")
            self.fillers()
            self.use_as_data(reg)
            self.pinned.discard(reg)

    def trap(self) -> None:
        """An instruction that traps; the handler resumes after it."""
        kind = self.rng.random()
        if kind < 0.15:
            self.emit(self.rng.choice(["ecall", "ebreak"]))
        elif kind < 0.3:
            self.emit(f".word 0x{self.rng.choice(ILLEGAL):08x}")
        elif kind < 0.55:                      # misaligned: no access, no register written
            if self.rng.random() < 0.5:
                op, size = self.rng.choice([(op, size) for op, size in LOADS if size > 1])
                offset = self.rng.randrange(0, DATA_BYTES - 4, size) + self.rng.randrange(1, size)
                self.emit(f"{op} x{self.dst()}, {offset - 2048}(x{BASE})")
            else:
                op, size = self.rng.choice([(op, size) for op, size in STORES if size > 1])
                offset = self.rng.randrange(0, DATA_BYTES - 4, size) + self.rng.randrange(1, size)
                self.emit(f"{op} x{self.src()}, {offset - 2048}(x{BASE})")
        elif kind < 0.75:                      # a data-port error
            base = self.dst(allow_x0=False)
            self.pinned.add(base)
            self.emit(f"lui x{base}, 0x40000")
            self.fillers()
            if self.rng.random() < 0.5:
                op, size = self.rng.choice(LOADS)
                self.emit(f"{op} x{self.dst()}, {self.rng.randrange(0, 64, size)}(x{base})")
            else:
                op, size = self.rng.choice(STORES)
                self.emit(f"{op} x{self.src()}, {self.rng.randrange(0, 64, size)}(x{base})")
            self.pinned.discard(base)
        else:                                  # a misaligned jump or taken branch target
            after = self.fresh()
            style = self.rng.random()
            if style < 0.35:
                self.emit(f"jal x{self.dst()}, {after}+2")
            elif style < 0.7:
                self.emit(f"beq x0, x0, {after}+2")
            else:
                target = self.dst(allow_x0=False)
                self.emit(f"la x{target}, {after}")
                self.emit(f"jalr x{self.dst()}, 2(x{target})")
            self.lines.append(f"{after}:")

    def forward_branch(self, depth: int) -> None:
        target = self.fresh()
        if self.rng.random() < 0.1:            # a link value as a load address (reads code words)
            link = self.dst(allow_x0=False)
            self.pinned.add(link)
            self.emit(f"jal x{link}, {target}")
            self.lines.append(f"{target}:")
            self.fillers()
            op, size = self.rng.choice(LOADS)
            self.emit(f"{op} x{self.dst()}, {self.rng.randrange(0, 16, size)}(x{link})")
            self.pinned.discard(link)
            return
        if self.rng.random() < 0.3:            # jump and link forward: the link value is data
            link = self.dst(allow_x0=False)
            self.pinned.add(link)
            self.emit(f"jal x{link}, {target}")
            self.block(self.rng.randint(1, 6), depth + 1)
            self.lines.append(f"{target}:")
            self.fillers()
            self.use_as_data(link)
            self.pinned.discard(link)
            return
        s1, s2 = self.src(), self.src()
        self.emit(f"{self.rng.choice(BRANCH)} x{s1}, x{s2}, {target}")
        self.block(self.rng.randint(1, 6), depth + 1)
        self.lines.append(f"{target}:")

    def use_as_data(self, reg: int) -> None:
        """Consume `reg` as an ALU, branch, store-data, multiply/divide or CSR-source operand."""
        kinds = (["alu", "branch", "store"] + (["muldiv"] if "m" in self.ext else [])
                 + (["amo-data"] if "a" in self.ext.split(",") else [])
                 + (["csr"] if "zicsr" in self.ext else []) + (["csr-irq"] if "irqcsr" in self.ext else []))
        kind = self.rng.choice(kinds)
        if kind == "alu":
            self.emit(f"{self.rng.choice(ALU_R)} x{self.dst()}, x{reg}, x{self.src()}")
        elif kind == "branch":                 # both outcomes continue at the next instruction
            after = self.fresh()
            self.emit(f"{self.rng.choice(BRANCH)} x{reg}, x{self.src()}, {after}")
            self.lines.append(f"{after}:")
        elif kind == "store":
            op, size = self.rng.choice(STORES)
            self.emit(f"{op} x{reg}, {self.rng.randrange(-2048, 2048, size)}(x{BASE})")
        elif kind == "amo-data":               # on the base's own word, so it can follow at distance 1
            self.emit(f"{self.rng.choice(AMOS)} x{self.dst()}, x{reg}, (x{BASE})")
        elif kind == "csr":
            op = self.rng.choice(["csrrw", "csrrs", "csrrc"])
            self.emit(f"{op} x{self.dst()}, {self.rng.choice(CSR_WRITABLE)}, x{reg}")
        elif kind == "csr-irq":                # mcountinhibit takes any value's CY and IR bits
            self.emit(f"{self.rng.choice(['csrrs', 'csrrc'])} x{self.dst()}, mcountinhibit, x{reg}")
        else:
            self.emit(f"{self.rng.choice(MUL + DIV)} x{self.dst()}, x{reg}, x{self.src()}")

    def loop(self, depth: int) -> None:
        top = self.fresh()
        self.emit(f"li x{COUNTER}, {self.rng.randint(1, 4)}")
        self.lines.append(f"{top}:")
        self.block(self.rng.randint(2, 8), depth + 1, in_loop=True)
        self.emit(f"addi x{COUNTER}, x{COUNTER}, -1")
        # Equivalent loop-closing branches, so backward bne, blt and bltu all occur.
        self.emit(self.rng.choice([f"bnez x{COUNTER}, {top}", f"blt x0, x{COUNTER}, {top}",
                                   f"bltu x0, x{COUNTER}, {top}"]))

    def call(self) -> None:
        leaf = self.fresh()
        body = [f"{leaf}:"]
        saved, self.lines = self.lines, body
        for _ in range(self.rng.randint(0, 4)):    # 0: return right after the call (link -> jalr)
            self.alu()
        self.emit(f"jalr x0, 0(x{LINK})" if self.rng.random() < 0.5 else "ret")
        self.leaves.extend(self.lines)
        self.lines = saved
        style = self.rng.random()
        if style < 0.35:
            self.emit(f"jal x{LINK}, {leaf}")
            return
        # jalr computes (rs1 + imm) & ~1: vary the offset and set the low bit, so
        # both the addition and the bit clearing are exercised. With rs1 = x1 the
        # instruction also overwrites its own base (rd == rs1).
        target = LINK if self.rng.random() < 0.25 else self.dst(allow_x0=False)
        self.pinned.add(target)
        imm, low = self.rng.choice([0, 4, -4, 8]), self.rng.randint(0, 1)
        self.emit(f"la x{target}, {leaf}{low - imm:+d}")
        if style > 0.7 and target != LINK:     # the target comes from memory: load -> jalr
            slot = self.rng.randrange(-2048, 2048, 4)
            self.emit(f"sw x{target}, {slot}(x{BASE})")
            self.emit(f"lw x{target}, {slot}(x{BASE})")
        self.fillers()
        self.emit(f"jalr x{LINK}, {imm}(x{target})")
        self.pinned.discard(target)

    def block(self, length: int, depth: int = 0, in_loop: bool = False) -> None:
        for _ in range(length):
            if "zicsr" in self.ext:
                special = self.rng.random()
                if special < 0.06:
                    self.csr()
                    continue
                if special < 0.09:
                    self.trap()
                    continue
            elif "irqcsr" in self.ext and self.rng.random() < 0.05:
                self.irq_csr()
                continue
            if "a" in self.ext.split(",") and self.rng.random() < 0.06:
                self.atomic()
                continue
            if "zifencei" in self.ext.split(",") and self.rng.random() < 0.02:
                self.emit("fence.i" if self.rng.random() < 0.6 else self.rng.choice(FENCES))
                continue
            choice = self.rng.random()
            if choice < 0.45:
                self.alu()
            elif choice < 0.62:
                self.memory()
            elif choice < 0.70:
                self.address_hazard()
            elif choice < 0.82 and "m" in self.ext:
                self.muldiv()
            elif choice < 0.90 and depth < 2:
                self.forward_branch(depth)
            elif choice < 0.94 and depth < 1 and not in_loop:
                self.loop(depth)
            elif choice < 0.97 and depth < 2 and not in_loop:
                self.call()
            else:
                self.alu()

    def program(self, length: int) -> str:
        data = [self.rng.getrandbits(32) for _ in range(DATA_BYTES // 4)]
        init = [f"    li x{r}, 0x{self.rng.choice(CORNERS + [self.rng.getrandbits(32)] * 4):x}" for r in FREE]
        self.block(length)
        return "\n".join([
            "// Generated by scripts/rvgen.py; see its docstring.",
            '#include "riscv_test.h"',
            "RVTEST_RV32U",
            "RVTEST_CODE_BEGIN",
            f"    la x{BASE}, rvgen_data + 2048",
            *init,
            *self.lines,
            "    RVTEST_PASS",
            *self.leaves,
            *(TRAP_HANDLER if "zicsr" in self.ext else []),
            "RVTEST_CODE_END",
            "    .data",
            "RVTEST_DATA_BEGIN",
            "    .align 4",
            "rvgen_data:",
            *(f"    .word 0x{word:08x}" for word in data),
            "RVTEST_DATA_END",
            "",
        ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--length", type=int, default=2000, help="top-level instructions (blocks add more)")
    parser.add_argument("--ext", default="m", help="extensions beyond I to use, comma-separated: m, a, zicsr")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(Generator(args.seed, args.ext).program(args.length))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
