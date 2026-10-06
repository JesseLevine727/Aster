// The Phase 19 gate programs' shared pieces (software/npu2, milestone 19.4):
// the console (bytes stored to 0x2000_0000), the CPU's cycle counter, Xasterdot8
// as a plain (schedulable) instruction, and the hook that asks the SoC
// testbench (verification/npu/tb_npu_soc.cpp) to check a GEMM the CPU computed
// against its reference: the descriptor on the register page at 0x2000_3F00,
// then 1 to 0x2000_3F3C.
#ifndef NPU2_SOC_H
#define NPU2_SOC_H

#include <stdint.h>

#define NPU2_PAGE(offset) (*(volatile uint32_t *)(uintptr_t)(0x20000000u + (offset)))

static inline void npu2_putc(char c) { NPU2_PAGE(0) = (uint32_t)(uint8_t)c; }
static inline void npu2_puts(const char *s) { while (*s) npu2_putc(*s++); }
static inline void npu2_put_u64(uint64_t value) {
    char digits[21];
    int n = 0;
    do { digits[n++] = (char)('0' + value % 10u); value /= 10u; } while (value);
    while (n) npu2_putc(digits[--n]);
}
static inline void npu2_field(const char *key, uint64_t value) {
    npu2_putc(','); npu2_puts(key); npu2_putc('='); npu2_put_u64(value);
}

static inline uint64_t npu2_cycles(void) {
    uint32_t high, low, again;
    do {
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(high));
        __asm__ volatile ("csrr %0, mcycle" : "=r"(low));
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(again));
    } while (high != again);
    return ((uint64_t)high << 32) | low;
}

// Xasterdot8: rd = the sum of the four signed byte products (cpu.md §2).
static inline int32_t npu2_dot8(uint32_t a, uint32_t b) {
    uint32_t r;
    __asm__ (".insn r 0x0b, 0, 0, %0, %1, %2" : "=r"(r) : "r"(a), "r"(b));
    return (int32_t)r;
}

// Ask the testbench to check C = A x B (npu.md §2's layout) as the CPU left it.
static inline void npu2_check_gemm(const void *a, const void *b, const void *c, uint32_t a_stride,
                                   uint32_t b_stride, uint32_t c_stride, uint32_t m, uint32_t n, uint32_t k) {
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    NPU2_PAGE(0x3F00) = (uint32_t)(uintptr_t)a; NPU2_PAGE(0x3F04) = (uint32_t)(uintptr_t)b;
    NPU2_PAGE(0x3F08) = (uint32_t)(uintptr_t)c; NPU2_PAGE(0x3F0C) = a_stride; NPU2_PAGE(0x3F10) = b_stride;
    NPU2_PAGE(0x3F14) = c_stride; NPU2_PAGE(0x3F18) = m; NPU2_PAGE(0x3F1C) = n; NPU2_PAGE(0x3F20) = k;
    NPU2_PAGE(0x3F3C) = 1u;
}

#endif
