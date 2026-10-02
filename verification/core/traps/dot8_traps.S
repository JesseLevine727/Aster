// Directed trap test (18.5, docs/cpu.md §2): every custom-0 encoding other
// than dot8's — the 1,023 with (funct7, funct3) != (0, 0), PicoRV32's
// retirq/maskirq/waitirq (funct7 2-4) among them — and custom-1, -2 and -3
// are illegal instructions, as v1's probe matrix requires (verification/unit/
// tb_aster_dot8_probe.cpp). In lockstep with Spike (each trap record: cause,
// mepc, mtval; no register written) and self-checking: the handler counts the
// traps, requires mcause 2 and mtval equal to the instruction at mepc, and
// resumes after it; a dot8 after them still computes.
#include "riscv_test.h"
#include "test_macros.h"

// A custom opcode's R-type word with varied register fields.
#define CUSTOM(opcode, f3, f7) \
    .word (opcode) | ((f3) << 12) | ((f7) << 25) | ((((f7) + (f3)) & 31) << 7) \
          | ((((f7) * 5) & 31) << 15) | ((((f3) * 7 + (f7)) & 31) << 20)

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    li s10, 0                     // traps
    li s9, 0                      // traps whose cause or mtval was wrong
    .irp f7, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,125,126,127
        .irp f3, 0,1,2,3,4,5,6,7
            .if (\f7 != 0) || (\f3 != 0)
                CUSTOM(0x0b, \f3, \f7)
            .endif
        .endr
    .endr
    .irp opcode, 0x2b, 0x5b, 0x7b
        .irp f3, 0,1,2,3,4,5,6,7
            CUSTOM(\opcode, \f3, 0)
        .endr
        CUSTOM(\opcode, 0, 1)
        CUSTOM(\opcode, 0, 127)
    .endr
    li t0, 1023 + 3 * 10
    bne s10, t0, fail
    bnez s9, fail
    li TESTNUM, 3
    li a0, 0x01010101
    .insn r 0x0b, 0, 0, a1, a0, a0
    li t0, 4
    bne a1, t0, fail
    RVTEST_PASS
    TEST_PASSFAIL

    .align 2
    .global mtvec_handler
mtvec_handler:
    csrrw sp, mscratch, sp
    sw t0, 0(sp)
    sw t1, 4(sp)
    addi s10, s10, 1
    csrr t0, mcause
    li t1, CAUSE_ILLEGAL_INSTRUCTION
    bne t0, t1, 1f
    csrr t0, mepc
    lw t0, 0(t0)
    csrr t1, mtval
    beq t0, t1, 2f
1:  addi s9, s9, 1
2:  csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    lw t1, 4(sp)
    lw t0, 0(sp)
    csrrw sp, mscratch, sp
    mret
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
RVTEST_DATA_END
