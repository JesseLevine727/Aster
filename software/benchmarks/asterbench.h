#ifndef ASTER_BENCHMARK_H
#define ASTER_BENCHMARK_H
#include "aster.h"

#ifndef BENCHMARK_WORDS
#define BENCHMARK_WORDS 64u
#endif
#ifndef BENCHMARK_REPETITIONS
#define BENCHMARK_REPETITIONS 4u
#endif
#ifndef BENCHMARK_SEED
#define BENCHMARK_SEED 0x13570000u
#endif
#ifndef BENCH_RANDOM
#define BENCH_RANDOM 0
#endif
_Static_assert(BENCHMARK_WORDS >= 2 && BENCHMARK_WORDS <= 4096 &&
               (BENCHMARK_WORDS & (BENCHMARK_WORDS-1)) == 0, "benchmark words must be power of two, 2..4096");
_Static_assert(BENCHMARK_REPETITIONS >= 1 && BENCHMARK_REPETITIONS <= 64, "repetitions must be 1..64");

static inline void aster_bench_u32(const char *name, uint32_t value) {
    aster_putc(','); aster_puts(name); aster_putc('='); aster_put_u32(value);
}
static inline void aster_bench_counter(const char *name, struct aster_perf_counter value) {
    aster_putc(','); aster_puts(name); aster_puts("=0x"); aster_put_hex64(value);
}
static inline void aster_bench_emit(const char *name, uint32_t checksum, uint32_t pass,
                                   const struct aster_perf_snapshot *snapshot) {
    aster_puts("ASTERBENCH,version=2,name="); aster_puts(name);
    aster_bench_u32("bytes", BENCHMARK_WORDS * 4u);
    aster_bench_u32("repetitions", BENCHMARK_REPETITIONS);
    aster_puts(",seed=0x"); aster_put_hex32(BENCHMARK_SEED);
    aster_puts(",status="); aster_puts(pass && *ASTER_PERF_ABI == 2 ? "PASS" : "FAIL");
    aster_puts(",checksum=0x"); aster_put_hex32(checksum);
    aster_bench_u32("clock_hz", *ASTER_PERF_CLOCK_HZ);
    aster_bench_u32("l1", *ASTER_PERF_FLAGS & 1u);
    aster_bench_u32("sync_memory", (*ASTER_PERF_FLAGS >> 1) & 1u);
    aster_bench_u32("line_words", *ASTER_PERF_LINE_WORDS);
    aster_bench_u32("line_count", *ASTER_PERF_LINE_COUNT);
    aster_bench_u32("memory_wait", *ASTER_PERF_MEMORY_WAIT);
    aster_bench_counter("cycles", snapshot->cycles);
    aster_bench_counter("retired", snapshot->retired);
    aster_bench_counter("memory_transactions", snapshot->memory_transactions);
    aster_bench_counter("backing_transactions", snapshot->backing_transactions);
    aster_bench_counter("cache_accesses", snapshot->cache_accesses);
    aster_bench_counter("cache_misses", snapshot->cache_misses);
    aster_bench_counter("dma_bytes", snapshot->dma_bytes);
    aster_bench_counter("accelerator_cycles", snapshot->accelerator_cycles);
    aster_putc('\n');
}
#endif
