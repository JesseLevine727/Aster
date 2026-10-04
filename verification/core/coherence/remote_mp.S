// Coherence with another master (18.6), run in the shell alone with the
// shell's remote master (+remote_mp, tb_core_ports.cpp): the shell writes a
// message into remote_area again and again — data words 1-15 set to the
// message's number n, then the flag word 0 set to n — and, with the caches,
// presents each written line on the data cache's snoop input. The program reads
// the flag and then the data, which must be at least the flag (message passing:
// the data was written first), and the flag never goes back; reading the flag
// again after the data, it must be at least the first read. It ends when the
// flag reaches 200. The lines stay in the data cache between messages, so
// without the snoops the flag would never change (the run would time out),
// and a line refilled across a write and kept would show data older than its
// flag.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, remote_area
    li s1, 0                      // the last flag seen
    li s2, 200                    // the flag that ends the test
loop:
    lw t0, 0(s0)                  // the flag
    bltu t0, s1, fail             // never goes back
    mv s1, t0
    addi t1, s0, 4                // data words 1-15, in order
    addi t2, s0, 64
1:  lw t3, 0(t1)
    bltu t3, t0, fail             // data older than the flag read before it
    addi t1, t1, 4
    bltu t1, t2, 1b
    lw t4, 0(s0)                  // the flag again
    bltu t4, t0, fail
    addi t1, s0, 60               // and the data again, last word first
2:  lw t3, 0(t1)
    bltu t3, t0, fail
    addi t1, t1, -4
    bne t1, s0, 2b
    bltu s1, s2, loop
    RVTEST_PASS
    TEST_PASSFAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    .align 6
    .globl remote_area
remote_area:                      // four lines: the flag and data words 1-3, then data 4-15
    .fill 16, 4, 0
RVTEST_DATA_END
