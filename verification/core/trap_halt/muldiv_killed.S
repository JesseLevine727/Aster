// Trap-halt test (milestone 18.2, before traps are taken in 18.3): a load the
// memory answers with d_rsp_error (+bus_error_traps), behind a valid load,
// followed by a division and a multiply. The division enters Execute, and
// starts, while the faulting load is in M1; when the valid load's answer is
// late (under back-pressure) the faulting load waits in M1 and the division
// runs before the trap kills it at the commit point. The divider must stop
// with it (the RTL asserts that it never runs without a division in Execute).
// The load must stop the core with a trap record at trap_pc; the records
// before it must match Spike, and the stores after it must never reach
// memory. Expected status: TRAP (make core-aster-tests, with +bus_error_traps).
#include "riscv_test.h"
#include "test_macros.h"

RVTEST_RV32U
RVTEST_CODE_BEGIN
    li TESTNUM, 1
    la t2, data
    li t3, 0x5a5a5a5a
    li t0, 0x10000000
    li a1, 100
    li a2, 3
    lw t4, 0(t2)                  // a valid load, answered late under back-pressure
    .globl trap_pc
trap_pc:
    lw t1, 0(t0)
    div s2, a1, a2                // younger than the trap: killed, while dividing when the load waits
    mul s3, a1, a2
    sw t3, 0(t2)                  // must not reach memory
    sw t3, 4(t2)
    RVTEST_PASS
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
data:
    .word 0x11111111, 0x22222222
RVTEST_DATA_END
