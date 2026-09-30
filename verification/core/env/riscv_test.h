// Test environment for the Phase 18 CPU shell and Spike lockstep.
// Programs start at _start (0x8000_0000), zero every register so the DUT and
// Spike (whose boot ROM leaves values in t0/a0/a1) begin from the same state,
// and report through the riscv-tests tohost convention: 1 = pass,
// (TESTNUM << 1) | 1 = fail. No CSR is touched, so the same image runs on
// PicoRV32 (no Zicsr) and on the Aster core before its Zicsr milestone. The
// machine-mode tests (RVTEST_RV32M, rv32mi) need a trap vector and are not
// supported by this environment; building one fails on the missing macro.
#ifndef ASTER_CORE_SHELL_ENV_H
#define ASTER_CORE_SHELL_ENV_H

#define TESTNUM gp
#define RVTEST_RV32U
#define RVTEST_RV64U

#define ASTER_ZERO_XREGS \
    li x1, 0;  li x2, 0;  li x3, 0;  li x4, 0;  li x5, 0;  li x6, 0;  li x7, 0; \
    li x8, 0;  li x9, 0;  li x10, 0; li x11, 0; li x12, 0; li x13, 0; li x14, 0; \
    li x15, 0; li x16, 0; li x17, 0; li x18, 0; li x19, 0; li x20, 0; li x21, 0; \
    li x22, 0; li x23, 0; li x24, 0; li x25, 0; li x26, 0; li x27, 0; li x28, 0; \
    li x29, 0; li x30, 0; li x31, 0;

#define RVTEST_CODE_BEGIN \
    .section .text.init, "ax", @progbits; \
    .align 2; .globl _start; _start: \
    ASTER_ZERO_XREGS

#define RVTEST_CODE_END \
    unimp;

#define ASTER_REPORT(value_reg) \
    fence; la t6, tohost; sw value_reg, 0(t6); \
    1: j 1b;

#define RVTEST_PASS \
    li TESTNUM, 1; ASTER_REPORT(TESTNUM)

// As upstream: a failure before any test case ran (TESTNUM still 0) never
// reports, so it cannot look like a pass; the shell times out instead.
#define RVTEST_FAIL \
    1: beqz TESTNUM, 1b; \
    sll TESTNUM, TESTNUM, 1; or TESTNUM, TESTNUM, 1; ASTER_REPORT(TESTNUM)

#define RVTEST_DATA_BEGIN \
    .pushsection .tohost, "aw", @progbits; \
    .align 6; .globl tohost; tohost: .dword 0; .size tohost, 8; \
    .align 6; .globl fromhost; fromhost: .dword 0; .size fromhost, 8; \
    .popsection; \
    .align 4; .globl begin_signature; begin_signature:

#define RVTEST_DATA_END \
    .align 4; .globl end_signature; end_signature:

#endif
