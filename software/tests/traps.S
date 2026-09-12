    .option norvc
    .section .text.init
    .globl _start
_start:
    lui t0, 0x20000
    la t1, armed
1:
    lbu t2, 0(t1)
    beq t2, zero, 2f
    sw t2, 0(t0)
    addi t1, t1, 1
    j 1b
2:
    lui t0, 0x10000
#if TRAP_CASE == 0
    .word 0xffffffff
#elif TRAP_CASE == 1
    ecall
#elif TRAP_CASE == 2
    ebreak
#elif TRAP_CASE == 3
    lw t1, 1(t0)
#elif TRAP_CASE == 4
    lh t1, 1(t0)
#elif TRAP_CASE == 5
    sw t1, 2(t0)
#elif TRAP_CASE == 6
    sh t1, 1(t0)
#elif TRAP_CASE == 7
    jalr zero, 2(t0)
#elif TRAP_CASE == 8
    .word 0x0020006f  // jal x0,+2
#elif TRAP_CASE == 9
    .word 0x00000163  // beq x0,x0,+2
#elif TRAP_CASE == 10
    li t0, 0x20000004 // MMIO is not executable
    jalr zero, 0(t0)
#elif TRAP_CASE == 11
    li t0, 0x10010000 // first unmapped word past RAM
    jalr zero, 0(t0)
#else
#error Unknown TRAP_CASE
#endif
    // If the intended operation failed to trap, never produce a later trap.
3:
    j 3b
    .section .rodata
armed: .asciz "TRAP ARMED\n"
