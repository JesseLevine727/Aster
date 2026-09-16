#ifndef ASTER_WORKLOAD_COH_H
#define ASTER_WORKLOAD_COH_H
#include "aster.h"

// AsterBench v10 record for the coherent RV32IMA SoC (CPU counter ABI 4).
// The eight v10 counters are mapped from the primary hart's 14-event bank:
// i_access + d_access for cache accesses and i_miss + d_miss for misses.

#define COH_CONTROL      0x20003080u
#define COH_CPU_ABI      0x20003084u
#define COH_CLOCK_HZ     0x20003088u
#define COH_FLAGS        0x2000308cu
#define COH_LINE_WORDS   0x20003090u
#define COH_LINE_COUNT   0x20003094u
#define COH_MEMORY_WAIT  0x20003098u
#define COH_CPU_ABI_VALUE 4u

static inline uint32_t coh_reg(uint32_t address) {
    return *(volatile uint32_t *)(uintptr_t)address;
}

static inline uint64_t coh_read64(uint32_t address) {
    volatile uint32_t *low = (volatile uint32_t *)(uintptr_t)address;
    volatile uint32_t *high = low + 1;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return ((uint64_t)last << 32) | value;
}

static inline uint64_t coh_counter(uint32_t event) {
    return coh_read64(0x20003000u + event * 8u);
}

static inline void coh_field(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static inline void coh_hex32(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static inline void coh_hex64(const char *key, uint64_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x");
    aster_put_hex32((uint32_t)(value >> 32)); aster_put_hex32((uint32_t)value);
}
static inline void aster_workload_emit_coh(const char *name, const char *category,
                                           uint32_t size, uint32_t iterations, uint32_t param,
                                           uint32_t seed, uint32_t checksum, uint32_t pass) {
    aster_puts("ASTERBENCH,version=10,name="); aster_puts(name);
    aster_puts(",category="); aster_puts(category);
    aster_puts(",status="); aster_puts(pass ? "PASS" : "FAIL");
    coh_field("size", size);
    coh_field("iterations", iterations);
    coh_field("param", param);
    coh_hex32("seed", seed);
    coh_hex32("checksum", checksum);
    coh_field("clock_hz", coh_reg(COH_CLOCK_HZ));
    coh_field("l1", coh_reg(COH_FLAGS) & 1u);
    coh_field("sync_memory", (coh_reg(COH_FLAGS) >> 1) & 1u);
    coh_field("line_words", coh_reg(COH_LINE_WORDS));
    coh_field("line_count", coh_reg(COH_LINE_COUNT));
    coh_field("memory_wait", coh_reg(COH_MEMORY_WAIT));
    coh_hex64("cycles", coh_counter(0));
    coh_hex64("retired", coh_counter(1));
    coh_hex64("memory_transactions", coh_counter(2));
    coh_hex64("backing_transactions", coh_counter(7));
    coh_hex64("cache_accesses", coh_counter(3) + coh_counter(5));
    coh_hex64("cache_misses", coh_counter(4) + coh_counter(6));
    coh_hex64("dma_bytes", coh_read64(0x30000120u));
    coh_hex64("accelerator_cycles", coh_read64(0x40000048u));
    aster_putc('\n');
}
#endif
