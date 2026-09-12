#include <stdint.h>
#include "aster.h"

static volatile uint32_t initialized = 0x12345678u;
static volatile uint8_t odd_data[3] = {0x81u, 0x42u, 0xffu};
static volatile uint32_t bss_words[7];
static volatile uint8_t bss_bytes[5];

__attribute__((noinline)) static uint32_t stack_sum(uint32_t depth) {
    volatile uint32_t local[4] = {depth, depth + 1, depth + 2, depth + 3};
    uintptr_t sp;
    __asm__ volatile ("mv %0, sp" : "=r"(sp));
    if ((sp & 15u) != 0 || sp < 0x1000f000u || sp >= 0x10010000u)
        return 0xffffffffu;
    uint32_t nested = depth ? stack_sum(depth - 1) : 0;
    return nested + local[0] + local[1] + local[2] + local[3];
}

int main(void) {
    if (initialized != 0x12345678u || odd_data[0] != 0x81u ||
        odd_data[1] != 0x42u || odd_data[2] != 0xffu) {
        aster_puts("FAIL: initialized data\n");
        return 1;
    }
    for (unsigned i = 0; i < 7; ++i)
        if (bss_words[i] != 0) { aster_puts("FAIL: BSS words\n"); return 1; }
    for (unsigned i = 0; i < 5; ++i)
        if (bss_bytes[i] != 0) { aster_puts("FAIL: BSS bytes\n"); return 1; }
    if (stack_sum(8) != 198u) { aster_puts("FAIL: stack\n"); return 1; }

    // The harness resets the SoC after this line, without reinitializing RAM.
    // Both initialized data and BSS must be restored on the second boot.
    initialized = 0xdeadbeefu;
    odd_data[1] = 0;
    for (unsigned i = 0; i < 7; ++i) bss_words[i] = 0x55aa0000u + i;
    for (unsigned i = 0; i < 5; ++i) bss_bytes[i] = 0x5au;
    aster_puts("RUNTIME PASS\n");
    return 0;
}
