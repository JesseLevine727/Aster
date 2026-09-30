/* Phase 18 CPU-shell layout for the CPU kernels: code, data and stack in the
   96 KiB shell memory at 0x8000_0000 (Spike: -m0x80000000:0x18000), plus the
   SoC register page at 0x2000_0000 as plain memory (+io_page; Spike maps
   0x20000000:0x10000). */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { SHELL (rwx) : ORIGIN = 0x80000000, LENGTH = 0x18000 }
SECTIONS
{
    .text.init : { *(.text.init) } > SHELL
    .text : { *(.text .text.*) } > SHELL
    .tohost ALIGN(0x40) : { *(.tohost) } > SHELL
    .rodata : { *(.rodata .rodata.*) *(.srodata .srodata.*) } > SHELL
    .data : { *(.data .data.*) *(.sdata .sdata.*) } > SHELL
    .bss (NOLOAD) : { __bss_start = .; *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) . = ALIGN(4); } > SHELL
    _end = .;
    _stack_top = ORIGIN(SHELL) + LENGTH(SHELL);
    /* At least 8 KiB between the end of bss and the top of memory for the stack. */
    ASSERT(_stack_top - _end >= 0x2000, "kernel leaves less than 8 KiB of stack in the shell memory")
}
