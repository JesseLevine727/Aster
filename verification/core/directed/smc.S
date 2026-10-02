// Directed test (18.4, docs/cpu.md §6.3 "fence.i self-modifying code"): code
// that rewrites instructions and executes them after fence.i, in lockstep with
// Spike and self-checking. fence.i waits in Execute until every older data
// access has been answered, then refetches everything after it, so:
// - an instruction rewritten right behind the store (already fetched, stale,
//   when fence.i executes) runs as rewritten;
// - the store is still in flight when fence.i reaches Execute (it waits);
// - a loop rewrites its own body each iteration, adding a different value
//   (40 times: under back-pressure some of the stores' answers come late,
//   and fence.i must wait for them, not just for M1 to empty).
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    // addi a0, x0, 7 over addi a0, x0, 1, executed right after fence.i.
    la s0, patch1
    li t0, 0x00700513
    sw t0, 0(s0)
    fence.i
patch1:
    addi a0, x0, 1
    li t1, 7
    bne a0, t1, fail

    // A loop rewriting its body: iteration k adds k (addi a1, a1, k).
    li TESTNUM, 3
    li a1, 0
    li s1, 1                      // k
    la s2, patch2
1:  slli t0, s1, 20
    li t2, 0x00058593             // addi a1, a1, 0
    or t0, t0, t2
    sw t0, 0(s2)
    fence.i
patch2:
    addi a1, a1, 0
    addi s1, s1, 1
    li t3, 41
    bne s1, t3, 1b
    li t3, 820                    // 1 + 2 + ... + 40
    bne a1, t3, fail

    // The store's answer is late (a load ahead of it under back-pressure).
    li TESTNUM, 4
    la s0, patch3
    li t0, 0x02a00613             // addi a2, x0, 42
    lw t4, 0(s0)
    sw t0, 0(s0)
    fence.i
patch3:
    addi a2, x0, 2
    li t1, 42
    bne a2, t1, fail
    RVTEST_PASS
    TEST_PASSFAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
RVTEST_DATA_END
