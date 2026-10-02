// Directed test (18.5): Xasterdot8's dot8 in lockstep with Spike (its aster_dot8
// extension) and self-checking — the v1 probe's ISA cases (verification/unit/
// tb_aster_dot8_probe.cpp) on the Aster core, with the pipeline's own:
// - operands forwarded from an ALU result, a load (load-use), a multiply, a
//   division, an AMO, lr and a link value, at distance 1;
// - its result (from M2: Decode waits one cycle at distance 1) forwarded to an
//   ALU (also making an address), a branch, a store's data, a multiply and the
//   next dot8's operands (back-to-back chains, rd = rs1 and rd = rs2) at
//   distances 1-4;
// - a dot8 behind a load whose answer M2 waits for (under back-pressure the
//   dot8 waits in M1 and its reader in Execute);
// - rd = x0, rs1 = x0, rs2 = x0, rd = rs1 = rs2;
// - lr.w, dot8, sc.w: the reservation survives the dot8 (v1's probe);
// - the corners: -128 x -128 in every lane (65,536, which needs 18 bits), 127
//   x -128, a mix, and the sign extension of a negative sum.
// (traps/csr_ordering checks that mie's bit 12 and mstatus.XS stay 0, which
// Spike makes writable for any custom extension.)
#include "riscv_test.h"
#include "test_macros.h"

#define DOT8(rd, rs1, rs2) .insn r 0x0b, 0, 0, rd, rs1, rs2

RVTEST_RV32U
RVTEST_CODE_BEGIN
    la s0, data
    li TESTNUM, 2

    // Corners.
    li a0, 0x80808080
    DOT8(a1, a0, a0)              // 4 x 16384 = 65536
    li t0, 0x10000
    bne a1, t0, fail
    li a2, 0x7f7f7f7f
    DOT8(a3, a2, a0)              // 4 x -16256 = -65024
    li t0, -65024
    bne a3, t0, fail
    li a4, 0x01ff7f80             // lanes (low first) -128, 127, -1, 1
    li a5, 0x02030405             // 5, 4, 3, 2
    DOT8(a6, a4, a5)              // -640 + 508 - 3 + 2 = -133
    li t0, -133
    bne a6, t0, fail

    // Registers: rd = x0 writes nothing; x0 operands read 0; rd = rs1 = rs2.
    li TESTNUM, 3
    li a1, 0x12345678
    DOT8(x0, a1, a1)
    DOT8(a2, x0, a1)
    bnez a2, fail
    DOT8(a2, a1, x0)
    bnez a2, fail
    li a3, 0x02020202
    DOT8(a3, a3, a3)              // 4 x 4 = 16
    li t0, 16
    bne a3, t0, fail

    // Operands forwarded at distance 1.
    li TESTNUM, 4
    li t1, 0x01010101
    addi a0, t1, 0x101            // ALU: 0x01010202
    DOT8(a1, a0, t1)              // 2 + 2 + 1 + 1 = 6
    li t0, 6
    bne a1, t0, fail
    lw a2, 0(s0)                  // load-use: 0x03030303
    DOT8(a3, a2, t1)              // 12
    li t0, 12
    bne a3, t0, fail
    li t2, 3
    mul a4, t1, t2                // 0x03030303
    DOT8(a5, t1, a4)              // 12
    bne a5, t0, fail
    li t3, 0x06060606
    div a6, t3, t2                // 0x02020202
    DOT8(a7, a6, t1)              // 8
    li t0, 8
    bne a7, t0, fail
    addi s1, s0, 4
    amoadd.w a0, x0, (s1)         // 0x04040404, unchanged
    DOT8(a1, a0, t1)              // 16
    li t0, 16
    bne a1, t0, fail
    lr.w a2, (s1)
    DOT8(a3, t1, a2)              // 16: the reservation survives the dot8
    sc.w a4, a2, (s1)
    bnez a4, fail
    bne a3, t0, fail
    jal a5, 1f                    // a link value as an operand
1:  DOT8(a6, a5, x0)
    bnez a6, fail

    // The result forwarded at distances 1 to 4, to every consumer.
    li TESTNUM, 5
    li a0, 0x01010101
    li a1, 0x02020202
    DOT8(a2, a0, a1)              // 8
    addi a3, a2, 1                // ALU, distance 1 (Decode waits one cycle)
    li t0, 9
    bne a3, t0, fail
    DOT8(a2, a0, a1)
    bne a2, a3, 1f                // branch, distance 1 (8 != 9: taken)
    j fail
1:  DOT8(a2, a0, a1)
    sw a2, 8(s0)                  // store data, distance 1
    lw t1, 8(s0)
    li t0, 8
    bne t1, t0, fail
    DOT8(a2, a0, a1)
    mul a4, a2, a2                // multiply, distance 1
    li t0, 64
    bne a4, t0, fail
    DOT8(a2, a0, a1)              // 8: an offset making a load's address (s0 + 8)
    add a5, s0, a2                // distance 1
    lw a6, 0(a5)
    li t0, 8
    bne a6, t0, fail
    DOT8(a2, a0, a1)
    nop
    addi a3, a2, 2                // distance 2
    li t0, 10
    bne a3, t0, fail
    DOT8(a2, a0, a1)
    nop
    nop
    addi a3, a2, 3                // distance 3
    li t0, 11
    bne a3, t0, fail
    DOT8(a2, a0, a1)
    nop
    nop
    nop
    addi a3, a2, 4                // distance 4
    li t0, 12
    bne a3, t0, fail

    // Back-to-back chains: rd = rs1, rd = rs2.
    li TESTNUM, 6
    li a0, 0x00000002
    li a1, 0x00000003
    DOT8(a0, a0, a1)              // 6
    DOT8(a0, a0, a1)              // 18
    DOT8(a0, a1, a0)              // 54
    DOT8(a0, a0, a0)              // 54 x 54 = 2916 (one lane)
    li t0, 2916
    bne a0, t0, fail
    DOT8(a0, a0, a0)              // 2916 = 0x0b64, lanes 100 and 11: 10000 + 121
    li t0, 10121
    bne a0, t0, fail

    // Behind a load the memory may answer late: the dot8 then waits in M1, and
    // its reader in Execute.
    li TESTNUM, 7
    li s2, 8
    li a1, 0x01010101
1:  lw a2, 0(s0)                  // 0x03030303
    DOT8(a3, a1, a1)              // 4
    add a4, a3, a3                // 8
    DOT8(a5, a2, a1)              // 12
    sub a6, a5, a4                // 4
    li t0, 4
    bne a6, t0, fail
    addi s2, s2, -1
    bnez s2, 1b


    RVTEST_PASS
    TEST_PASSFAIL
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x03030303, 0x04040404, 0
RVTEST_DATA_END
