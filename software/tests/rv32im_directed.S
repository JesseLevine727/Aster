    .option norvc
    .option norelax
    .section .text.init
    .globl _start

    .macro CHECK_R op, lhs, rhs, want
    addi s0, s0, 1
    li t0, \lhs
    li t1, \rhs
    \op t2, t0, t1
    li t3, \want
    bne t2, t3, fail
    .endm

    .macro CHECK_ALIAS op, lhs, rhs, want
    addi s0, s0, 1
    li t0, \lhs
    li t1, \rhs
    \op t0, t0, t1
    li t3, \want
    bne t0, t3, fail
    li t0, \lhs
    li t1, \rhs
    \op t1, t0, t1
    bne t1, t3, fail
    .endm

    .macro CHECK_I op, lhs, imm, want
    addi s0, s0, 1
    li t0, \lhs
    \op t2, t0, \imm
    li t3, \want
    bne t2, t3, fail
    .endm

    .macro CHECK_BRANCH op, lhs, rhs, want
    addi s0, s0, 1
    li t0, \lhs
    li t1, \rhs
    li t2, 0
    \op t0, t1, .Ltaken\@
    j .Ldone\@
.Ltaken\@:
    li t2, 1
.Ldone\@:
    li t3, \want
    bne t2, t3, fail
    .endm

    .macro CHECK_LOAD op, offset, want
    addi s0, s0, 1
    \op t2, \offset(t0)
    li t3, \want
    bne t2, t3, fail
    .endm

_start:
    li s0, 0
    .include "rv32im_vectors.inc"
    lui t0, 0x10000
    li t1, 0x80ff7f01
    sw t1, 0(t0)
    CHECK_LOAD lw, 0, 0x80ff7f01
    CHECK_LOAD lb, 0, 1
    CHECK_LOAD lb, 1, 127
    CHECK_LOAD lb, 2, -1
    CHECK_LOAD lb, 3, -128
    CHECK_LOAD lbu, 0, 1
    CHECK_LOAD lbu, 1, 127
    CHECK_LOAD lbu, 2, 255
    CHECK_LOAD lbu, 3, 128
    CHECK_LOAD lh, 0, 0x7f01
    CHECK_LOAD lh, 2, -32513
    CHECK_LOAD lhu, 0, 0x7f01
    CHECK_LOAD lhu, 2, 0x80ff
    li t1, 0x42
    sb t1, 0(t0)
    CHECK_LOAD lw, 0, 0x80ff7f42
    sb t1, 1(t0)
    CHECK_LOAD lw, 0, 0x80ff4242
    sb t1, 2(t0)
    CHECK_LOAD lw, 0, 0x80424242
    sb t1, 3(t0)
    CHECK_LOAD lw, 0, 0x42424242
    li t1, 0xbeef
    sh t1, 0(t0)
    CHECK_LOAD lw, 0, 0x4242beef
    sh t1, 2(t0)
    CHECK_LOAD lw, 0, 0xbeefbeef
    addi t0, t0, 4
    CHECK_LOAD lw, -4, 0xbeefbeef
    fence rw, rw
    addi zero, zero, 1
    lw zero, -4(t0)
    mul zero, t0, t1
    addi t2, zero, 7
    li t3, 7
    bne t2, t3, fail
    lui t6, 0xfffff
    li t3, 0xfffff000
    bne t6, t3, fail
pc_here:
    auipc t2, 0
    la t3, pc_here
    bne t2, t3, fail
    jal t4, jal_target
jal_return:
    j jalr_test
jal_target:
    la t3, jal_return
    bne t4, t3, fail
    jalr zero, 0(t4)
jalr_test:
    la t0, jalr_target
    addi t0, t0, 1  # JALR must ignore target bit zero.
    jalr t4, 0(t0)
jalr_return:
    j pass
jalr_target:
    la t3, jalr_return
    bne t4, t3, fail
    jalr zero, 0(t4)
fail:
    la t1, fail_message
    jal ra, print_message
    li t5, 28
hex_loop:
    srl t2, s0, t5
    andi t2, t2, 15
    la t1, digits
    add t1, t1, t2
    lbu t2, 0(t1)
    sw t2, 0(t0)
    addi t5, t5, -4
    bge t5, zero, hex_loop
    li t2, 10
    sw t2, 0(t0)
    j finished
pass:
    la t1, pass_message
    jal ra, print_message
finished:
    j finished
print_message:
    lui t0, 0x20000
1:
    lbu t2, 0(t1)
    beq t2, zero, 2f
    sw t2, 0(t0)
    addi t1, t1, 1
    j 1b
2:
    ret
    .section .rodata
pass_message: .asciz "RV32IM PASS\n"
fail_message: .asciz "FAIL: RV32IM vector 0x"
digits: .ascii "0123456789abcdef"
