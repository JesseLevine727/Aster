#include "aster_multicore.h"

#ifndef PARALLEL_WORDS
#define PARALLEL_WORDS 64
#endif
#ifndef PARALLEL_ROUNDS
#define PARALLEL_ROUNDS 4
#endif
#ifndef PARALLEL_JOBS
#define PARALLEL_JOBS 3
#endif
#ifndef PARALLEL_WORKERS
#define PARALLEL_WORKERS 2
#endif
#ifndef PARALLEL_SEED
#define PARALLEL_SEED 0x13570000u
#endif
_Static_assert(PARALLEL_WORDS >= 2 && PARALLEL_WORDS <= 1024, "words must be 2..1024");
_Static_assert(PARALLEL_ROUNDS >= 1 && PARALLEL_ROUNDS <= 64, "rounds must be 1..64");
_Static_assert(PARALLEL_JOBS >= 1 && PARALLEL_JOBS <= 16, "jobs must be 1..16");
_Static_assert(PARALLEL_WORKERS == 1 || PARALLEL_WORKERS == 2, "workers must be 1 or 2");

#define ARM 0x40000000u
#define BOOT 0x80000000u
static volatile uint32_t inputs[PARALLEL_WORDS], outputs[PARALLEL_WORDS];
static volatile uint32_t private0[PARALLEL_WORDS] ASTER_PRIVATE0;
static volatile uint32_t private1[PARALLEL_WORDS] ASTER_PRIVATE1;
static volatile uint32_t completed_words[2], checksums[2];

// Separate function/section: the RTL harness observes real retirements within
// these ELF symbol bounds on each hardware core. No fake per-hart labels.
__attribute__((noipa, section(".text.benchmark")))
void aster_parallel_kernel(volatile uint32_t *work, uint32_t count) {
    for (uint32_t r = 0; r < PARALLEL_ROUNDS; ++r) {
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t x = work[i] + 0x9e3779b9u + r;
            x = (x ^ (x >> 16)) * 0x7feb352du;
            x = (x ^ (x >> 15)) * 0x846ca68bu;
            work[i] = x ^ (x >> 16);
        }
    }
}

static void work_slice(unsigned hart) {
    const uint32_t split = PARALLEL_WORKERS == 2 ? (PARALLEL_WORDS+1u)/2u : PARALLEL_WORDS;
    const uint32_t begin = hart ? split : 0;
    const uint32_t end = hart ? PARALLEL_WORDS : split;
    volatile uint32_t *private = hart ? private1 : private0;
    for (uint32_t i = begin; i < end; ++i) private[i-begin] = inputs[i];
    aster_parallel_kernel(private, end-begin);
    uint32_t sum = 0;
    for (uint32_t i = begin; i < end; ++i) {
        const uint32_t value = private[i-begin];
        outputs[i] = value;
        sum += value ^ ((i+1u) * 0x9e3779b9u);
    }
    checksums[hart] = sum;
    completed_words[hart] = end-begin;
}

void aster_secondary_main(void) {
    if (*ASTER_HART_ID != 1 || PARALLEL_WORKERS != 2) for (;;) {}
    aster_publish(ASTER_TO_HART0, BOOT);
    for (uint32_t job = 1; job <= PARALLEL_JOBS; ++job) {
        while (aster_observe(ASTER_TO_HART1) != (ARM | job)) {}
        aster_publish(ASTER_TO_HART0, ARM | job);
        while (aster_observe(ASTER_TO_HART1) != job) {}
        work_slice(1);
        aster_publish(ASTER_TO_HART0, job);
    }
}

static uint32_t reference_word(uint32_t seed, uint32_t index) {
    // Scalar reference uses a word-major traversal; the measured kernel uses
    // rounds-major volatile private-memory updates. Host oracles are separate.
    uint32_t value = seed ^ (index * 0x1021u);
    for (uint32_t round = 0; round < PARALLEL_ROUNDS; ++round) {
        value += round + 0x9e3779b9u;
        value ^= value >> 16;
        value *= 0x7feb352du;
        value ^= value >> 15;
        value *= 0x846ca68bu;
        value ^= value >> 16;
    }
    return value;
}
static void decimal(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static void hex(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static void counter(const char *key, struct aster_perf_counter value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex64(value);
}
static const char *const names[2][8] = {
    {"h0_cycles", "h0_retired", "h0_memory_transactions", "h0_cache_accesses",
     "h0_cache_misses", "h0_dma_bytes", "h0_accelerator_cycles", "h0_backing_transactions"},
    {"h1_cycles", "h1_retired", "h1_memory_transactions", "h1_cache_accesses",
     "h1_cache_misses", "h1_dma_bytes", "h1_accelerator_cycles", "h1_backing_transactions"}
};
static void emit(uint32_t job, uint32_t seed, uint32_t pass,
                 struct aster_perf_counter snapshot[2][8]) {
    aster_puts("ASTERBENCH,version=3,name=parallel_mix,status=");
    aster_puts(pass ? "PASS" : "FAIL");
    decimal("bytes", PARALLEL_WORDS*4u); decimal("rounds", PARALLEL_ROUNDS);
    decimal("jobs", PARALLEL_JOBS); decimal("job", job);
    hex("base_seed", PARALLEL_SEED); hex("seed", seed);
    decimal("harts", *ASTER_HART_COUNT); decimal("workers", PARALLEL_WORKERS);
    decimal("h0_words", completed_words[0]); decimal("h1_words", completed_words[1]);
    hex("h0_checksum", checksums[0]); hex("h1_checksum", checksums[1]);
    hex("checksum", checksums[0]+checksums[1]);
    decimal("clock_hz", *ASTER_PERF_CLOCK_HZ);
    decimal("l1", *ASTER_PERF_FLAGS & 1u); decimal("sync_memory", (*ASTER_PERF_FLAGS >> 1) & 1u);
    decimal("line_words", *ASTER_PERF_LINE_WORDS); decimal("line_count", *ASTER_PERF_LINE_COUNT);
    decimal("memory_wait", *ASTER_PERF_MEMORY_WAIT);
    counter("cycles", snapshot[0][0]);
    for (unsigned h = 0; h != 2; ++h)
        for (unsigned i = 0; i != 8; ++i) counter(names[h][i], snapshot[h][i]);
    aster_putc('\n');
}

int main(void) {
    if (*ASTER_HART_ID != 0 || *ASTER_HART_COUNT < PARALLEL_WORKERS || *ASTER_PERF_ABI != 3) {
        aster_puts("PARALLEL CONFIG FAIL\n");
        return 1;
    }
    if (PARALLEL_WORKERS == 2) {
        aster_secondary_release();
        while (aster_observe(ASTER_TO_HART0) != BOOT) {}
    }
    for (uint32_t job = 1; job <= PARALLEL_JOBS; ++job) {
        const uint32_t seed = PARALLEL_SEED ^ (job * 0x9e3779b9u);
        for (uint32_t i = 0; i != PARALLEL_WORDS; ++i) {
            inputs[i] = seed ^ (i * 0x1021u);
            outputs[i] = ~reference_word(seed, i);
        }
        completed_words[0] = completed_words[1] = 0;
        checksums[0] = checksums[1] = 0;
        if (PARALLEL_WORKERS == 2) {
            aster_publish(ASTER_TO_HART1, ARM | job);
            while (aster_observe(ASTER_TO_HART0) != (ARM | job)) {}
        }
        aster_perf_clear();
        if (PARALLEL_WORKERS == 2) aster_publish(ASTER_TO_HART1, job);
        work_slice(0);
        if (PARALLEL_WORKERS == 2)
            while (aster_observe(ASTER_TO_HART0) != job) {}
        aster_fence();
        *ASTER_PERF_CONTROL = 2;
        aster_fence();
        struct aster_perf_counter snapshot[2][8];
        for (unsigned h = 0; h != 2; ++h) for (unsigned i = 0; i != 8; ++i) {
            volatile uint32_t *p = ASTER_PERF_BASE + h*64u + (i == 7 ? 16u : i*2u);
            snapshot[h][i] = aster_perf_read_counter(p, p+1);
        }
        uint32_t pass = snapshot[0][0].lo == snapshot[1][0].lo && snapshot[0][0].hi == snapshot[1][0].hi;
        uint32_t sum[2] = {0, 0};
        const uint32_t split = PARALLEL_WORKERS == 2 ? (PARALLEL_WORDS+1u)/2u : PARALLEL_WORDS;
        for (uint32_t i = 0; i != PARALLEL_WORDS; ++i) {
            const uint32_t expected = reference_word(seed, i);
            pass &= outputs[i] == expected;
            sum[i >= split] += expected ^ ((i+1u) * 0x9e3779b9u);
        }
        pass &= completed_words[0] == split && completed_words[1] == PARALLEL_WORDS-split;
        pass &= checksums[0] == sum[0] && checksums[1] == sum[1];
        if (job == PARALLEL_JOBS && PARALLEL_WORKERS == 2) aster_secondary_reset();
        emit(job, seed, pass, snapshot);
    }
    return 0;
}
