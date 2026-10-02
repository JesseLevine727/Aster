// Directed interrupt test (18.3): enabling and priority, with exact mepc.
// - A pending line is not taken while its mie bit or mstatus.MIE is clear.
// - No interrupt is taken after a CSR instruction or `mret` in M1 (they change
//   what enables interrupts; docs/cpu.md §9): when `csrs mie`, `csrsi mstatus`
//   or `mret` enables a pending interrupt, it is taken after the next
//   instruction, so mepc is the one after that.
// - An exception and a pending interrupt at the same instruction: the
//   exception is taken (a faulting load, and an ecall, right behind the
//   `csrsi` that enables the interrupt); the interrupt follows after the
//   handler's mret, after the instruction there.
#include "irq_test.h"

    .set shell_expect_interrupts, 5
    .globl shell_expect_interrupts

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    li s0, 0
    // MEIP high, mie.MEIE clear, MIE set: not taken.
    IRQ_RAISE(MIP_MEIP, 0)
    csrsi mstatus, MSTATUS_MIE
    li t2, 30
1:  addi t2, t2, -1
    bnez t2, 1b
    la t0, irq_count
    lw t1, 0(t0)
    bnez t1, fail
    // Enabled by csrs mie: taken after the next instruction.
    li t0, MIP_MEIP
    csrs mie, t0
    addi s0, s0, 1
e1: addi s0, s0, 1
    IRQ_WAIT(1)
    EXPECT_IRQ(0, IRQ_MEI, e1)

    // Enabled by csrsi mstatus.
    li TESTNUM, 3
    csrci mstatus, MSTATUS_MIE
    IRQ_RAISE(MIP_MEIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    csrsi mstatus, MSTATUS_MIE
    addi s0, s0, 1
e2: addi s0, s0, 1
    IRQ_WAIT(2)
    EXPECT_IRQ(1, IRQ_MEI, e2)

    // Enabled by mret (MPIE set).
    li TESTNUM, 4
    csrci mstatus, MSTATUS_MIE
    IRQ_RAISE(MIP_MEIP, 0)
    li t0, MSTATUS_MPIE
    csrs mstatus, t0
    la t0, 2f
    csrw mepc, t0
    mret
2:  addi s0, s0, 1
e3: addi s0, s0, 1
    IRQ_WAIT(3)
    EXPECT_IRQ(2, IRQ_MEI, e3)

    // An exception (a faulting load) while the interrupt becomes pending.
    li TESTNUM, 5
    csrci mstatus, MSTATUS_MIE
    IRQ_RAISE(MIP_MEIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    li t3, 0x40000000
    csrsi mstatus, MSTATUS_MIE
x1: lw t4, 0(t3)                  // traps: the exception wins
    addi s0, s0, 1                // where the handler returns
e4: addi s0, s0, 1                // the interrupt comes after the instruction above
    IRQ_WAIT(5)
    EXPECT_IRQ(3, CAUSE_LOAD_ACCESS, x1)
    EXPECT_IRQ(4, IRQ_MEI, e4)

    // An ecall the same way.
    li TESTNUM, 6
    csrci mstatus, MSTATUS_MIE
    IRQ_RAISE(MIP_MEIP, 0)
    li t2, 10
1:  addi t2, t2, -1
    bnez t2, 1b
    csrsi mstatus, MSTATUS_MIE
x2: ecall
    addi s0, s0, 1
e5: addi s0, s0, 1
    IRQ_WAIT(7)
    EXPECT_IRQ(5, CAUSE_MACHINE_ECALL, x2)
    EXPECT_IRQ(6, IRQ_MEI, e5)
    li TESTNUM, 7
    li t0, 10                     // every addi ran exactly once
    bne s0, t0, fail
    RVTEST_PASS
    TEST_PASSFAIL
    IRQ_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    IRQ_DATA
RVTEST_DATA_END
