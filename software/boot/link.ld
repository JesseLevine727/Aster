ENTRY(_start)

MEMORY
{
    ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 64K
    RAM (rwx) : ORIGIN = 0x10000000, LENGTH = 64K
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
