// 20.4's matrix: the reduction (docs/matrix.md §4.4), in v1's version and the scaling gate's, on AsterBench v12.
//   REDUCE_VERSION 1, v1's (software/benchmarks/workload_reduce.c, its computation and window): hart 0
//     refills the whole array inside the window; with two workers hart 1 sums the upper half (v1's epoch
//     handshake, hart 1 released just before START, so inside the window as in v1).
//   REDUCE_VERSION 2, the gate's (the owner, 7 October 2026; soc.md §9): each worker fills and sums its own
//     half, through the runtime's dispatch and join; hart 1 is released and ready before the window opens;
//     the window covers the four iterations' fills, sums, dispatches and joins.
// Both: 1,024 words, 4 iterations, seed 0x13570000, the checksum (checksum * 33) ^ total per iteration, which
// workload_reference.reduce_checksum recomputes. A warm run's untimed pass first (the same computation);
// a cold run's first pass is timed. Each hart stamps its work interval (its first fill or sum to its last).
#include <stdint.h>
#include <stdatomic.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"

#ifndef REDUCE_VERSION
#define REDUCE_VERSION 2
#endif
#ifndef REDUCE_WORKERS
#define REDUCE_WORKERS 2u
#endif
#define REDUCE_WORDS 1024u
#define REDUCE_ITERATIONS 4u
#define REDUCE_SEED 0x13570000u

_Static_assert(REDUCE_WORKERS == 1u || REDUCE_WORKERS == 2u, "workers must be 1 or 2");

static volatile uint32_t array[REDUCE_WORDS];
static volatile uint32_t stamped[2];                   // each hart's interval begun in this window

static void stamp_start(uint32_t hart) { if (!stamped[hart]) { stamped[hart] = 1; v12_work_start(hart); } }

static uint32_t sum_range(uint32_t begin, uint32_t end) {
    uint32_t sum = 0;
    for (uint32_t i = begin; i < end; ++i) sum += array[i];
    return sum;
}

static void fill_range(uint32_t begin, uint32_t end) {
    for (uint32_t i = begin; i < end; ++i) array[i] = REDUCE_SEED ^ (i * 0x1021u);
}

#if REDUCE_VERSION == 1
// v1's: hart 1 sums the upper half for each new epoch
static _Atomic uint32_t reduce_epoch, reduce_done;
static volatile uint32_t reduce_partial;

void aster_secondary_main(void) {
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&reduce_epoch, memory_order_acquire);
        if (epoch != 0u && epoch != last) {
            last = epoch;
            stamp_start(1);
            reduce_partial = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
            v12_work_end(1);
            atomic_store_explicit(&reduce_done, epoch, memory_order_release);
        }
    }
}

static uint32_t run(uint32_t pass) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
        stamp_start(0);
        fill_range(0, REDUCE_WORDS);
        __asm__ volatile ("fence rw,rw" ::: "memory");
#if REDUCE_WORKERS == 2
        const uint32_t epoch = pass * REDUCE_ITERATIONS + iteration;    // (each pass's epochs new to hart 1)
        atomic_store_explicit(&reduce_done, 0u, memory_order_relaxed);
        atomic_store_explicit(&reduce_epoch, epoch, memory_order_release);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
        while (atomic_load_explicit(&reduce_done, memory_order_acquire) != epoch) { }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        const uint32_t total = left + reduce_partial;
#else
        (void)pass;
        const uint32_t total = sum_range(0, REDUCE_WORDS);
#endif
        checksum = (checksum * 33u) ^ total;
    }
    v12_work_end(0);
    return checksum;
}
#else
// the gate's: each worker fills and sums its own half
#if REDUCE_WORKERS == 2
static volatile uint32_t upper_sum;

static void upper_half(void *arg) {
    (void)arg;
    stamp_start(1);
    fill_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
    upper_sum = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
    v12_work_end(1);
}
#endif

static uint32_t run(uint32_t pass) {
    (void)pass;
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
#if REDUCE_WORKERS == 2
        aster_smp_dispatch(upper_half, 0);
        stamp_start(0);
        fill_range(0, REDUCE_WORDS / 2u);
        const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
        aster_smp_join();
        const uint32_t total = left + upper_sum;
#else
        stamp_start(0);
        fill_range(0, REDUCE_WORDS);
        const uint32_t total = sum_range(0, REDUCE_WORDS);
#endif
        checksum = (checksum * 33u) ^ total;
    }
    v12_work_end(0);
    return checksum;
}
#endif

int main(void) {
    static struct v12_record record;
#if REDUCE_VERSION == 2 && REDUCE_WORKERS == 2
    aster_smp_start();                                 // (released and ready before the window)
#endif
#ifndef MATRIX_COLD
#if REDUCE_VERSION == 1 && REDUCE_WORKERS == 2
    *(volatile uint32_t *)0x20002004u = 1u;            // (warm: hart 1 is running before the timed pass)
#endif
    (void)run(0);                                      // the warm-up pass
#endif
    stamped[0] = stamped[1] = 0;
    v12_prepare();
#if defined(MATRIX_COLD) && REDUCE_VERSION == 1 && REDUCE_WORKERS == 2
    *(volatile uint32_t *)0x20002004u = 1u;            // v1's: released just before START
#endif
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t checksum = run(1);
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    v12_end(&record);
    record.name = REDUCE_VERSION == 1 ? "reduce_v1" : "reduce_fill";
    record.family = "coherence";
    record.method = REDUCE_WORKERS == 2 ? "multicore" : "scalar";
    record.window = "e2e";
#ifdef MATRIX_COLD
    record.cache_state = "cold";
#else
    record.cache_state = "warm";
#endif
    record.size = REDUCE_WORDS * 4u; record.iterations = REDUCE_ITERATIONS; record.param = REDUCE_WORKERS;
    record.seed = REDUCE_SEED; record.checksum = checksum; record.workers = REDUCE_WORKERS; record.pass = 1;
    v12_emit(&record);
    return 0;
}
