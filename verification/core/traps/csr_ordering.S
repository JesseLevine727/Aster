// Directed CSR test (18.3, docs/cpu.md §6.3 "CSR read-modify-write
// ordering"): Zicsr in lockstep with Spike — every CSR write's value and every
// read compared — and self-checking.
// - Read-modify-write chains on mscratch, each reading the last one's write
//   (CSR instructions are serializing: Execute holds one until M1 is empty).
// - The source operand forwarded from an ALU result, a load (load-use), a
//   multiply and a division; the CSR result forwarded to an ALU operand at
//   distances 1-4, a branch, a store's data, a load's address and a `jalr`.
// - minstret exactly: a write suppresses the writing instruction's own
//   increment, a trapping instruction does not count, the handler's do, and
//   mcountinhibit.IR freezes it; mcountinhibit.CY freezes mcycle (two reads
//   equal — the values themselves are not compared, docs/cpu.md §6). The
//   counter writes (mcountinhibit, minstret, minstreth, mcycle) sit right
//   behind loads and stores, so under back-pressure the writer waits in M1:
//   its own retirement still counts by the mcountinhibit before it and a
//   minstret write still suppresses it, as in Spike, and mcycle counts on from a written
//   value (a range check, true in Spike as in the core) and carries into
//   mcycleh.
// - The hardware performance monitor's mhpmcounter3-31(h) and mhpmevent3-31
//   read 0 and ignore writes.
// - mret: mstatus.MIE from MPIE and MPIE set (no interrupt is pending), and
//   `csrw mepc` right before `mret`; a `csrw mtvec` right before a trap that
//   then goes to the new handler; `wfi` as a no-op.
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    la s0, data
    li s11, 0

    // Read-modify-write chains.
    li t3, 0x00ff00ff
    csrw mscratch, t3
    csrrs a0, mscratch, s0        // a0 = 0x00ff00ff, mscratch |= s0
    csrrc a1, mscratch, t3        // a1 = 0x00ff00ff | s0
    csrrwi a2, mscratch, 0x15     // a2 = s0 & ~0x00ff00ff
    csrrsi a3, mscratch, 0x0a     // a3 = 0x15
    csrrci a4, mscratch, 0x01     // a4 = 0x1f
    csrr a5, mscratch             // 0x1e
    bne a0, t3, fail
    or t4, t3, s0
    bne a1, t4, fail
    not t5, t3
    and t5, s0, t5
    bne a2, t5, fail
    li t4, 0x15
    bne a3, t4, fail
    li t4, 0x1f
    bne a4, t4, fail
    li t4, 0x1e
    bne a5, t4, fail

    // Sources forwarded into CSR writes.
    li TESTNUM, 3
    addi t0, s0, 4
    csrw mscratch, t0             // from an ALU result (distance 1)
    lw t1, 0(s0)
    csrw mtval, t1                // from a load (waits for it)
    li t2, 3
    mul t2, t2, t1
    csrw mepc, t2                 // from a multiply
    li t3, 100
    li t4, 7
    div t3, t3, t4
    csrw mcause, t3               // from a division (14)
    csrr a0, mscratch
    bne a0, t0, fail
    csrr a0, mtval
    bne a0, t1, fail
    csrr a0, mepc                 // mepc drops bits 1:0
    andi t2, t2, -4
    bne a0, t2, fail
    csrr a0, mcause
    li t4, 14
    bne a0, t4, fail

    // CSR results forwarded out.
    li TESTNUM, 4
    la t0, 1f
    csrw mscratch, t0
    csrr a0, mscratch
    jalr x0, a0, 0                // a jalr target
    j fail
1:  csrw mscratch, s0
    csrr a0, mscratch
    lw a1, 0(a0)                  // a load's address
    csrr a2, mscratch
    sw a2, 8(s0)                  // a store's data
    csrr a3, mscratch
    beq a3, s0, 2f                // a branch
    j fail
2:  csrr a4, mscratch
    nop
    nop
    nop
    add a5, a4, a4                // distance 4 (the register file's write-through)
    csrr a6, mscratch
    nop
    add a7, a6, a6                // distance 2
    lw t1, 8(s0)
    bne t1, s0, fail
    add t2, s0, s0
    bne a5, t2, fail
    bne a7, t2, fail

    // minstret: a write suppresses its own increment; the next read sees it.
    li TESTNUM, 5
    csrwi minstret, 0
    csrr a0, minstret             // 0
    nop
    nop
    csrr a1, minstret             // 3 (the first read and two nops)
    bnez a0, fail
    li t0, 3
    bne a1, t0, fail
    // A trapping instruction does not count; the handler's instructions do.
    csrwi minstret, 0
trap0:
    ecall
    csrr a2, minstret
    // mcountinhibit.IR freezes minstret.
    csrwi mcountinhibit, 4
    csrr a3, minstret
    nop
    nop
    csrr a4, minstret
    csrwi mcountinhibit, 0
    bne a3, a4, fail
    // mcountinhibit.CY freezes mcycle (only the comparison is checked).
    csrwi mcountinhibit, 1
    csrr a5, mcycle
    nop
    csrr a6, mcycle
    csrwi mcountinhibit, 0
    bne a5, a6, fail
    // Counter writes right behind loads and stores (the writer may wait in M1).
    li t2, 3
1:  lw t1, 0(s0)
    csrwi mcountinhibit, 4
    csrr a3, minstret
    sw t1, 12(s0)
    csrwi mcountinhibit, 0
    csrr a4, minstret
    bne a3, a4, fail              // neither the store nor the second write counted
    addi t2, t2, -1
    bnez t2, 1b
    // minstret and minstreth written right behind a load and a store.
    li t0, 100
    lw t1, 0(s0)
    csrw minstret, t0             // the writer does not count itself
    csrr a3, minstret
    li t2, 100
    bne a3, t2, fail
    sw t1, 12(s0)
    csrw minstreth, t0
    csrr a3, minstreth
    bne a3, t0, fail
    lw t1, 0(s0)
    csrw minstreth, zero
    lw t1, 0(s0)
    li t0, 0x1000
    csrw mcycleh, zero
    csrw mcycle, t0
    nop
    nop
    csrr a5, mcycle               // it counts on from 0x1000
    li t0, 0x1001
    bltu a5, t0, fail
    li t0, 0x1040
    bgeu a5, t0, fail
    li t0, -16
    csrw mcycleh, zero
    csrw mcycle, t0
    li t2, 12
2:  addi t2, t2, -1
    bnez t2, 2b
    csrr a6, mcycleh              // the carry out of mcycle
    li t0, 1
    bne a6, t0, fail
    // The hardware performance monitor: zero, writes ignored.
    csrr a0, mhpmcounter3
    bnez a0, fail
    li t0, 5
    csrw mhpmcounter3, t0
    csrw mhpmcounter31h, t0
    csrw mhpmevent17, t0
    csrr a0, mhpmcounter3
    csrr a1, mhpmcounter31h
    csrr a2, mhpmevent17
    or a0, a0, a1
    or a0, a0, a2
    bnez a0, fail
    // Writing the counters' halves.
    li t0, -1
    csrw minstreth, t0
    csrw minstret, t0
    nop                           // wraps minstret to 0 and minstreth to 0
    csrr a0, minstreth
    bnez a0, fail
    li t0, 0x1234
    csrw mcycle, t0
    csrw mcycleh, t0

    // mret: MIE = MPIE, MPIE = 1; mepc written right before it.
    li TESTNUM, 6
    li t0, MSTATUS_MPIE
    csrs mstatus, t0
    la t1, 3f
    csrw mepc, t1
    mret
    j fail
3:  csrr a0, mstatus
    li t0, MSTATUS_MPP | MSTATUS_MPIE | MSTATUS_MIE
    bne a0, t0, fail
    csrci mstatus, MSTATUS_MIE
    wfi                           // a no-op

    // A new mtvec right before a trap.
    li TESTNUM, 7
    la t0, other_handler
    csrrw s1, mtvec, t0
    ebreak
    csrw mtvec, s1
    li t0, 1
    bne s2, t0, fail              // other_handler ran

    li TESTNUM, 8
    EXPECT_TRAPS(1)               // the ecall (the ebreak went to other_handler)
    EXPECT_TRAP(0, CAUSE_MACHINE_ECALL, trap0, 0)
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER

    .align 2
other_handler:
    li s2, 1
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x13572468, 0, 0, 0
    TRAP_DATA
RVTEST_DATA_END
