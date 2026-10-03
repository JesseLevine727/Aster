/* v1 firmware on the Aster core in the CPU shell (milestone 18.6: the firmware
   regression), with the ported runtime (software/runtime/start_aster.S):
   software/boot/link.ld's sections, in the shell's 96 KiB memory at
   0x8000_0000 instead of v1's ROM at 0 and RAM at 0x1000_0000 — "ROM" (code,
   read-only data and the initial data image) in its first 64 KiB, RAM in the
   remaining 32 KiB — plus the harness's tohost (exit.c) and the memory's end
   for the runner. verification/host/test_core_firmware.py checks the sections
   against v1's. */
ENTRY(_start)

MEMORY
{
    ROM (rx)  : ORIGIN = 0x80000000, LENGTH = 64K
    RAM (rwx) : ORIGIN = 0x80010000, LENGTH = 32K
}

SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.text.init))
        /* Startup relaxation depends on ROM size. Keep measured kernels at
           the same address across sequential/random initialization code. */
        . = ALIGN(256);
        *(.text.benchmark)
        *(.text*)
    } > ROM

    .rodata : ALIGN(4)
    {
        *(.rodata*)
    } > ROM

    .tohost : ALIGN(64) { KEEP(*(.tohost)) } > ROM

    .data : ALIGN(4)
    {
        __data_start = .;
        *(.data*)
        *(.sdata*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT> ROM

    __data_load_start = LOADADDR(.data);

    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start = .;
        *(.bss.benchmark)
        *(.bss*)
        *(.sbss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM

    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
    PROVIDE(__stack_size = 4096);
    __stack_bottom = __stack_top - __stack_size;
    shell_memory_end = ORIGIN(RAM) + LENGTH(RAM);

    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0, "Aster stack must be 16-byte aligned")
    ASSERT(__stack_bottom >= ORIGIN(RAM) && __stack_bottom >= __bss_end,
           "Aster RAM overflow into reserved stack")

    /DISCARD/ :
    {
        *(.comment)
        *(.eh_frame)
        *(.riscv.attributes)
    }
}
