// Milestone 19.4: the NPU's register page takes only word loads and word
// stores (npu.md §3); in the Phase 19 SoC the data cache's word-only I/O
// window (aster_l1d's IO_WORD_ONLY) makes any other access an access fault
// with mtval the address, decided before the access leaves the cache. A byte
// and a halfword load, a byte and a halfword store, lr, sc and an AMO on the
// page each trap (load access fault for the loads and lr; store/AMO access
// fault for the others), and none reaches the NPU: the registers keep their
// values (A_BASE, written before, is read back), the destination registers
// theirs, and the NPU never starts (a byte store to CONTROL would be START).
// Then a word store and load of A_BASE do reach it. The stores would show in
// the registers had they got through; the loads and atomics would only read,
// so for them the proof is the traps, and the SoC's assertion that only word
// loads and stores reach the NPU's register port. Built with the trap tests'
// environment (verification/core/traps/trap_test.h).
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    li s11, 0
    li s1, 0x40000000             // the NPU's register page
    li s2, 0x80001234
    sw s2, 0x10(s1)               // A_BASE (a word store: accepted)
    li a3, 0x77
t0_insn:
    lb a3, 0x10(s1)
t1_insn:
    lhu a3, 0x12(s1)
t2_insn:
    sb a3, 0x000(s1)              // CONTROL's START bit, if it got through
t3_insn:
    sh a3, 0x10(s1)
t4_insn:
    lr.w a3, (s1)
t5_insn:
    sc.w a3, s2, (s1)
t6_insn:
    amoor.w a3, s2, (s1)
    li t1, 0x77
    bne a3, t1, fail

    li TESTNUM, 3
    EXPECT_TRAPS(7)
    EXPECT_TRAP(0, CAUSE_LOAD_ACCESS, t0_insn, 0x40000010)
    EXPECT_TRAP(1, CAUSE_LOAD_ACCESS, t1_insn, 0x40000012)
    EXPECT_TRAP(2, CAUSE_STORE_ACCESS, t2_insn, 0x40000000)
    EXPECT_TRAP(3, CAUSE_STORE_ACCESS, t3_insn, 0x40000010)
    EXPECT_TRAP(4, CAUSE_LOAD_ACCESS, t4_insn, 0x40000000)
    EXPECT_TRAP(5, CAUSE_STORE_ACCESS, t5_insn, 0x40000000)
    EXPECT_TRAP(6, CAUSE_STORE_ACCESS, t6_insn, 0x40000000)

    li TESTNUM, 4
    lw t1, 0x10(s1)               // A_BASE kept its value
    bne t1, s2, fail
    lw t1, 0x4(s1)                // STATUS: idle (no job started)
    bnez t1, fail
    li t2, 0x80005678
    sw t2, 0x10(s1)
    lw t1, 0x10(s1)
    bne t1, t2, fail
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER
RVTEST_CODE_END

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    TRAP_DATA
RVTEST_DATA_END
