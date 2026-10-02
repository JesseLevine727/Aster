// Directed interrupt tests (milestone 18.3, docs/cpu.md §3-§4, §6.3):
// self-checking programs run in the shell alone (+irq_device; Spike cannot
// raise the same interrupts at the same instructions), each declaring the
// interrupts it must take (shell_expect_interrupts).
//
// The shell's interrupt device (tb_core_ports.cpp) is a word at 0x3000_0000: a
// store sets MEIP, MTIP and MSIP to its bits 11, 7 and 3 (mip's layout),
// `value >> 16` cycles after the cycle following its acceptance; a load reads
// them. IRQ_RAISE writes it.
//
// The handler (mtvec_handler) appends {mcause, mepc, mtval} to irq_log (64
// entries, then it wraps) and counts in irq_count. For an interrupt it clears that
// line (the line's mip bit is the interrupt's code) and returns to mepc; for
// an exception it returns past the instruction. It preserves every register,
// using the environment's save area (mscratch).
#ifndef ASTER_IRQ_TEST_H
#define ASTER_IRQ_TEST_H

#include "riscv_test.h"
#include "test_macros.h"

#define IRQ_DEVICE 0x30000000
#define IRQ_RAISE(lines, delay) \
    li t0, IRQ_DEVICE; li t1, ((delay) << 16) | (lines); sw t1, 0(t0);

#define IRQ_HANDLER \
    .align 2; .global mtvec_handler; \
mtvec_handler: \
    csrrw sp, mscratch, sp; \
    sw t0, 0(sp); sw t1, 4(sp); sw t2, 8(sp); \
    la t0, irq_count; lw t1, 0(t0); addi t2, t1, 1; sw t2, 0(t0); \
    andi t1, t1, 63; slli t1, t1, 4; la t2, irq_log; add t1, t1, t2; \
    csrr t2, mcause; sw t2, 0(t1); \
    csrr t2, mepc; sw t2, 4(t1); \
    csrr t2, mtval; sw t2, 8(t1); \
    csrr t2, mcause; bgez t2, 2f; \
    andi t2, t2, 31; li t1, 1; sll t1, t1, t2; not t1, t1; \
    li t0, IRQ_DEVICE; lw t2, 0(t0); and t2, t2, t1; sw t2, 0(t0); \
    j 3f; \
2:  csrr t2, mepc; addi t2, t2, 4; csrw mepc, t2; \
3:  lw t2, 8(sp); lw t1, 4(sp); lw t0, 0(sp); \
    csrrw sp, mscratch, sp; \
    mret;

#define IRQ_DATA \
    .align 4; irq_count: .word 0; \
    .align 4; irq_log: .fill 256, 4, 0;

// Wait until irq_count reaches n (uses t0, t1).
#define IRQ_WAIT(n) \
    la t0, irq_count; 9: lw t1, 0(t0); li t2, n; bltu t1, t2, 9b;

// Log entry k (mod 64) was mcause `cause` at mepc `label`, and an interrupt
// left mtval zero (uses t0-t2).
#define EXPECT_IRQ(k, cause, label) \
    la t0, irq_log + 16 * ((k) % 64); \
    lw t1, 0(t0); li t2, cause; bne t1, t2, fail; \
    lw t1, 4(t0); la t2, label; bne t1, t2, fail; \
    lw t1, 0(t0); bgez t1, 8f; lw t1, 8(t0); bnez t1, fail; 8:

#define IRQ_MEI 0x8000000b
#define IRQ_MSI 0x80000003
#define IRQ_MTI 0x80000007

#endif
