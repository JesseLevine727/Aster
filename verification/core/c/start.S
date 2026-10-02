// Phase 18 CPU-shell start-up for the C tests (verification/core/c, 18.5): zero
// every register (Spike's boot ROM leaves values in t0/a0/a1), set the stack,
// run main, and report through tohost — 1 when main returns 0, else
// (code << 1) | 1, as the riscv-tests do. Linked with the directed tests'
// layout (verification/core/directed/link.ld); .bss is zero in the shell's
// memory and in Spike's.
    .section .text.init, "ax", @progbits
    .globl _start
_start:
    li x1, 0;  li x2, 0;  li x3, 0;  li x4, 0;  li x5, 0;  li x6, 0;  li x7, 0
    li x8, 0;  li x9, 0;  li x10, 0; li x11, 0; li x12, 0; li x13, 0; li x14, 0
    li x15, 0; li x16, 0; li x17, 0; li x18, 0; li x19, 0; li x20, 0; li x21, 0
    li x22, 0; li x23, 0; li x24, 0; li x25, 0; li x26, 0; li x27, 0; li x28, 0
    li x29, 0; li x30, 0; li x31, 0
    la sp, c_stack_top
    call main
    li t0, 1
    beqz a0, 1f
    slli t0, a0, 1
    ori t0, t0, 1
1:  la t1, tohost
    sw t0, 0(t1)
2:  j 2b

    .pushsection .tohost, "aw", @progbits
    .align 6; .globl tohost; tohost: .dword 0; .size tohost, 8
    .align 6; .globl fromhost; fromhost: .dword 0; .size fromhost, 8
    .popsection

    .bss
    .align 4
    .space 4096
c_stack_top:
