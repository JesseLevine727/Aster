// Trap-halt test (milestone 18.1, before traps are taken in 18.3): a load the
// memory answers with d_rsp_error (+bus_error_traps) right behind a valid load,
// so that under back-pressure the valid load waits in M2 for a late answer while
// the errored one waits in M1: the error, sampled in the cycle after acceptance,
// must be held until the access commits (docs/cpu.md §4). The load must stop
// the core at the commit point with a trap record at trap_pc; the records
// before it must match Spike, and the stores after it must never reach memory
// (the shell fails an unmatched write). Expected status: TRAP
// (make core-aster-tests, with +bus_error_traps).
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 1
    la t2, data
    li t3, 0x5a5a5a5a
    li t0, 0x10000000
    lw t4, 0(t2)                  // a valid load, answered late under back-pressure
    .globl trap_pc
trap_pc:
    lw t1, 0(t0)
    sw t3, 0(t2)                  // younger than the trap: must not reach memory
    sw t3, 4(t2)
    RVTEST_PASS
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x11111111, 0x22222222
RVTEST_DATA_END
