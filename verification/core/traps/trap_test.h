// Directed trap tests (milestone 18.3, docs/cpu.md §3-§4, §6.3): shared
// handler and checks. Each program raises exceptions on purpose and runs in
// lockstep with Spike (every record, trap records included, and the signature
// region, which holds the trap log); it also checks the log itself.
//
// The handler (the program's mtvec_handler, installed by the environment)
// appends {mcause, mepc, mtval, mstatus} to trap_log and resumes after the
// trapping instruction (mepc + 4) — or at s11 if s11 is nonzero, clearing it
// (after a fetch fault, or to leave a sequence early). It preserves every
// register but s11, using the environment's save area (mscratch).
#ifndef ASTER_TRAP_TEST_H
#define ASTER_TRAP_TEST_H

#include "riscv_test.h"
#include "test_macros.h"

#define TRAP_HANDLER \
    .align 2; .global mtvec_handler; \
mtvec_handler: \
    csrrw sp, mscratch, sp; \
    sw t0, 0(sp); sw t1, 4(sp); sw t2, 8(sp); \
    la t0, trap_count; lw t1, 0(t0); addi t2, t1, 1; sw t2, 0(t0); \
    slli t2, t1, 4; la t1, trap_log; add t1, t1, t2; \
    csrr t2, mcause; sw t2, 0(t1); \
    csrr t2, mepc; sw t2, 4(t1); \
    csrr t2, mtval; sw t2, 8(t1); \
    csrr t2, mstatus; sw t2, 12(t1); \
    csrr t2, mepc; addi t2, t2, 4; \
    beqz s11, 1f; mv t2, s11; li s11, 0; \
1:  csrw mepc, t2; \
    lw t2, 8(sp); lw t1, 4(sp); lw t0, 0(sp); \
    csrrw sp, mscratch, sp; \
    mret;

#define TRAP_DATA \
    .align 4; trap_count: .word 0; \
    .align 4; trap_log: .fill 256, 4, 0;            /* 64 entries */

// The number of traps so far must be n (uses t0-t2).
#define EXPECT_TRAPS(n) \
    la t0, trap_count; lw t1, 0(t0); li t2, n; bne t1, t2, fail;

// Trap n (from 0) was `cause` at the label `epc`, with mtval `tval` (a
// constant: EXPECT_TRAP) or the address of a label (EXPECT_TRAP_AT); uses t0-t2.
#define EXPECT_TRAP(n, cause, epc, tval) \
    la t0, trap_log + 16 * (n); \
    lw t1, 0(t0); li t2, cause; bne t1, t2, fail; \
    lw t1, 4(t0); la t2, epc;   bne t1, t2, fail; \
    lw t1, 8(t0); li t2, tval;  bne t1, t2, fail;
#define EXPECT_TRAP_AT(n, cause, epc, tval_label, offset) \
    la t0, trap_log + 16 * (n); \
    lw t1, 0(t0); li t2, cause; bne t1, t2, fail; \
    lw t1, 4(t0); la t2, epc;   bne t1, t2, fail; \
    lw t1, 8(t0); la t2, tval_label; addi t2, t2, offset; bne t1, t2, fail;

#endif
