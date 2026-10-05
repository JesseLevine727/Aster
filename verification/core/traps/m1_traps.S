// Directed trap test (18.3): exceptions known in M1 — loads and stores the
// memory answers with d_rsp_error (+bus_error_traps, an address outside the
// shell's memory and Spike's: 0x4000_0000), a load and a store access fault
// with mtval the address. Each traps at the commit point with younger work in
// flight that the trap must kill: a store (the shell fails a write with no
// retired store), a CSR write (mie must keep its value), a division that has
// started (re-executed from the beginning after the handler) and a multiply.
// Faulting loads sit behind valid loads whose answers are late, so the error
// is held in M1 while it waits and a division behind it starts before the
// trap kills it: 32 times at the end, each behind a load of a new line (the
// cached core misses and the error waits for the refill; the uncached core's
// answer is late on some iterations under back-pressure). The shell counts
// both (m1_error_waits, div_kills); make core-aster-tests requires them in its
// back-pressure modes and core-aster-l1-tests in every suite mode
// (run_core_tests.py --require-trap-covers).
// The successor of an M1 trap on the ALU path is a dependent use of the
// faulting load's register, which must keep its old value.
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, data
    li s11, 0
    li s1, 0x40000000             // outside memory
    li s2, 0x5a5a5a5a
    li a1, 100
    li a2, 7

    // A faulting load right behind a valid one; the younger CSR write and
    // store are killed and skipped (the handler resumes at 1f), so neither
    // may ever take effect.
    li a3, 0x1234
    la s11, 1f
    lw t3, 0(s0)
t0_insn:
    lw a3, 0(s1)
    csrwi mie, 8
    sw s2, 0(s0)
1:  csrr t1, mie
    bnez t1, fail
    lw t1, 0(s0)
    li t2, 0x11111111
    bne t1, t2, fail
    addi a4, a3, 1                // the faulting load wrote nothing
    li t1, 0x1235
    bne a4, t1, fail
    sw s2, 0(s0)

    // A faulting store, then a faulting load with a division and a multiply behind it.
t1_insn:
    sw s2, 4(s1)
    lw t3, 4(s0)
t2_insn:
    lw a3, 8(s1)
    div a5, a1, a2
    mul a6, a1, a2
    li t1, 14
    bne a5, t1, fail
    li t1, 700
    bne a6, t1, fail

    // A sub-word faulting load and store (mtval: the byte address).
t3_insn:
    lbu a3, 3(s1)
t4_insn:
    sh a3, 6(s1)
    // Atomics outside memory (18.4): lr is a load access fault, sc and an
    // AMO a store/AMO access fault; a3 keeps its value.
    li a3, 0x77
t5_insn:
    lr.w a3, (s1)
t6_insn:
    sc.w a3, s2, (s1)
t7_insn:
    amoswap.w a3, s2, (s1)
    li t1, 0x77
    bne a3, t1, fail

    // 32 faulting loads, each right behind a load of a new line, with a
    // division and a multiply behind it (resumed at the division).
    la s3, lines
    li s4, 32
2:  lw t3, 0(s3)
t8_insn:
    lw a3, 12(s1)
    div a5, a1, a2
    mul a6, a1, a2
    li t1, 14
    bne a5, t1, fail
    li t1, 700
    bne a6, t1, fail
    addi s3, s3, 16
    addi s4, s4, -1
    bnez s4, 2b
    li t1, 0x77
    bne a3, t1, fail

    li TESTNUM, 3
    EXPECT_TRAPS(40)
    EXPECT_TRAP(0, CAUSE_LOAD_ACCESS, t0_insn, 0x40000000)
    EXPECT_TRAP(1, CAUSE_STORE_ACCESS, t1_insn, 0x40000004)
    EXPECT_TRAP(2, CAUSE_LOAD_ACCESS, t2_insn, 0x40000008)
    EXPECT_TRAP(3, CAUSE_LOAD_ACCESS, t3_insn, 0x40000003)
    EXPECT_TRAP(4, CAUSE_STORE_ACCESS, t4_insn, 0x40000006)
    EXPECT_TRAP(5, CAUSE_LOAD_ACCESS, t5_insn, 0x40000000)
    EXPECT_TRAP(6, CAUSE_STORE_ACCESS, t6_insn, 0x40000000)
    EXPECT_TRAP(7, CAUSE_STORE_ACCESS, t7_insn, 0x40000000)
    la s3, trap_log + 16 * 8      // traps 8-39: the loop's
    li s4, 32
3:  lw t1, 0(s3)
    li t2, CAUSE_LOAD_ACCESS
    bne t1, t2, fail
    lw t1, 4(s3)
    la t2, t8_insn
    bne t1, t2, fail
    lw t1, 8(s3)
    li t2, 0x4000000c
    bne t1, t2, fail
    addi s3, s3, 16
    addi s4, s4, -1
    bnez s4, 3b
    li TESTNUM, 4
    lw t1, 0(s0)
    bne t1, s2, fail
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x11111111, 0x22222222, 0, 0
    .align 4
lines:
    .fill 128, 4, 0                 // 32 lines of 16 bytes
    TRAP_DATA
RVTEST_DATA_END
