/* Fixed Phase 8 allocations: identical addresses across all K/alignment/methods.
 * Payload buffers are prepared by real RISC-V C, never by the capture host. */
ENTRY(_start)
MEMORY
{
    ROM (rx) : ORIGIN = 0x00000000, LENGTH = 64K
    SHARED (rwx) : ORIGIN = 0x10000000, LENGTH = 4K
    DOT8_A (rw) : ORIGIN = 0x10001000, LENGTH = 0x2000
    DOT8_B (rw) : ORIGIN = 0x10003000, LENGTH = 0x3000
    DOT8_Y (rw) : ORIGIN = 0x10006000, LENGTH = 0x2000
    PRIVATE0 (rwx) : ORIGIN = 0x10008000, LENGTH = 16K
    PRIVATE1 (rwx) : ORIGIN = 0x1000c000, LENGTH = 16K
}
SECTIONS
{
    .text : ALIGN(4)
    {
        KEEP(*(.text.init))
        . = ALIGN(256);
        __benchmark_start = .;
        __scalar_start = .;
        *(.text.benchmark.scalar)
        __scalar_end = .;
        . = ALIGN(256);
        __custom_start = .;
        *(.text.benchmark.custom)
        __custom_end = .;
        __benchmark_end = .;
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
    .dot8_a (NOLOAD) : ALIGN(64) { KEEP(*(.dot8_a)) } > DOT8_A
    .dot8_b (NOLOAD) : ALIGN(64) { KEEP(*(.dot8_b)) } > DOT8_B
    .dot8_y (NOLOAD) : ALIGN(64) { KEEP(*(.dot8_y)) } > DOT8_Y
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
    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0, "dot8 stack alignment")
    ASSERT(__stack0_bottom >= ORIGIN(PRIVATE0) && __stack0_bottom >= __private0_end, "dot8 results overlap stack0")
    ASSERT(__stack1_bottom >= ORIGIN(PRIVATE1) && __stack1_bottom >= __private1_end, "dot8 results overlap stack1")
    /DISCARD/ : { *(.comment) *(.eh_frame) *(.riscv.attributes) }
}
