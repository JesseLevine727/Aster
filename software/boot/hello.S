    .option norvc
    .section .text.init
    .globl _start

_start:
    # UART_TX is the write-only transmit register at 0x20000000.
    lui     t0, 0x20000
    la      t1, message

print_loop:
    lbu     t2, 0(t1)
    beq     t2, zero, finished
    sw      t2, 0(t0)
    addi    t1, t1, 1
    jal     zero, print_loop

finished:
    jal     zero, finished

    .section .rodata
    .align  2
message:
    .asciz  "Hello from Aster\n"
