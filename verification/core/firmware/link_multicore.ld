/* v1 multicore-runtime firmware on the Aster core in the CPU shell (milestone
   18.6: the firmware regression), with the ported runtime
   (software/runtime/start_multicore_aster.S): software/boot/link_multicore.ld's
   sections, in the shell's 96 KiB memory at 0x8000_0000 instead of v1's ROM at
   0 and RAM at 0x1000_0000 — "ROM" 32 KiB (v1: 64 KiB), then v1's shared
   32 KiB and two private 16 KiB regions — plus the harness's tohost (exit.c)
   and the memory's end for the runner. verification/host/test_core_firmware.py
   checks the sections against v1's. */
ENTRY(_start)

MEMORY
{
    ROM (rx) : ORIGIN = 0x80000000, LENGTH = 32K
    SHARED (rwx) : ORIGIN = 0x80008000, LENGTH = 32K
    PRIVATE0 (rwx) : ORIGIN = 0x80010000, LENGTH = 16K
    PRIVATE1 (rwx) : ORIGIN = 0x80014000, LENGTH = 16K
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
    } > ROM
    .rodata : ALIGN(4) { *(.rodata*) } > ROM
    .tohost : ALIGN(64) { KEEP(*(.tohost)) } > ROM
    .data : ALIGN(4)
    {
        __data_start = .;
        *(.data*) *(.sdata*)
        . = ALIGN(4);
        __data_end = .;
    } > SHARED AT> ROM
    __data_load_start = LOADADDR(.data);
    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start = .;
        *(.bss*) *(.sbss*) *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > SHARED
    .npu_summary (NOLOAD) : ALIGN(16)
    {
        __npu_summary_start = .;
        KEEP(*(.npu_summary*))
        . = ALIGN(4);
        __npu_summary_end = .;
    } > SHARED
    .private0 (NOLOAD) : ALIGN(16)
    {
        __private0_start = .;
        *(.private0*)
        . = ALIGN(4);
        __private0_end = .;
    } > PRIVATE0
    .private1 (NOLOAD) : ALIGN(16)
    {
        __private1_start = .;
        *(.private1*)
        . = ALIGN(4);
        __private1_end = .;
    } > PRIVATE1
    PROVIDE(__stack_size = 4096);
    __stack0_top = ORIGIN(PRIVATE0) + LENGTH(PRIVATE0);
    __stack1_top = ORIGIN(PRIVATE1) + LENGTH(PRIVATE1);
    __stack0_bottom = __stack0_top - __stack_size;
    __stack1_bottom = __stack1_top - __stack_size;
    shell_memory_end = ORIGIN(PRIVATE1) + LENGTH(PRIVATE1);
    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0,
           "Aster multicore stacks must be 16-byte aligned")
    ASSERT(__stack0_bottom >= ORIGIN(PRIVATE0) && __stack0_bottom >= __private0_end,
           "Aster hart 0 private RAM overflow into stack")
    ASSERT(__stack1_bottom >= ORIGIN(PRIVATE1) && __stack1_bottom >= __private1_end,
           "Aster hart 1 private RAM overflow into stack")
    /DISCARD/ : { *(.comment) *(.eh_frame) *(.riscv.attributes) }
}
