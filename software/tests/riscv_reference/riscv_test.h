// Owned bare-metal environment for the unmodified pinned RV32UA programs.
// No privileged CSRs, traps, OS or tohost device is implied by this adapter.
#ifndef ASTER_RISCV_REFERENCE_ENV_H
#define ASTER_RISCV_REFERENCE_ENV_H
#define TESTNUM gp
#define RVTEST_RV32U
#define RVTEST_RV64U
#define RVTEST_CODE_BEGIN \
    .section .text.reference,"ax",@progbits; \
    .align 2; .globl aster_reference_entry; aster_reference_entry:
#define RVTEST_CODE_END \
    .globl aster_reference_end; aster_reference_end:
#define RVTEST_PASS li a0, 1; j aster_reference_report;
#define RVTEST_FAIL slli a0, TESTNUM, 1; ori a0, a0, 1; j aster_reference_report;
#define RVTEST_DATA_BEGIN .align 4; .globl begin_signature; begin_signature:
#define RVTEST_DATA_END .align 4; .globl end_signature; end_signature:
#endif
