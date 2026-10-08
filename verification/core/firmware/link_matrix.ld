/* 20.4's workload matrix (docs/matrix.md §8, step 1): the Phase 20 SoC's 96 KiB of main memory as one
   region, its sections end to end and the two 4 KiB stacks at the top, so no fixed split strands space
   (MNIST's 51 KB of read-only data, the GEMM's arena). The runtime is link_multicore.ld's
   (software/runtime/start_multicore_aster.S): .data, .bss, .private0 and .private1, each hart's stack.

   .data has no separate load image here (its load address is its own, so the start-up's copy is a no-op).
   A warm run's harness (software/matrix) keeps .data's first state in .data_save, which this layout
   reserves at .data's size, and restores it from there. */
ENTRY(_start)

MEMORY
{
    MAIN (rwx) : ORIGIN = 0x80000000, LENGTH = 96K
}

SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.text.init))
        . = ALIGN(256);
        __benchmark_start = .;
        *(.text.benchmark)
        __benchmark_end = .;
        *(.text*)
    } > MAIN
    .rodata : ALIGN(4) { *(.rodata*) *(.srodata*) } > MAIN
    .tohost : ALIGN(64) { KEEP(*(.tohost)) } > MAIN
    .data : ALIGN(4)
    {
        __data_start = .;
        *(.data*) *(.sdata*)
        . = ALIGN(4);
        __data_end = .;
    } > MAIN
    __data_load_start = __data_start;
    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start = .;
        *(.bss*) *(.sbss*) *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > MAIN
    .npu_summary (NOLOAD) : ALIGN(16)
    {
        __npu_summary_start = .;
        KEEP(*(.npu_summary*))
        . = ALIGN(4);
        __npu_summary_end = .;
    } > MAIN
    .data_save (NOLOAD) : ALIGN(4)
    {
        __data_save_start = .;
        . += SIZEOF(.data);
        __data_save_end = .;
    } > MAIN
    .private0 (NOLOAD) : ALIGN(16)
    {
        __private0_start = .;
        *(.private0*)
        . = ALIGN(4);
        __private0_end = .;
    } > MAIN
    .private1 (NOLOAD) : ALIGN(16)
    {
        __private1_start = .;
        *(.private1*)
        . = ALIGN(4);
        __private1_end = .;
    } > MAIN
    PROVIDE(__stack_size = 4096);
    __stack1_top = ORIGIN(MAIN) + LENGTH(MAIN);
    __stack1_bottom = __stack1_top - __stack_size;
    __stack0_top = __stack1_bottom;
    __stack0_bottom = __stack0_top - __stack_size;
    shell_memory_end = ORIGIN(MAIN) + LENGTH(MAIN);
    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0, "the stacks must be 16-byte aligned")
    ASSERT(__private1_end <= __stack0_bottom, "the program and its data overflow into the stacks")
    /DISCARD/ : { *(.comment) *(.eh_frame) *(.riscv.attributes) }
}
