// Directed test (18.4): the A extension in lockstep with Spike (every result,
// memory record and sc outcome compared) and self-checking.
// - Operands forwarded into AMOs: the address from an ALU result (distance 1)
//   and from a load (load-use), the data from a multiply, a division and a
//   load; AMO, lr and sc results (late: from W, like a load's) forwarded to an
//   ALU operand, a branch, a store's data and the next AMO's address.
// - Every AMO at the corners: signed and unsigned min and max across the sign
//   boundary, swap, and add wrapping; rd = x0, rd = rs1 and rd = rs2.
// - lr/sc: success; an sc with no reservation fails; an sc to another word
//   fails and ends the reservation; a second lr moves it; the hart's own store
//   between lr and sc does not end it (as in Spike: the memory side decides,
//   docs/cpu.md §5); work between lr and sc.
// - Ordering on one hart: a store, an AMO and a load to one word observe each
//   other in program order.
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    la s0, data
    li TESTNUM, 2

    // Forwarding into an AMO: the address from an ALU result and from a load.
    li t0, 5
    addi a0, s0, 0
    amoadd.w a1, t0, (a0)         // data[0]: 10 -> 15, a1 = 10
    lw a2, 36(s0)                 // the address of data[1]
    amoswap.w a3, t0, (a2)        // load-use into the address: data[1] -> 5, a3 = 20
    li t1, 10
    bne a1, t1, fail
    li t1, 20
    bne a3, t1, fail
    // The data from a multiply, a division and a load.
    li t0, 6
    li t1, 7
    mul t2, t0, t1
    amoadd.w a4, t2, (s0)         // 15 + 42 = 57
    li t3, 100
    div t4, t3, t1                // 14
    amoxor.w a5, t4, (s0)         // 57 ^ 14 = 55
    lw t5, 8(s0)                  // data[2] = 3
    amoor.w a6, t5, (s0)          // 55 | 3 = 55
    lw t6, 0(s0)
    li t1, 55
    bne t6, t1, fail

    // AMO results forwarded at distance 1: to an ALU, a branch, a store, an address.
    li TESTNUM, 3
    li t0, 1
    amoadd.w a0, t0, (s0)         // a0 = 55
    addi a1, a0, 1
    amoadd.w a2, t0, (s0)         // a2 = 56
    bne a2, a1, fail
    amoadd.w a3, t0, (s0)         // a3 = 57
    sw a3, 12(s0)
    lw t1, 12(s0)
    bne t1, a3, fail
    addi t2, s0, 40
    lr.w a4, (t2)                 // a4 = &data[3]
    amoswap.w a5, t0, (a4)        // the lr result is the next AMO's address: a5 = 57
    bne a5, a3, fail

    // Corners: min/max signed and unsigned across the sign boundary.
    li TESTNUM, 4
    li t0, -1
    sw t0, 16(s0)                 // data[4] = -1
    addi a0, s0, 16
    li t1, 1
    amomin.w a1, t1, (a0)         // min(-1, 1) = -1 stays
    lw a2, 16(s0)
    bne a2, t0, fail
    amominu.w a1, t1, (a0)        // minu(0xffffffff, 1) = 1
    lw a2, 16(s0)
    bne a2, t1, fail
    amomax.w a1, t0, (a0)         // max(1, -1) = 1 stays
    amomaxu.w a1, t0, (a0)        // maxu(1, 0xffffffff) = 0xffffffff
    lw a2, 16(s0)
    bne a2, t0, fail
    li t2, 0x80000000
    amoand.w a1, t2, (a0)         // 0xffffffff & 0x80000000
    amoadd.w a1, t2, (a0)         // 0x80000000 + 0x80000000 wraps to 0
    lw a2, 16(s0)
    bnez a2, fail
    // Register aliasing: rd = x0, rd = rs1, rd = rs2.
    li t3, 9
    amoswap.w x0, t3, (a0)        // data[4] = 9, nothing written to a register
    mv t4, a0
    amoadd.w t4, t3, (t4)         // rd = rs1: t4 = 9, data[4] = 18
    li t5, 9
    bne t4, t5, fail
    li t5, 4
    amoadd.w t5, t5, (a0)         // rd = rs2: t5 = 18, data[4] = 22
    li t6, 18
    bne t5, t6, fail

    // lr/sc.
    li TESTNUM, 5
    addi a0, s0, 20               // data[5]
    addi a1, s0, 24               // data[6]
    li t0, 0x1234
    lr.w t1, (a0)
    sc.w t2, t0, (a0)             // succeeds
    bnez t2, fail
    sc.w t2, t0, (a0)             // no reservation: fails
    beqz t2, fail
    lr.w t1, (a0)
    sc.w t2, t0, (a1)             // another word: fails, and ends the reservation
    beqz t2, fail
    sc.w t2, t0, (a0)
    beqz t2, fail
    lr.w t1, (a0)
    lr.w t1, (a1)                 // the reservation moves to data[6]
    sc.w t2, t0, (a0)
    beqz t2, fail
    lr.w t1, (a1)
    addi t3, t1, 1                // work between lr and sc
    mul t3, t3, t3
    sw t3, 28(s0)
    sc.w t2, t3, (a1)
    bnez t2, fail
    lr.w t1, (a0)
    sw t0, 0(a0)                  // the hart's own store to the word: the sc still succeeds (as Spike)
    sc.w t2, t0, (a0)
    bnez t2, fail

    // Ordering on one hart: store, AMO and load to one word.
    li TESTNUM, 6
    addi a0, s0, 28
    li t0, 100
    sw t0, 0(a0)
    amoadd.w t1, t0, (a0)         // sees the store: t1 = 100, word = 200
    lw t2, 0(a0)                  // sees the AMO
    li t3, 200
    bne t2, t3, fail
    bne t1, t0, fail
    RVTEST_PASS
    TEST_PASSFAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 10, 20, 3, 0, 0, 0, 0, 0
ptr:
    .word 0
    .word data + 4
    .word data + 12
RVTEST_DATA_END
