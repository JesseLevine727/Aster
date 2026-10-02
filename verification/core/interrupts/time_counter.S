// Self-checking test (18.3) of the time counter that `time` and `timeh` read,
// run in the shell alone (Spike's time moves at its own rate, so lockstep
// cannot check it; docs/cpu.md §6): it advances by at least the cycles a
// loop takes and not wildly more, keeps advancing while mcountinhibit stops
// mcycle and minstret, is not moved by a write to mcycle, and timeh is 0 this
// early. It takes no interrupt.
#include "irq_test.h"

    .set shell_expect_interrupts, 0
    .globl shell_expect_interrupts

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    csrr s0, time
    li t2, 100                    // at least 200 cycles (two instructions each)
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr s1, time
    sub t0, s1, s0
    li t1, 200
    bltu t0, t1, fail
    li t1, 5000
    bgeu t0, t1, fail

    li TESTNUM, 3                 // mcycle and minstret stopped: time runs on
    csrwi mcountinhibit, 5
    csrr s0, time
    csrr a0, mcycle
    li t2, 50
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr s1, time
    csrr a1, mcycle
    csrwi mcountinhibit, 0
    bne a0, a1, fail
    sub t0, s1, s0
    li t1, 100
    bltu t0, t1, fail

    li TESTNUM, 4                 // writing mcycle does not move time
    csrr s0, time
    csrw mcycle, zero
    csrw mcycleh, zero
    csrr s1, time
    bltu s1, s0, fail
    sub t0, s1, s0
    li t1, 100
    bgeu t0, t1, fail
    csrr t0, timeh
    bnez t0, fail
    RVTEST_PASS
    TEST_PASSFAIL
    IRQ_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    IRQ_DATA
RVTEST_DATA_END
