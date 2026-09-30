// Directed test (docs/cpu.md §4, milestone 18.1): Decode predicts a backward
// branch taken only when its target is word-aligned — a halfword target raises
// an exception if taken, so the branch is left to Execute. Here backward
// branches with halfword targets are never taken: with --cpi-check they must
// cost nothing (a Decode redirect to the misaligned target, then an Execute
// redirect back, would add cycles the CPI model does not charge).
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 1
    li a0, 0
    li t0, 5
    .balign 4
1:  addi a0, a0, 1
    nop
    bne x0, x0, 1b + 2            // backward, halfword target, never taken
    blt t0, x0, 1b + 2            // likewise (t0 > 0)
    addi t0, t0, -1
    bnez t0, 1b                   // the loop itself: backward, aligned, taken 4 times
    li t1, 5
    bne a0, t1, fail
    RVTEST_PASS
fail:
    RVTEST_FAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
RVTEST_DATA_END
