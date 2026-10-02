// Self-checking program (18.5), run in the shell alone in every memory mode:
// its 2.6 million instructions would make a 270 MB Spike log per run (it was
// run once in lockstep, every instruction matching Spike: docs/phase18.md,
// "Milestone 18.5"). Xasterdot8's arithmetic against a reference computed with
// mul (itself checked in lockstep) — v1's unit test
// (verification/unit/tb_aster_pcpi_dot8.cpp) on the Aster core:
// - every pair of signed bytes in every lane, the other lanes 0 (4 x 65,536);
// - every pair of 12 corner words (lanes of 0, 1, -1, 127, -128 and mixes);
// - 6,000 pseudo-random pairs of words.
// A dot8's result is read at distance 2 and 3 (behind a multiply), and by the
// reference's comparison; lockstep programs cover the other distances.
#include "riscv_test.h"
#include "test_macros.h"

#define DOT8(rd, rs1, rs2) .insn r 0x0b, 0, 0, rd, rs1, rs2

RVTEST_RV32U
RVTEST_CODE_BEGIN
    // Every byte pair in every lane.
    li TESTNUM, 2
    li s0, 0                      // the lane's shift: 0, 8, 16, 24
    li s3, 128
    li s4, 32
1:  li s1, -128                   // x
2:  li s2, -128                   // y
3:  andi t0, s1, 0xff
    sll t0, t0, s0
    andi t1, s2, 0xff
    sll t1, t1, s0
    DOT8(t2, t0, t1)
    mul t3, s1, s2
    bne t2, t3, fail
    addi s2, s2, 1
    blt s2, s3, 3b
    addi s1, s1, 1
    blt s1, s3, 2b
    addi s0, s0, 8
    blt s0, s4, 1b

    // Every pair of corner words, against the four lanes' products summed.
    li TESTNUM, 3
    la s5, corners
    li s6, 0                      // i
4:  li s7, 0                      // j
5:  slli t0, s6, 2
    add t0, s5, t0
    lw a0, 0(t0)
    slli t0, s7, 2
    add t0, s5, t0
    lw a1, 0(t0)
    DOT8(a2, a0, a1)
    jal ra, reference
    bne a2, a3, fail
    addi s7, s7, 1
    li t0, 12
    blt s7, t0, 5b
    addi s6, s6, 1
    blt s6, t0, 4b

    // Pseudo-random words (a linear congruential sequence).
    li TESTNUM, 4
    li s6, 6000
    li s8, 0x13570000             // the state
    li s9, 1664525
    li s10, 1013904223
6:  mul s8, s8, s9
    add s8, s8, s10
    mv a0, s8
    mul s8, s8, s9
    add s8, s8, s10
    mv a1, s8
    DOT8(a2, a0, a1)
    jal ra, reference
    bne a2, a3, fail
    addi s6, s6, -1
    bnez s6, 6b
    RVTEST_PASS
    TEST_PASSFAIL

// a3 = the sum over the four lanes of a0's and a1's signed bytes' products
// (uses t0-t2, t4).
reference:
    li a3, 0
    li t4, 24                     // the left shift that brings the lane to the top
7:  sll t0, a0, t4
    srai t0, t0, 24
    sll t1, a1, t4
    srai t1, t1, 24
    mul t2, t0, t1
    add a3, a3, t2
    addi t4, t4, -8
    bgez t4, 7b
    ret
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
corners:
    .word 0x00000000, 0x01010101, 0xFFFFFFFF, 0x7F7F7F7F, 0x80808080, 0x807F807F
    .word 0x7F80FF01, 0x01FF7F80, 0x80000000, 0x0000007F, 0xFF00FF00, 0x12345678
RVTEST_DATA_END
