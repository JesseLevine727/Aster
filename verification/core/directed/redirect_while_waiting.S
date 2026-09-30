// Directed test (docs/cpu.md §4, milestone 18.1): control transfers that sit
// in Decode while they wait for a load's result. A backward branch (predicted
// taken) or a `jal` redirects once, in its first Decode cycle, and keeps
// waiting there; the redirect must neither be lost nor repeated. With
// --cpi-check the cycle count must equal the CPI model's, which charges the
// load-use wait and the redirect overlapping.
//
// 1. A list walk: `lw` then a backward `bnez` on the loaded value at distance
//    1 (two cycles in Decode) and, in the second loop, at distance 2.
// 2. A `jal` right behind a load-use consumer (it waits in the buffer, then
//    redirects from Decode), and a `jalr` whose address comes from a load.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 1                 // distance 1: lw; bnez back
    la t0, list
    li a0, 0
1:  lw t1, 0(t0)
    bnez t1, 2f                   // forward (not taken until the end)
    j 3f
2:  addi a0, a0, 1
    addi t0, t0, 4
    lw t1, 0(t0)
    bnez t1, 2b                   // backward, on the load at distance 1
3:  li t2, 7
    bne a0, t2, fail

    li TESTNUM, 2                 // distance 2: lw; addi; bnez back
    la t0, list
    li a0, 0
4:  lw t1, 0(t0)
    addi t0, t0, 4
    addi a0, a0, 1
    bnez t1, 4b
    li t2, 8
    bne a0, t2, fail

    li TESTNUM, 3                 // jal behind a load-use consumer; jalr through a loaded address
    la t0, targets
    li a1, 0
    lw t1, 0(t0)
    add a1, a1, t1
    jal ra, 5f
    j 6f
5:  lw t3, 4(t0)
    jalr x0, 0(t3)
    j fail
6:  li t2, 3
    bne a1, t2, fail

    RVTEST_PASS
fail:
    RVTEST_FAIL
7:  li a1, 3
    jr ra
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
list:
    .word 11, 22, 33, 44, 55, 66, 77, 0
targets:
    .word 3, 7b
RVTEST_DATA_END
