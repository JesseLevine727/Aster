// Store buffering with another master (18.6), run in the shell alone with the
// shell's remote master (+remote_sb, tb_core_ports.cpp). Round k = 1..200: the
// program writes GO = k (which releases the remote), waits a few cycles (a
// count varying with k), writes X = k, fences, and reads Y; the remote, after
// its own random delay, writes Y = k, reads X, and reports what it read (R2)
// and DONE = k. Both reads missing the other's write (Y < k and R2 < k) is
// forbidden by RVWMO — the program's X store is a posted store, and its Y
// line is cached from the last round, so the data cache must take the
// remote's snoop of Y by the time its posted store is accepted. Every outcome
// must also be at most k.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, remote_area
    li s1, 1                      // k
    li s2, 201                    // the round after the last
round:
    sw s1, 0(s0)                  // GO = k
    li t0, 7
    mul t0, t0, s1
    andi t0, t0, 15               // 0-15 iterations, varying with k
1:  beqz t0, 2f
    addi t0, t0, -1
    j 1b
2:  sw s1, 16(s0)                 // X = k (posted)
    fence rw, rw
    lw t1, 32(s0)                 // Y
3:  lw t2, 48(s0)                 // wait for DONE = k
    bne t2, s1, 3b
    lw t3, 52(s0)                 // R2: the X the remote read
    bltu s1, t1, fail             // Y above k
    bltu s1, t3, fail             // R2 above k
    bgeu t1, s1, 4f               // Y saw the remote's write: fine
    bltu t3, s1, fail             // both missed: forbidden
4:  addi s1, s1, 1
    bltu s1, s2, round
    RVTEST_PASS
    TEST_PASSFAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    .align 6
    .globl remote_area
remote_area:                      // four lines: GO; X; Y; DONE and R2
    .fill 16, 4, 0
RVTEST_DATA_END
