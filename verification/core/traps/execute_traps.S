// Directed trap test (18.3): exceptions known in Execute — misaligned loads
// and stores (mtval: the address; none reaches the data port) and misaligned
// jump and branch targets (mtval: the target; the jump writes no link) —
// with the address forwarded from an ALU result and from a load (load-use),
// and the branches in both prediction directions: a forward branch (predicted
// not taken, redirected from Execute) and a backward one (Decode predicts a
// backward branch only when its target is aligned, so this one is resolved in
// Execute too); a trapping branch's own redirect is replaced by the trap's.
// Untaken branches and a `jalr` target with only bit 0 set do not trap.
// lr.w, sc.w and an AMO on a misaligned address trap too (18.4).
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, data
    li s11, 0

    // Misaligned accesses: the base forwarded from an ALU result ...
    addi a0, s0, 0
t0_insn:
    lh a1, 1(a0)
t1_insn:
    lhu a1, 3(a0)
t2_insn:
    lw a1, 2(a0)
    // ... and from a load (the address waits for it).
    lw a0, 16(s0)                 // data + 0 (the word holds the address)
t3_insn:
    lw a1, 1(a0)
t4_insn:
    sh a1, 3(a0)
t5_insn:
    sw a1, 2(a0)
    sw s0, 8(s0)                  // aligned: happens
    li TESTNUM, 3
    lw t1, 8(s0)
    bne t1, s0, fail

    // Jumps: jalr to a halfword (no link written), jalr ignoring bit 0, jal to a halfword.
    li TESTNUM, 4
    li t1, 0x7777
    la t0, 1f
t6_insn:
    jalr t1, t0, 2
1:  li t2, 0x7777
    bne t1, t2, fail
    la t0, 2f
    jalr t1, t0, 1                // the target's bit 0 is ignored: no trap
    j fail
2:  li t1, 0x6666
t7_insn:
    jal t1, 3f + 2
3:  li t2, 0x6666
    bne t1, t2, fail

    // Branches: taken forward to a halfword, untaken to one, taken backward to one.
    li TESTNUM, 5
t8_insn:
    beq x0, x0, 4f + 2
    j 5f
4:  j fail
5:  bne x0, x0, 4b + 2            // not taken: no trap
    li a2, 1
6:  bnez a2, 7f                   // the first time, skip ahead
    j 8f
7:  li a2, 0
t9_insn:
    beqz a2, 6b + 2               // backward and taken: traps, resumes below
    beqz x0, 6b                   // backward, taken to an aligned target (predicted)
8:
    // Atomics on a misaligned word (18.4): lr is a misaligned load, sc and an
    // AMO a misaligned store/AMO; none reaches the data port.
    li TESTNUM, 8
    addi t0, s0, 1
t10_insn:
    lr.w a1, (t0)
t11_insn:
    sc.w a1, a2, (t0)
t12_insn:
    amoadd.w a1, a2, (t0)
    li TESTNUM, 6
    EXPECT_TRAPS(13)
    EXPECT_TRAP_AT(0, CAUSE_MISALIGNED_LOAD, t0_insn, data, 1)
    EXPECT_TRAP_AT(1, CAUSE_MISALIGNED_LOAD, t1_insn, data, 3)
    EXPECT_TRAP_AT(2, CAUSE_MISALIGNED_LOAD, t2_insn, data, 2)
    EXPECT_TRAP_AT(3, CAUSE_MISALIGNED_LOAD, t3_insn, data, 1)
    EXPECT_TRAP_AT(4, CAUSE_MISALIGNED_STORE, t4_insn, data, 3)
    EXPECT_TRAP_AT(5, CAUSE_MISALIGNED_STORE, t5_insn, data, 2)
    EXPECT_TRAP_AT(6, CAUSE_MISALIGNED_FETCH, t6_insn, t6_insn, 6)
    EXPECT_TRAP_AT(7, CAUSE_MISALIGNED_FETCH, t7_insn, t7_insn, 6)
    EXPECT_TRAP_AT(8, CAUSE_MISALIGNED_FETCH, t8_insn, t8_insn, 10)
    EXPECT_TRAP_AT(9, CAUSE_MISALIGNED_FETCH, t9_insn, t9_insn, -10)
    EXPECT_TRAP_AT(10, CAUSE_MISALIGNED_LOAD, t10_insn, data, 1)
    EXPECT_TRAP_AT(11, CAUSE_MISALIGNED_STORE, t11_insn, data, 1)
    EXPECT_TRAP_AT(12, CAUSE_MISALIGNED_STORE, t12_insn, data, 1)
    li TESTNUM, 7
    lw t1, 0(s0)                  // no misaligned store changed memory
    li t2, 0x03020100
    bne t1, t2, fail
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x03020100, 0x07060504, 0, 0
    .word data
    TRAP_DATA
RVTEST_DATA_END
