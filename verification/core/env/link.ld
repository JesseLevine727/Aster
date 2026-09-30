/* Phase 18 CPU shell: one unified 96 KiB memory at 0x8000_0000 (the size of
   the v2 on-chip SRAM decision), shared with Spike's -m0x80000000:0x18000. */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { SHELL (rwx) : ORIGIN = 0x80000000, LENGTH = 0x18000 }
SECTIONS
{
    .text.init : { *(.text.init) } > SHELL
    .text : { *(.text .text.*) } > SHELL
    .tohost ALIGN(0x1000) : { *(.tohost) } > SHELL
    .rodata : { *(.rodata .rodata.*) *(.srodata .srodata.*) } > SHELL
    .data : { *(.data .data.*) *(.sdata .sdata.*) } > SHELL
    .bss : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) } > SHELL
    _end = .;
}
