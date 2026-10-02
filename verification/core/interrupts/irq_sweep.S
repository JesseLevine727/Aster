// Directed interrupt test (18.3, docs/cpu.md §6.3 "interrupts arriving in
// every pipeline state"): one MEIP per iteration, raised `d` cycles after the
// device store for d = 0..127, while the same block runs — CSR writes read
// back (mie, toggling MTIE, which cannot fire: MTIP stays low; and
// mcountinhibit: a write in M1 when the interrupt becomes pending must not be
// lost), loads, stores, a load-use, a multiply, a 36-cycle division (an
// interrupt that arrives while it divides waits for it: one is taken only
// after an instruction in M1), branches taken and not, a Decode-redirected
// `jal`, an Execute-redirected `jalr` and a CSR read — so the interrupt lands
// behind every instruction of the block in turn. Each iteration checks every
// result, that exactly one interrupt was taken and that it left mtval zero;
// the shell checks that no store was performed twice (a store interrupted and
// re-executed would be). Under back-pressure the arrivals also meet M1 waiting
// for M2.
#include "irq_test.h"

    .set shell_expect_interrupts, 128
    .globl shell_expect_interrupts

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, data
    li s1, 0                      // d
    li s2, 128
    li t0, MIP_MEIP
    csrw mie, t0
    csrsi mstatus, MSTATUS_MIE
iteration:
    // The device delay field is bits 31:16.
    slli t1, s1, 16
    li t2, MIP_MEIP
    or t1, t1, t2
    li t0, IRQ_DEVICE
    sw t1, 0(t0)
    // The block.
    li t4, MIP_MEIP | MIP_MTIP
    csrw mie, t4
    csrr t5, mie
    bne t5, t4, fail
    csrwi mcountinhibit, 5
    csrr t5, mcountinhibit
    li t6, 5
    bne t5, t6, fail
    li t4, MIP_MEIP
    csrw mie, t4
    csrwi mcountinhibit, 0
    csrr t5, mie
    bne t5, t4, fail
    csrr t5, mcountinhibit
    bnez t5, fail
    sw s1, 0(s0)
    lw a0, 0(s0)                  // load-use
    addi a1, a0, 3
    li a2, 1000
    mul a3, a1, a2
    li a4, 7
    div a5, a3, a4
    rem a6, a3, a4
    beqz x0, 1f                   // taken forward (Execute redirect)
    j fail
1:  bnez x0, fail                 // not taken
    jal ra, leaf                  // Decode redirect; leaf returns with jalr
    sw a7, 4(s0)
    lw t2, 4(s0)
    csrr t3, mtvec
    // Checks: a1 = d + 3, a3 = 1000 (d + 3), a5 = a3 / 7, a6 = a3 % 7, a7 = a5 + a6.
    addi t4, s1, 3
    bne a1, t4, fail
    li t5, 1000
    mul t5, t4, t5
    bne a3, t5, fail
    li t6, 7
    divu t4, t5, t6
    bne a5, t4, fail
    remu t6, t5, t6
    bne a6, t6, fail
    add t4, t4, t6
    bne a7, t4, fail
    bne t2, t4, fail
    la t4, mtvec_handler
    bne t3, t4, fail
    // Exactly one interrupt per iteration (wait for a late one).
    addi s1, s1, 1
    la t0, irq_count
2:  lw t1, 0(t0)
    bltu t1, s1, 2b
    bne t1, s1, fail
    addi t1, t1, -1               // this iteration's entry: mcause MEI, mtval zero
    andi t1, t1, 63
    slli t1, t1, 4
    la t2, irq_log
    add t1, t1, t2
    lw t2, 0(t1)
    li t3, IRQ_MEI
    bne t2, t3, fail
    lw t2, 8(t1)
    bnez t2, fail
    bltu s1, s2, iteration
    RVTEST_PASS
    TEST_PASSFAIL

leaf:
    add a7, a5, a6
    ret
    IRQ_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0, 0, 0, 0
    IRQ_DATA
RVTEST_DATA_END
