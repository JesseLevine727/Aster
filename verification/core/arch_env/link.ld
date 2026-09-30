/* riscv-arch-test programs on the Phase 18 CPU shell: the same layout as
   env/link.ld with a 2 MiB region (jal-01 alone is 1.77 MB), matched by the
   shell's +mem_bytes=200000 and Spike's -m0x80000000:0x200000. The size is a
   property of the conformance harness, not of the design. */
OUTPUT_ARCH(riscv)
ENTRY(rvtest_entry_point)
MEMORY { SHELL (rwx) : ORIGIN = 0x80000000, LENGTH = 0x200000 }
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
