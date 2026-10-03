    # The Aster core's trap entry (milestone 18.6: the runtime port). v1's
    # PicoRV32 runtime took its IRQ at the fixed vector 0x10 and returned with
    # retirq; the Aster core takes every trap at mtvec (direct mode, set by
    # start_aster.S and start_multicore_aster.S) and returns with mret. The
    # entry preserves the caller-saved registers, as v1's did, and dispatches
    # in C: an interrupt (mcause bit 31) to aster_irq_dispatch, v1's dispatch
    # (the interrupt controller drives the machine external interrupt), and an
    # exception to aster_exception(mcause, mepc, mtval). The weak defaults keep
    # v1's behaviour: a firmware that never provides a dispatch takes an ebreak
    # (an exception here), and an exception halts the hart — through
    # aster_exit(-1), which a harness may provide (the default returns, and the
    # hart then spins). A firmware's own aster_exception must not return unless
    # it has advanced mepc: mret would retry the faulting instruction.
    .option norvc
    .section .text
    .align  2
    .globl  aster_trap_entry
    .type   aster_trap_entry, @function
aster_trap_entry:
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
    csrr    a0, mcause
    bltz    a0, 1f
    csrr    a1, mepc
    csrr    a2, mtval
    call    aster_exception
    j       2f
1:  call    aster_irq_dispatch
2:  lw      ra, 0(sp)
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
    mret
    .size   aster_trap_entry, .-aster_trap_entry

    .globl  aster_irq_dispatch
    .weak   aster_irq_dispatch
    .type   aster_irq_dispatch, @function
aster_irq_dispatch:
    ebreak
1:  j       1b
    .size   aster_irq_dispatch, .-aster_irq_dispatch

    .globl  aster_exception
    .weak   aster_exception
    .type   aster_exception, @function
aster_exception:
    li      a0, -1
    call    aster_exit
1:  j       1b
    .size   aster_exception, .-aster_exception

    .globl  aster_exit
    .weak   aster_exit
    .type   aster_exit, @function
aster_exit:
    ret
    .size   aster_exit, .-aster_exit
