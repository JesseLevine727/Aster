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
        __data_end = .;
    } > RAM AT> ROM

    __data_load_start = LOADADDR(.data);

    .bss (NOLOAD) : ALIGN(4)
    {
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        __bss_end = .;
    } > RAM

    __stack_top = ORIGIN(RAM) + LENGTH(RAM);

    ASSERT(__stack_top >= __bss_end, "Aster RAM overflow")

    /DISCARD/ :
    {
        *(.comment)
        *(.eh_frame)
        *(.riscv.attributes)
    }
}
