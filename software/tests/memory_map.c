#include <stdint.h>
#include "aster.h"

static int hole(uintptr_t address) {
    volatile uint32_t *p = (volatile uint32_t *)address;
    uint32_t before = *p; // Force allocation if cacheability is too broad.
    *p = 0xdeadbeefu;
    return before == 0 && *p == 0;
}

int main(void) {
    const uintptr_t holes[] = {0x00010000u, 0x0ffffffcu, 0x10010000u,
        0x1ffffffcu, 0x20001000u, 0x20002000u, 0x20004000u,
        0x30000000u, 0x40000000u, 0xfffffffcu};
    for (unsigned i = 0; i < sizeof holes / sizeof holes[0]; ++i)
        if (!hole(holes[i])) { aster_puts("FAIL: unmapped address\n"); return 1; }

    // Address zero is memory in this freestanding target, not an OS null page.
    uint32_t original;
    __asm__ volatile ("lw %0, 0(zero)" : "=r"(original));
    __asm__ volatile ("sw %0, 0(zero)" :: "r"(0xdeadbeefu) : "memory");
    uint32_t after;
    __asm__ volatile ("lw %0, 0(zero)" : "=r"(after));
    if (original != after) { aster_puts("FAIL: ROM write permission\n"); return 1; }

    volatile uint32_t *last = (volatile uint32_t *)0x1000fffcu;
    // Avoid the active C stack while testing the uppermost RAM word.
    uint32_t saved = *last;
    *last = 0xa55a1234u;
    uint32_t observed = *last;
    *last = saved;
    if (observed != 0xa55a1234u) { aster_puts("FAIL: RAM upper boundary\n"); return 1; }

    volatile uint32_t *code = (volatile uint32_t *)0x10008000u;
    code[0] = 0x02a00513u; // addi a0, zero, 42
    code[1] = 0x00008067u; // ret
    __asm__ volatile ("fence rw,rw" ::: "memory");
    if (((int (*)(void))code)() != 42) { aster_puts("FAIL: RAM execution\n"); return 1; }
    code[0] = 0x01700513u; // update the same RAM instruction to return 23
    __asm__ volatile ("fence rw,rw" ::: "memory");
    if (((int (*)(void))code)() != 23) { aster_puts("FAIL: stale RAM instruction\n"); return 1; }
    // Upper-byte stores to TXDATA must not emit an extra UART character.
    *(volatile uint8_t *)0x20000001u = 0x58u;
    if (*(volatile uint32_t *)0x20000004u != 1u) {
        aster_puts("FAIL: UART status\n"); return 1;
    }
    aster_puts("MEMORY MAP PASS\n");
    return 0;
}
