#ifndef ASTER_RUNTIME_H
#define ASTER_RUNTIME_H

#include <stdint.h>

#define ASTER_UART_TX ((volatile uint32_t *)0x20000000u)
#define ASTER_PERF_BASE ((volatile uint32_t *)0x20003000u)
#define ASTER_PERF_CYCLE_LO (ASTER_PERF_BASE + 0u)
#define ASTER_PERF_CYCLE_HI (ASTER_PERF_BASE + 1u)
#define ASTER_PERF_RETIRED_LO (ASTER_PERF_BASE + 2u)
#define ASTER_PERF_RETIRED_HI (ASTER_PERF_BASE + 3u)
#define ASTER_PERF_MEM_TXN_LO (ASTER_PERF_BASE + 4u)
#define ASTER_PERF_MEM_TXN_HI (ASTER_PERF_BASE + 5u)
#define ASTER_PERF_CACHE_ACCESS_LO (ASTER_PERF_BASE + 6u)
#define ASTER_PERF_CACHE_ACCESS_HI (ASTER_PERF_BASE + 7u)
#define ASTER_PERF_CACHE_MISS_LO (ASTER_PERF_BASE + 8u)
#define ASTER_PERF_CACHE_MISS_HI (ASTER_PERF_BASE + 9u)
#define ASTER_PERF_DMA_BYTES_LO (ASTER_PERF_BASE + 10u)
#define ASTER_PERF_DMA_BYTES_HI (ASTER_PERF_BASE + 11u)
#define ASTER_PERF_ACCEL_CYCLES_LO (ASTER_PERF_BASE + 12u)
#define ASTER_PERF_ACCEL_CYCLES_HI (ASTER_PERF_BASE + 13u)
#define ASTER_PERF_CONTROL (ASTER_PERF_BASE + 14u)

struct aster_perf_counter {
    uint32_t lo;
    uint32_t hi;
};

struct aster_perf_snapshot {
    struct aster_perf_counter cycles;
    struct aster_perf_counter retired;
    struct aster_perf_counter memory_transactions;
    struct aster_perf_counter cache_accesses;
    struct aster_perf_counter cache_misses;
    struct aster_perf_counter dma_bytes;
    struct aster_perf_counter accelerator_cycles;
};

static inline void aster_putc(char character) {
    *ASTER_UART_TX = (uint32_t)(uint8_t)character;
}

static inline void aster_puts(const char *text) {
    while (*text != '\0')
        aster_putc(*text++);
}

static inline void aster_put_hex32(uint32_t value) {
    static const char digits[] = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4)
        aster_putc(digits[(value >> shift) & 0xfu]);
}

static inline void aster_put_hex64(struct aster_perf_counter value) {
    aster_put_hex32(value.hi);
    aster_put_hex32(value.lo);
}

static inline struct aster_perf_counter aster_perf_read_counter(
    volatile uint32_t *lo_register, volatile uint32_t *hi_register) {
    struct aster_perf_counter value;
    uint32_t first_hi;
    uint32_t second_hi;
    do {
        first_hi = *hi_register;
        value.lo = *lo_register;
        second_hi = *hi_register;
    } while (first_hi != second_hi);
    value.hi = first_hi;
    return value;
}

static inline void aster_perf_clear(void) {
    *ASTER_PERF_CONTROL = 1u;
}

static inline void aster_perf_snapshot(struct aster_perf_snapshot *snapshot) {
    snapshot->cycles = aster_perf_read_counter(ASTER_PERF_CYCLE_LO,
                                                ASTER_PERF_CYCLE_HI);
    snapshot->retired = aster_perf_read_counter(ASTER_PERF_RETIRED_LO,
                                                 ASTER_PERF_RETIRED_HI);
    snapshot->memory_transactions = aster_perf_read_counter(
        ASTER_PERF_MEM_TXN_LO, ASTER_PERF_MEM_TXN_HI);
    snapshot->cache_accesses = aster_perf_read_counter(
        ASTER_PERF_CACHE_ACCESS_LO, ASTER_PERF_CACHE_ACCESS_HI);
    snapshot->cache_misses = aster_perf_read_counter(
        ASTER_PERF_CACHE_MISS_LO, ASTER_PERF_CACHE_MISS_HI);
    snapshot->dma_bytes = aster_perf_read_counter(ASTER_PERF_DMA_BYTES_LO,
                                                   ASTER_PERF_DMA_BYTES_HI);
    snapshot->accelerator_cycles = aster_perf_read_counter(
        ASTER_PERF_ACCEL_CYCLES_LO, ASTER_PERF_ACCEL_CYCLES_HI);
}

#endif
