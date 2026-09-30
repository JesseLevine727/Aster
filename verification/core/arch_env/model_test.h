// riscv-arch-test (3.x test format) model macros for the Phase 18 CPU shell.
// A test ends by storing 1 to tohost, as in the riscv-tests environment; the
// shell and Spike then dump the words from begin_signature to end_signature,
// which must be identical. No console and no interrupt hooks: the I, M, A and
// Zifencei suites need none, and the privilege tests (which need a writable
// mtvec) run from milestone 18.3.
#ifndef ASTER_CORE_SHELL_MODEL_TEST_H
#define ASTER_CORE_SHELL_MODEL_TEST_H

#define ALIGNMENT 2

#define RVMODEL_DATA_SECTION \
    .pushsection .tohost, "aw", @progbits; \
    .align 6; .global tohost; tohost: .dword 0; \
    .align 6; .global fromhost; fromhost: .dword 0; \
    .popsection;

#define RVMODEL_BOOT

#define RVMODEL_HALT \
    fence; li x1, 1; la t2, tohost; sw x1, 0(t2); \
    1: j 1b;

#define RVMODEL_DATA_BEGIN \
    RVMODEL_DATA_SECTION \
    .align ALIGNMENT; .global begin_signature; begin_signature:

#define RVMODEL_DATA_END \
    .align ALIGNMENT; .global end_signature; end_signature:

#define RVMODEL_IO_INIT
#define RVMODEL_IO_WRITE_STR(_R, _STR)
#define RVMODEL_IO_CHECK()
#define RVMODEL_IO_ASSERT_GPR_EQ(_S, _R, _I)
#define RVMODEL_IO_ASSERT_SFPR_EQ(_F, _R, _I)
#define RVMODEL_IO_ASSERT_DFPR_EQ(_D, _R, _I)

#define RVMODEL_SET_MSW_INT
#define RVMODEL_CLEAR_MSW_INT
#define RVMODEL_CLEAR_MTIMER_INT
#define RVMODEL_CLEAR_MEXT_INT

#endif
