    .option norvc
    .section .text.init
    .globl _start

_start:
    # RAM word store/load.
    lui     t0, 0x10000
    li      t1, 0x11223344
    sw      t1, 0(t0)
    lw      t2, 0(t0)
    bne     t2, t1, fail

    # Byte lane and unsigned byte load.
    li      t3, 0xff
    sb      t3, 4(t0)
    lbu     t4, 4(t0)
    bne     t4, t3, fail

    # RV32M operations are provided by PicoRV32's internal PCPI units.
    li      t1, 7
    li      t2, 9
    mul     t3, t1, t2
    li      t4, 63
    bne     t3, t4, fail
    div     t3, t3, t1
    bne     t3, t2, fail
    rem     t3, t4, t1
    bne     t3, zero, fail

    # A backward branch and an unconditional jump exercise control flow.
    li      t1, 4
countdown:
    addi    t1, t1, -1
    bne     t1, zero, countdown
    jal     zero, pass

fail:
    lui     t0, 0x20000
    la      t1, fail_message
    jal     zero, print_message

pass:
    lui     t0, 0x20000
    la      t1, pass_message

print_message:
    lbu     t2, 0(t1)
    beq     t2, zero, finished
    sw      t2, 0(t0)
    addi    t1, t1, 1
    jal     zero, print_message

finished:
    jal     zero, finished

    .section .rodata
    .align  2
pass_message:
    .asciz  "RV32IM PASS\n"
fail_message:
    .asciz  "RV32IM FAIL\n"
