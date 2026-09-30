// Phase 18 CPU-shell start-up for the CPU kernels (docs/cpu.md §7): zero every
// register (Spike's boot ROM leaves values in t0/a0/a1), set the stack at the
// top of the shell memory, seed the SoC register-page words the kernels check
// (the page is plain memory in the shell and in Spike), clear .bss, and run
// main. A kernel ends the run from aster_putc (kernels/aster.h); if main
// returns, the run reports pass.
    .section .text.init, "ax", @progbits
    .globl _start
_start:
    li x1, 0;  li x2, 0;  li x3, 0;  li x4, 0;  li x5, 0;  li x6, 0;  li x7, 0
    li x8, 0;  li x9, 0;  li x10, 0; li x11, 0; li x12, 0; li x13, 0; li x14, 0
    li x15, 0; li x16, 0; li x17, 0; li x18, 0; li x19, 0; li x20, 0; li x21, 0
    li x22, 0; li x23, 0; li x24, 0; li x25, 0; li x26, 0; li x27, 0; li x28, 0
    li x29, 0; li x30, 0; li x31, 0
    la sp, _stack_top
    // Register-page constants the kernels read (software/runtime/aster.h,
    // software/benchmarks/workload_coh.h): performance-counter ABI 2 and the
    // coherent-counter ABI 4; the clock is recorded as the v1 31.25 MHz.
    li t0, 0x20003048
    li t1, 2
    sw t1, 0(t0)
    li t0, 0x2000304c
    li t1, 31250000
    sw t1, 0(t0)
    li t0, 0x20003084
    li t1, 4
    sw t1, 0(t0)
    li t0, 0x20003088
    li t1, 31250000
    sw t1, 0(t0)
    la t0, __bss_start
    la t1, _end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  call main
    li t0, 1
    la t1, tohost
    fence
    sw t0, 0(t1)
3:  j 3b

    .pushsection .tohost, "aw", @progbits
    .align 6
    .globl tohost
tohost: .dword 0
    .align 6
    .globl fromhost
fromhost: .dword 0
    .popsection
