#include <stdint.h>

#include "aster.h"

#define BENCHMARK_WORDS 64u
#define BENCHMARK_REPETITIONS 4u

static volatile uint32_t source[ BENCHMARK_WORDS ];
static volatile uint32_t destination[ BENCHMARK_WORDS ];

static void copy_words(void) {
    for (uint32_t repetition = 0; repetition < BENCHMARK_REPETITIONS;
         ++repetition) {
        for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index)
            destination[index] = source[index];
    }
}

static void emit_counter(const char *name, struct aster_perf_counter value) {
    aster_putc(',');
    aster_puts(name);
    aster_puts("=0x");
    aster_put_hex64(value);
}

static void emit_u32(const char *name, uint32_t value) {
    aster_putc(','); aster_puts(name); aster_putc('='); aster_put_u32(value);
}

int main(void) {
    for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index) {
        source[index] = 0x13570000u ^ (index * 0x1021u);
        destination[index] = 0u;
    }

    aster_perf_clear();
    copy_words();

    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);

    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    uint32_t checksum = 0u;
    for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index) {
        if (destination[index] != source[index])
            pass = 0u;
        checksum = (checksum * 33u) ^ destination[index];
    }

    aster_puts("ASTERBENCH,version=2,name=memcpy,bytes=256,repetitions=4,seed=0x13570000,status=");
    aster_puts(pass ? "PASS" : "FAIL");
    aster_puts(",checksum=0x"); aster_put_hex32(checksum);
    emit_u32("clock_hz", *ASTER_PERF_CLOCK_HZ);
    emit_u32("l1", *ASTER_PERF_FLAGS & 1u);
    emit_u32("sync_memory", (*ASTER_PERF_FLAGS >> 1) & 1u);
    emit_u32("line_words", *ASTER_PERF_LINE_WORDS);
    emit_u32("line_count", *ASTER_PERF_LINE_COUNT);
    emit_u32("memory_wait", *ASTER_PERF_MEMORY_WAIT);
    emit_counter("cycles", snapshot.cycles);
    emit_counter("retired", snapshot.retired);
    emit_counter("memory_transactions", snapshot.memory_transactions);
    emit_counter("backing_transactions", snapshot.backing_transactions);
    emit_counter("cache_accesses", snapshot.cache_accesses);
    emit_counter("cache_misses", snapshot.cache_misses);
    emit_counter("dma_bytes", snapshot.dma_bytes);
    emit_counter("accelerator_cycles", snapshot.accelerator_cycles);
    aster_puts("\n");

    for (;;) { }
}
