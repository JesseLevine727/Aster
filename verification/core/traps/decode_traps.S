// Directed trap test (18.3): exceptions known in Decode — illegal
// instructions of every kind, `ecall` and `ebreak` — each committed at the end
// of M1 with the instructions around it in flight: the store just older than
// the trap must reach memory, the one just younger must not (the shell fails a
// store whose record never retires), and a load-use stall, a multiply and a
// Decode redirect sit next to some of them. (The handler resumes after the
// trap, so the younger store executes then: what must not happen is its
// write reaching memory early, which the shell's store check would see as a
// write with no retired store.) In lockstep with Spike, which
// rejects the same encodings (machine mode only, no C, F, A or Zifencei yet).
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, data
    li s1, 0x11111111
    li s2, 0x22222222
    li s11, 0

    // An all-zero word, behind a store; a younger store must not happen.
    sw s1, 0(s0)
t0_insn:
    .word 0x00000000
    sw s2, 4(s0)                  // younger: killed by the trap, executed after the handler
    // All ones (a 32-bit length encoding past ILEN), right behind a load-use stall.
    lw a0, 0(s0)
    add a1, a0, a0
t1_insn:
    .word 0xffffffff
    // A 16-bit encoding (no C): mtval is its 16 bits.
t2_insn:
    .word 0x45130001
    // A reserved OP-IMM shift (slli with funct7 1), behind a multiply.
    mul a2, a1, a1
t3_insn:
    .word 0x02051513
    // CSRs: one that does not exist, a write to a read-only one, and a write
    // (nonzero source) to a user counter; a read of the same counter is legal.
t4_insn:
    csrr a3, 0x7c0
t5_insn:
    csrw mhartid, a0
t6_insn:
    csrrs a3, cycle, a0
    csrrs a3, cycle, x0
    // SYSTEM encodings this core rejects: sret, sfence.vma, uret's slot and a
    // reserved funct3. (fence.i comes in 18.4; Spike always executes it.)
t7_insn:
    .word 0x10200073
t8_insn:
    .word 0x12000073
t9_insn:
    .word 0x00200073
t10_insn:
    .word 0x00004073
    // ecall and ebreak, back to back, the ebreak right after a Decode redirect.
t11_insn:
    ecall
    j 1f
1:
t12_insn:
    ebreak
    sw s2, 8(s0)                  // older than the next trap: must happen

    li TESTNUM, 3
    EXPECT_TRAPS(13)
    EXPECT_TRAP(0, CAUSE_ILLEGAL_INSTRUCTION, t0_insn, 0)
    EXPECT_TRAP(1, CAUSE_ILLEGAL_INSTRUCTION, t1_insn, 0xffffffff)
    EXPECT_TRAP(2, CAUSE_ILLEGAL_INSTRUCTION, t2_insn, 0x0001)
    EXPECT_TRAP(3, CAUSE_ILLEGAL_INSTRUCTION, t3_insn, 0x02051513)
    EXPECT_TRAP(4, CAUSE_ILLEGAL_INSTRUCTION, t4_insn, 0x7c0026f3)
    EXPECT_TRAP(5, CAUSE_ILLEGAL_INSTRUCTION, t5_insn, 0xf1451073)
    EXPECT_TRAP(6, CAUSE_ILLEGAL_INSTRUCTION, t6_insn, 0xc00526f3)
    EXPECT_TRAP(7, CAUSE_ILLEGAL_INSTRUCTION, t7_insn, 0x10200073)
    EXPECT_TRAP(8, CAUSE_ILLEGAL_INSTRUCTION, t8_insn, 0x12000073)
    EXPECT_TRAP(9, CAUSE_ILLEGAL_INSTRUCTION, t9_insn, 0x00200073)
    EXPECT_TRAP(10, CAUSE_ILLEGAL_INSTRUCTION, t10_insn, 0x00004073)
    EXPECT_TRAP(11, CAUSE_MACHINE_ECALL, t11_insn, 0)
    EXPECT_TRAP_AT(12, CAUSE_BREAKPOINT, t12_insn, t12_insn, 0)
    li TESTNUM, 4
    lw t1, 4(s0)
    bne t1, s2, fail
    lw t1, 8(s0)
    bne t1, s2, fail
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0, 0, 0, 0
    TRAP_DATA
RVTEST_DATA_END
