// Directed trap test (18.3): instruction access faults — right-path fetches
// outside memory, answered with i_rsp_error, which trap at the commit point
// (mcause 1, mepc and mtval the fetch address; the faulting "instruction" has
// no encoding). The fetch outside memory is reached four ways: a `jalr`
// (redirected from Execute), a `jal` (redirected from Decode), a taken
// forward branch (redirected from Execute), and running sequentially off the
// end of memory (the `.tail` code is the last thing in memory: directed/
// link.ld; see the page alignment below). The handler resumes at s11 each time.
#include "trap_test.h"

RVTEST_RV32M
RVTEST_CODE_BEGIN
    li TESTNUM, 2
    li s11, 0

    // jalr outside memory.
    la s11, 1f
    li t0, 0x40000000
    jalr t0
    j fail
1:
    // jal past the end of memory (within its reach).
    la s11, 2f
    jal ra, shell_memory_end + 0x100
    j fail
2:
    // A taken forward branch past the end, from the last words of memory.
    la s11, 3f
    j tail_branch
3:
    // Running off the end of memory.
    la s11, 4f
    j tail_run
4:
    li TESTNUM, 3
    EXPECT_TRAPS(4)
    EXPECT_TRAP(0, CAUSE_FETCH_ACCESS, 0x40000000, 0x40000000)
    EXPECT_TRAP_AT(1, CAUSE_FETCH_ACCESS, shell_memory_end + 0x100, shell_memory_end, 0x100)
    EXPECT_TRAP_AT(2, CAUSE_FETCH_ACCESS, shell_memory_end + 0x40, shell_memory_end, 0x40)
    EXPECT_TRAP_AT(3, CAUSE_FETCH_ACCESS, shell_memory_end, shell_memory_end, 0)
    RVTEST_PASS
    TEST_PASSFAIL
    TRAP_HANDLER
RVTEST_CODE_END

    // The tail code ends on a page boundary, so the shell's memory and Spike's
    // (whose -m region is rounded up to whole 4 KiB pages) end at the same
    // address: .tail starts page-aligned and its code fills its last bytes.
    .section .tail, "ax", @progbits
    .option norelax               // keep .balign out of the linker's relaxation
    .balign 4096
    .fill (4096 - 16) / 4, 4, 0
tail_branch:
    beq x0, x0, . + 0x50          // shell_memory_end + 0x40 (a relative target: a plain branch)
    j fail
tail_run:
    li a0, 1
    nop                           // the last word of memory: the next fetch faults

    .data
RVTEST_DATA_BEGIN
    TEST_DATA
    TRAP_DATA
RVTEST_DATA_END
