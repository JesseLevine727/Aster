/* Fixed Phase 11 MNIST inference map.
 * ROM holds code, the quantized weights and the retained test images.
 * Shared RAM holds the NPU-visible staged weights and the activation/scratch
 * buffers; the two private regions hold the stacks only. */
ENTRY(_start)
MEMORY
{
    ROM (rx) : ORIGIN = 0x00000000, LENGTH = 64K
    SHARED (rw) : ORIGIN = 0x10000000, LENGTH = 4K
    P11_W1 (rw) : ORIGIN = 0x10001000, LENGTH = 0x6200
    P11_W2 (rw) : ORIGIN = 0x10007200, LENGTH = 0x200
    P11_IO (rw) : ORIGIN = 0x10007400, LENGTH = 0x400
    PRIVATE0 (rwx) : ORIGIN = 0x10008000, LENGTH = 16K
    PRIVATE1 (rwx) : ORIGIN = 0x1000c000, LENGTH = 16K
}
SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.text.init))
        *(.text*)
    } > ROM
    .rodata : ALIGN(4) { *(.rodata*) } > ROM
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
    .p11_w1 (NOLOAD) : ALIGN(64) { KEEP(*(.p11_w1)) } > P11_W1
    .p11_w2 (NOLOAD) : ALIGN(64) { KEEP(*(.p11_w2)) } > P11_W2
    .p11_io (NOLOAD) : ALIGN(64) { KEEP(*(.p11_io)) } > P11_IO
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
    __stack0_top = ORIGIN(PRIVATE0)+LENGTH(PRIVATE0);
    __stack1_top = ORIGIN(PRIVATE1)+LENGTH(PRIVATE1);
    __stack0_bottom = __stack0_top-__stack_size;
    __stack1_bottom = __stack1_top-__stack_size;
    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0, "phase11 stack alignment")
    ASSERT(__stack0_bottom >= ORIGIN(PRIVATE0) && __stack0_bottom >= __private0_end, "phase11 results overlap stack0")
    ASSERT(__stack1_bottom >= ORIGIN(PRIVATE1) && __stack1_bottom >= __private1_end, "phase11 results overlap stack1")
    /DISCARD/ : { *(.comment) *(.eh_frame) *(.riscv.attributes) }
}
