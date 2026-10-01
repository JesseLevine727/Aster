// Directed test (docs/cpu.md §4, milestone 18.2): a multiply or divide on a
// mispredicted path is squashed as it enters Execute and must have no effect:
// it writes no register, and a division never starts (the divider would hold
// Execute for 34 cycles, which the cycle check of make core-aster-tests would
// see, and the RTL asserts that a squashed instruction does nothing).
//
// The instruction after a branch that is taken although predicted not taken
// (forward), or after a `jalr`, enters Execute at the edge the redirect
// resolves, so it is the one squashed there. Each wrong-path instruction
// would overwrite a checked register: a division by zero writes -1.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li s2, 7
    li s3, 9
    li s4, 11
    li s5, 13
    li a1, 100
    li a2, 3

    li TESTNUM, 1                 // forward branch taken: div on the wrong path
    li t0, 1
    bnez t0, 1f
    div s2, a1, zero
1:  li t1, 7
    bne s2, t1, fail

    li TESTNUM, 2                 // forward branch taken: mul on the wrong path
    bnez t0, 1f
    mul s3, a1, a2
1:  li t1, 9
    bne s3, t1, fail

    li TESTNUM, 3                 // jalr: rem on the wrong path
    la t2, 1f
    jalr x0, 0(t2)
    rem s4, a1, a2
1:  li t1, 11
    bne s4, t1, fail

    li TESTNUM, 4                 // jalr: mulhu on the wrong path
    la t2, 1f
    jalr x0, 0(t2)
    mulhu s5, a1, a1
1:  li t1, 13
    bne s5, t1, fail

    li TESTNUM, 5                 // the right path still divides
    div s2, a1, a2
    li t1, 33
    bne s2, t1, fail
    rem s4, a1, a2
    li t1, 1
    bne s4, t1, fail

    RVTEST_PASS
fail:
    RVTEST_FAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
RVTEST_DATA_END
