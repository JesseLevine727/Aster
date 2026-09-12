    .option norvc
    .section .text.init
    .globl _start
    .type _start, @function

_start:
    # The linker places the stack at the first byte above the 64 KiB RAM.
    la      sp, __stack_top

    # Copy initialized data from its ROM load address into RAM.
    la      t0, __data_load_start
    la      t1, __data_start
    la      t2, __data_end
copy_data:
    bgeu    t1, t2, clear_bss_start
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    jal     zero, copy_data

    # Clear .bss before entering C. The loop is word based because the linker
    # aligns both symbols and the Phase 1 RAM interface is 32 bits wide.
clear_bss_start:
    la      t0, __bss_start
    la      t1, __bss_end
clear_bss:
    bgeu    t0, t1, bss_done
    sw      zero, 0(t0)
    addi    t0, t0, 4
    jal     zero, clear_bss

bss_done:
    call    main

runtime_halt:
    jal     zero, runtime_halt
