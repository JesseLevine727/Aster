    # start_multicore.S for the Aster core (milestone 18.6: the runtime port):
    # the same start-up and contract (software/runtime/README.md, docs/phase5.md)
    # with the hart's identity read from mhartid (the core's HART_ID) instead of
    # the hart-control page, and traps taken at mtvec (aster_trap.S) instead of
    # PicoRV32's fixed IRQ vector. A returning main passes its status to
    # aster_exit (by default it returns); either return parks that hart.
    .section .text.init
    .globl _start
    .option norvc
    .option norelax

_start:
    la      t0, aster_trap_entry
    csrw    mtvec, t0
    # No stack access before reading the identity of the requesting real core.
    csrr    a0, mhartid
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
    call    aster_exit
    j       runtime_halt
secondary_c:
    call    aster_secondary_main
runtime_halt:
    j       runtime_halt
