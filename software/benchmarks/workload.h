#ifndef ASTER_WORKLOAD_H
#define ASTER_WORKLOAD_H
#include "asterbench.h"

// Generic AsterBench v10 workload record. Each workload reports its own size,
// iteration count, seed and self-checked checksum plus the common hardware
// counters. The strict validator lives in scripts/asterbench_v10.py.

static inline void aster_workload_field(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static inline void aster_workload_hex32(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static inline void aster_workload_counter(const char *key, struct aster_perf_counter value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex64(value);
}
static inline void aster_workload_emit(const char *name, const char *category,
                                       uint32_t size, uint32_t iterations, uint32_t param,
                                       uint32_t seed, uint32_t checksum, uint32_t pass,
                                       const struct aster_perf_snapshot *snapshot) {
    aster_puts("ASTERBENCH,version=10,name="); aster_puts(name);
    aster_puts(",category="); aster_puts(category);
    aster_puts(",status="); aster_puts(pass ? "PASS" : "FAIL");
    aster_workload_field("size", size);
    aster_workload_field("iterations", iterations);
    aster_workload_field("param", param);
    aster_workload_hex32("seed", seed);
    aster_workload_hex32("checksum", checksum);
    aster_workload_field("clock_hz", *ASTER_PERF_CLOCK_HZ);
    aster_workload_field("l1", *ASTER_PERF_FLAGS & 1u);
    aster_workload_field("sync_memory", (*ASTER_PERF_FLAGS >> 1) & 1u);
    aster_workload_field("line_words", *ASTER_PERF_LINE_WORDS);
    aster_workload_field("line_count", *ASTER_PERF_LINE_COUNT);
    aster_workload_field("memory_wait", *ASTER_PERF_MEMORY_WAIT);
    aster_workload_counter("cycles", snapshot->cycles);
    aster_workload_counter("retired", snapshot->retired);
    aster_workload_counter("memory_transactions", snapshot->memory_transactions);
    aster_workload_counter("backing_transactions", snapshot->backing_transactions);
    aster_workload_counter("cache_accesses", snapshot->cache_accesses);
    aster_workload_counter("cache_misses", snapshot->cache_misses);
    aster_workload_counter("dma_bytes", snapshot->dma_bytes);
    aster_workload_counter("accelerator_cycles", snapshot->accelerator_cycles);
    aster_putc('\n');
}
#endif
