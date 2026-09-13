#ifndef REFERENCE_HART
#define REFERENCE_HART 0
#endif
#if REFERENCE_HART != 0 && REFERENCE_HART != 1
#error REFERENCE_HART must be 0 or 1
#endif
    .option norvc
    .option norelax
    .section .text.init
    .globl _start
_start:
    li t0, 0x20002000
    lw tp, 0(t0)
    bnez tp, reference_select
    la t0, __data_load_start
    la t1, __data_start
    la t2, __data_end
1:  bgeu t1, t2, 2f
    lw t3, 0(t0)
    sw t3, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 4
    j 1b
2:  la t0, __bss_start
    la t1, __bss_end
3:  bgeu t0, t1, reference_select
    sw zero, 0(t0)
    addi t0, t0, 4
    j 3b
reference_select:
    li t0, REFERENCE_HART
    beq tp, t0, reference_run
#if REFERENCE_HART == 1
    # Hart 0 initializes shared data once, then releases the actual worker.
    fence rw, rw
    li t0, 0x20002004
    li t1, 1
    sw t1, 0(t0)
#endif
    j aster_reference_halt
reference_run:
    la t0, aster_reference_status
    sw zero, 0(t0)
    sw zero, 4(t0)
    sw zero, 8(t0)
    sw zero, 12(t0)
    fence rw, rw
    j aster_reference_entry
    .section .text
    .globl aster_reference_report
aster_reference_report:
    la t0, aster_reference_status
    sw gp, 4(t0)
    li t1, REFERENCE_HART
    sw t1, 8(t0)
    li t1, 0xa57e6001
    sw t1, 12(t0)
    fence rw, rw
    sw a0, 0(t0)
    .globl aster_reference_halt
aster_reference_halt:
    j aster_reference_halt
#if REFERENCE_HART == 0
    .section .private0,"aw",@nobits
#else
    .section .private1,"aw",@nobits
#endif
    .align 4
    .globl aster_reference_status
    .type aster_reference_status, @object
aster_reference_status:
    .space 16
    .size aster_reference_status, .-aster_reference_status
