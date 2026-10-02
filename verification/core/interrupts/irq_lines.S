// Directed interrupt test (18.3): each line (MSIP, MTIP, MEIP) taken alone,
// with mcause its code, mtval zero and mepc inside the waiting loop; a `wfi`
// in that loop is a no-op; mip read with each line alone and with all three
// raised while MIE is clear; then the three taken in priority order (MEI, MSI, MTI)
// when it is set: each handler clears only its own line, so the next is taken
// right after its mret.
#include "irq_test.h"

    .set shell_expect_interrupts, 6
    .globl shell_expect_interrupts

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    li t0, MIP_MSIP | MIP_MTIP | MIP_MEIP
    csrw mie, t0
    csrsi mstatus, MSTATUS_MIE

    // One line at a time.
    IRQ_RAISE(MIP_MSIP, 5)
wait1:
    wfi
    la t0, irq_count
    lw t1, 0(t0)
    beqz t1, wait1
wait1_end:
    IRQ_RAISE(MIP_MTIP, 0)
    IRQ_WAIT(2)
    IRQ_RAISE(MIP_MEIP, 20)
    IRQ_WAIT(3)
    li TESTNUM, 3
    la t0, irq_log
    lw t1, 0(t0)
    li t2, IRQ_MSI
    bne t1, t2, fail
    lw t1, 4(t0)                  // mepc inside the waiting loop
    la t2, wait1
    bltu t1, t2, fail
    la t2, wait1_end
    bgeu t1, t2, fail
    lw t1, 8(t0)                  // an interrupt leaves mtval zero
    bnez t1, fail
    lw t1, 16(t0)
    li t2, IRQ_MTI
    bne t1, t2, fail
    lw t1, 32(t0)
    li t2, IRQ_MEI
    bne t1, t2, fail

    // mip's layout, one line at a time while MIE is clear (none is taken).
    li TESTNUM, 6
    csrci mstatus, MSTATUS_MIE
    IRQ_RAISE(MIP_MSIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr t1, mip
    li t2, MIP_MSIP
    bne t1, t2, fail
    IRQ_RAISE(MIP_MTIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr t1, mip
    li t2, MIP_MTIP
    bne t1, t2, fail
    IRQ_RAISE(MIP_MEIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr t1, mip
    li t2, MIP_MEIP
    bne t1, t2, fail

    // All three at once while MIE is clear: none is taken, and mip shows them
    // (bits 11, 7 and 3); then they are taken by priority.
    li TESTNUM, 4
    IRQ_RAISE(MIP_MSIP | MIP_MTIP | MIP_MEIP, 0)
    li t2, 50
1:  addi t2, t2, -1
    bnez t2, 1b
    csrr t1, mip
    li t2, MIP_MSIP | MIP_MTIP | MIP_MEIP
    bne t1, t2, fail
    la t0, irq_count
    lw t1, 0(t0)
    li t2, 3
    bne t1, t2, fail
    csrsi mstatus, MSTATUS_MIE
    IRQ_WAIT(6)
    li TESTNUM, 5
    la t0, irq_log
    lw t1, 48(t0)
    li t2, IRQ_MEI
    bne t1, t2, fail
    lw t1, 64(t0)
    li t2, IRQ_MSI
    bne t1, t2, fail
    lw t1, 80(t0)
    li t2, IRQ_MTI
    bne t1, t2, fail
    RVTEST_PASS
    TEST_PASSFAIL
    IRQ_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    IRQ_DATA
RVTEST_DATA_END
