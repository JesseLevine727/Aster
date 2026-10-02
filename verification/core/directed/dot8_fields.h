// Directed tests dot8_fields_0-3 (18.5): dot8 with every (rd, rs1, rs2)
// register-field combination — 32,768, as v1's probe matrix (verification/
// unit/tb_aster_dot8_probe.cpp) runs them — in lockstep with Spike, eight rd
// values per program (the whole matrix exceeds the shell's 96 KiB). Before
// each rd's 1,024 combinations, x1-x31 are loaded with distinct words (a lane
// of -128 or 127 in some); rd then takes each result in turn, so every
// combination reading rd reads the previous dot8's result at distance 1 (x0
// as rd writes nothing and reads 0).
#ifndef ASTER_DOT8_FIELDS_H
#define ASTER_DOT8_FIELDS_H

#include "riscv_test.h"

.macro load_all g
    .irp r, 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
        li x\r, ((\g * 0x9E3779B1 + \r * 0x85EBCA77) ^ ((\r & 3) * 0x7F800080)) & 0xFFFFFFFF
    .endr
.endm

.macro rd_block rd
    load_all \rd
    .irp a, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
        .irp b, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
            .insn r 0x0b, 0, 0, x\rd, x\a, x\b
        .endr
    .endr
.endm

#define DOT8_FIELDS_PROGRAM(...) \
    RVTEST_RV32U; \
    RVTEST_CODE_BEGIN; \
    .irp d, __VA_ARGS__; rd_block \d; .endr; \
    RVTEST_PASS; \
    RVTEST_CODE_END; \
    .data; \
    RVTEST_DATA_BEGIN; \
    RVTEST_DATA_END

#endif
