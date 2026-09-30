/* Phase 18 CPU shell, directed tests: the riscv-tests layout (env/link.ld),
   then a `.tail` section, after which the shell memory ends
   (shell_memory_end sets the shell's +mem_bytes and Spike's -m size), so
   instructions in `.tail` sit at the very end of memory and the core's
   sequential fetches past them are answered with i_rsp_error. */
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
    .tail ALIGN(4) : { *(.tail) } > SHELL
    shell_memory_end = .;
    _end = .;
}
