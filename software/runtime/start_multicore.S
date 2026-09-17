    .section .text.init
    .globl _start
    .option norvc
    .option norelax

_start:
    j       _start_setup
    .org    0x10
    .globl  aster_irq_vector
aster_irq_vector:
    j       aster_irq_handler

_start_setup:
    # No stack access before reading the identity of the requesting real core.
    li      t0, 0x20002000
    lw      a0, 0(t0)
    mv      tp, a0
    bnez    a0, secondary_start
    la      sp, __stack0_top
    la      t0, __data_load_start
    la      t1, __data_start
    la      t2, __data_end
copy_data:
    bgeu    t1, t2, clear_shared_start
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    j       copy_data
clear_shared_start:
    la      t0, __bss_start
    la      t1, __bss_end
clear_shared:
    bgeu    t0, t1, primary_private
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       clear_shared
primary_private:
    la      t0, __private0_start
    la      t1, __private0_end
    j       clear_private
secondary_start:
    la      sp, __stack1_top
    la      t0, __private1_start
    la      t1, __private1_end
clear_private:
    bgeu    t0, t1, enter_c
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       clear_private
enter_c:
    fence   rw, rw
    bnez    tp, secondary_c
    call    main
    j       runtime_halt
secondary_c:
    call    aster_secondary_main
runtime_halt:
    j       runtime_halt

    # PicoRV32 takes its IRQ at the fixed vector 0x10. The handler preserves the
    # caller-saved registers, dispatches in C, then returns with retirq (which
    # restores the PC from q0). A weak default dispatch traps if a firmware
    # never provides one.
    .section .text
    .globl  aster_irq_handler
    .type   aster_irq_handler, @function
aster_irq_handler:
    addi    sp, sp, -64
    sw      ra, 0(sp)
    sw      t0, 4(sp)
    sw      t1, 8(sp)
    sw      t2, 12(sp)
    sw      t3, 16(sp)
    sw      t4, 20(sp)
    sw      t5, 24(sp)
    sw      t6, 28(sp)
    sw      a0, 32(sp)
    sw      a1, 36(sp)
    sw      a2, 40(sp)
    sw      a3, 44(sp)
    sw      a4, 48(sp)
    sw      a5, 52(sp)
    sw      a6, 56(sp)
    sw      a7, 60(sp)
    call    aster_irq_dispatch
    lw      ra, 0(sp)
    lw      t0, 4(sp)
    lw      t1, 8(sp)
    lw      t2, 12(sp)
    lw      t3, 16(sp)
    lw      t4, 20(sp)
    lw      t5, 24(sp)
    lw      t6, 28(sp)
    lw      a0, 32(sp)
    lw      a1, 36(sp)
    lw      a2, 40(sp)
    lw      a3, 44(sp)
    lw      a4, 48(sp)
    lw      a5, 52(sp)
    lw      a6, 56(sp)
    lw      a7, 60(sp)
    addi    sp, sp, 64
    .word   0x0400000b              # retirq
    .size   aster_irq_handler, .-aster_irq_handler

    .globl  aster_irq_dispatch
    .weak   aster_irq_dispatch
    .type   aster_irq_dispatch, @function
aster_irq_dispatch:
    ebreak
1:  j       1b
    .size   aster_irq_dispatch, .-aster_irq_dispatch
