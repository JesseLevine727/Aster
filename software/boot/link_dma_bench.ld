/* Phase 7 size sweep: fixed buffer bases/index relationship across every size.
 * Payload allocations are explicitly prepared before use, not startup-cleared.
 * Ordinary initialized/BSS state and both protected stacks keep the same ABI.
 */
ENTRY(_start)
MEMORY
{
    ROM (rx) : ORIGIN = 0x00000000, LENGTH = 64K
    SHARED (rwx) : ORIGIN = 0x10000000, LENGTH = 4K
    DMA_SOURCE (rw) : ORIGIN = 0x10001000, LENGTH = 0x2100
    DMA_DESTINATION (rw) : ORIGIN = 0x10005080, LENGTH = 0x2100
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
        *(.text.benchmark)
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
    .dma_source (NOLOAD) : ALIGN(64) { KEEP(*(.dma_source)) } > DMA_SOURCE
    .dma_destination (NOLOAD) : ALIGN(64) { KEEP(*(.dma_destination)) } > DMA_DESTINATION
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
    ASSERT(__stack_size >= 16 && (__stack_size % 16) == 0,
           "Aster DMA benchmark stacks must be 16-byte aligned")
    ASSERT(__stack0_bottom >= ORIGIN(PRIVATE0) && __stack0_bottom >= __private0_end,
           "Aster DMA benchmark results/private0 overflow into stack")
    ASSERT(__stack1_bottom >= ORIGIN(PRIVATE1) && __stack1_bottom >= __private1_end,
           "Aster DMA benchmark private1 overflow into stack")
    /DISCARD/ : { *(.comment) *(.eh_frame) *(.riscv.attributes) }
}
