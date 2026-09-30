// Directed test (docs/cpu.md §4, milestone 18.1): wrong-path fetches that run
// off the end of memory are answered with i_rsp_error and must be discarded
// without effect — no trap, and only right-path instructions retire.
//
// The `.tail` code is the last thing in memory (directed/link.ld), so the
// fetch unit's sequential fetches after its last instructions fault. It is
// entered three ways, each leaving fetches past the end on the wrong path:
//   1. `beqz a2` backward, predicted and taken: a Decode redirect;
//   2. `beqz a2` predicted taken but not taken (an Execute redirect back to
//      the fall-through), then `bnez a3` not taken and `jr ra` — a `jalr`
//      one word from the end;
//   3. as 2, but `bnez a3` taken forward (predicted not taken: an Execute
//      redirect) to `jr ra` in the last word.
// Each visit counts in a0; the test checks the count and the path taken.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li a0, 0

    li TESTNUM, 1                 // path 1: a2 = 0
    li a2, 0
    li a3, 0
    j tail
back:
    li t0, 1
    bne a0, t0, fail

    li TESTNUM, 2                 // path 2: a2 != 0, a3 = 0
    li a2, 1
    jal ra, tail
    li t0, 2
    bne a0, t0, fail
    li t0, 2
    bne a4, t0, fail              // the fall-through path was taken

    li TESTNUM, 3                 // path 3: a2 != 0, a3 != 0
    li a3, 1
    jal ra, tail
    li t0, 3
    bne a0, t0, fail
    li t0, 3
    bne a4, t0, fail              // the forward branch was taken

    RVTEST_PASS
fail:
    RVTEST_FAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
RVTEST_DATA_END

    .section .tail, "ax", @progbits
    .globl shell_expect_fetch_errors      // the runner requires fetches outside memory
shell_expect_fetch_errors:
tail:
    addi a0, a0, 1
    beqz a2, back                 // backward: predicted taken
    li a4, 2
    bnez a3, last                 // forward: predicted not taken
    jr ra
last:
    li a4, 3
    jr ra                         // the last word of memory
