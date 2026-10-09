// 20.4's matrix: the reduction (docs/matrix.md §4.4), in v1's version and the scaling gate's, on AsterBench v12.
//   REDUCE_VERSION 1, v1's (software/benchmarks/workload_reduce.c, its computation and window): hart 0
//     refills the whole array inside the window; with two workers hart 1 sums the upper half (v1's epoch
//     handshake; a cold run releases hart 1 just before START, as v1 did, a warm run before its warm-up).
//   REDUCE_VERSION 2, the gate's (the owner, 7 October 2026; soc.md §9): each worker fills and sums its own
//     half, through the runtime's dispatch and join; hart 1 is released and ready before the window opens;
//     the window covers the four iterations' fills, sums, dispatches and joins.
// Both: 1,024 words, 4 iterations, seed 0x13570000, the checksum (checksum * 33) ^ total per iteration, which
// workload_reference.reduce_checksum recomputes. Two windows (matrix.md §4):
//   e2e     the version's own, as above.
//   kernel  the sums alone, the array in place: each iteration's array filled before its interval (v1's by
//           hart 0; the gate's by each worker, its half), the four intervals summed. Two workers' shares are
//           released inside it: v1's by its epoch (hart 1 waits on it), the gate's by a flag (matrix_window.h).
// A cold run (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is, for each window, an
// untimed pass of the same code, then the window; the array and the partial sums poisoned before each. Hart 1
// stamps its work interval (its first fill or sum to its last); hart 0's is the window, which ends in its own add
// and fold (matrix_window.h).
#include <stdint.h>
#include <stdatomic.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

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
static volatile uint32_t upper_sum;                    // hart 1's partial sum

// (the computation's functions out of line, noinline, so that their code is the same in every caller and build)
static __attribute__((noinline)) uint32_t sum_range(uint32_t begin, uint32_t end) {
    uint32_t sum = 0;
    for (uint32_t i = begin; i < end; ++i) sum += array[i];
    return sum;
}

static __attribute__((noinline)) void fill_range(uint32_t begin, uint32_t end) {
    for (uint32_t i = begin; i < end; ++i) array[i] = REDUCE_SEED ^ (i * 0x1021u);
}

#if REDUCE_VERSION == 1
// v1's: hart 1 sums the upper half for each new epoch
static _Atomic uint32_t reduce_epoch, reduce_done;
static __attribute__((unused)) uint32_t next_epoch;   // (two workers only)

void aster_secondary_main(void) {
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&reduce_epoch, memory_order_acquire);
        if (epoch != 0u && epoch != last) {
            last = epoch;
            matrix_stamp_start(1);
            upper_sum = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
            matrix_stamp_end(1);
            atomic_store_explicit(&reduce_done, epoch, memory_order_release);
        }
    }
}

static uint32_t sum_all(void) {                        // (in the window) v1's split sum
#if REDUCE_WORKERS == 2
    const uint32_t epoch = ++next_epoch;               // (each pass's epochs new to hart 1)
    atomic_store_explicit(&reduce_done, 0u, memory_order_relaxed);
    atomic_store_explicit(&reduce_epoch, epoch, memory_order_release);
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
    while (atomic_load_explicit(&reduce_done, memory_order_acquire) != epoch) { }
    __asm__ volatile ("fence rw,rw" ::: "memory");
    return left + upper_sum;
#else
    return sum_range(0, REDUCE_WORDS);
#endif
}

static uint32_t run_e2e(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
        matrix_last = iteration == REDUCE_ITERATIONS;
        fill_range(0, REDUCE_WORDS);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        checksum = (checksum * 33u) ^ sum_all();
    }
    return checksum;
}

static uint32_t run_kernel(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
        fill_range(0, REDUCE_WORDS);                   // (outside: v1's fill, by hart 0)
        matrix_last = iteration == REDUCE_ITERATIONS;
        matrix_open(iteration == 1);
        const uint32_t total = sum_all();
        matrix_close();
        checksum = (checksum * 33u) ^ total;
    }
    return checksum;
}
#else
// the gate's: each worker fills and sums its own half
#if REDUCE_WORKERS == 2
static void upper_half(void *arg) {                    // the e2e window's share: fill and sum
    (void)arg;
    matrix_stamp_start(1);
    fill_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
    upper_sum = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
    matrix_stamp_end(1);
}

static void upper_fill(void *arg) { (void)arg; fill_range(REDUCE_WORDS / 2u, REDUCE_WORDS); }

static void upper_sum_only(void *arg) {                // the kernel window's share
    (void)arg;
    matrix_stamp_start(1);
    upper_sum = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
    matrix_stamp_end(1);
}
#endif

static uint32_t run_e2e(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
#if REDUCE_WORKERS == 2
        matrix_last = iteration == REDUCE_ITERATIONS;
        aster_smp_dispatch(upper_half, 0);
        fill_range(0, REDUCE_WORDS / 2u);
        const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
        aster_smp_join();
        const uint32_t total = left + upper_sum;
#else
        fill_range(0, REDUCE_WORDS);
        const uint32_t total = sum_range(0, REDUCE_WORDS);
#endif
        checksum = (checksum * 33u) ^ total;
    }
    return checksum;
}

static uint32_t run_kernel(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
#if REDUCE_WORKERS == 2
        aster_smp_dispatch(upper_fill, 0);             // (outside: each worker fills its half)
        fill_range(0, REDUCE_WORDS / 2u);
        aster_smp_join();
        matrix_last = iteration == REDUCE_ITERATIONS;
        matrix_arm(upper_sum_only, 0);
        matrix_open(iteration == 1);
        matrix_release();
        const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
        matrix_await();
        const uint32_t total = left + upper_sum;
        matrix_close();
        aster_smp_join();
#else
        fill_range(0, REDUCE_WORDS);
        matrix_open(iteration == 1);
        const uint32_t total = sum_range(0, REDUCE_WORDS);
        matrix_close();
#endif
        checksum = (checksum * 33u) ^ total;
    }
    return checksum;
}
#endif

static void emit(struct v12_record *record, const char *window, uint32_t checksum) {
    matrix_hart0_whole(record);                        // (every window ends in hart 0's add and fold)
    record->name = REDUCE_VERSION == 1 ? "reduce_v1" : "reduce_fill";
    record->family = "coherence";
    record->method = REDUCE_WORKERS == 2 ? "multicore" : "scalar";
    record->window = window;
    record->cache_state = MATRIX_CACHE_STATE;
    record->size = REDUCE_WORDS * 4u; record->iterations = REDUCE_ITERATIONS; record->param = REDUCE_WORKERS;
    record->seed = REDUCE_SEED; record->checksum = checksum; record->workers = REDUCE_WORKERS; record->pass = 1;
    v12_emit(record);
}

#if REDUCE_VERSION == 2 && REDUCE_WORKERS == 2
static void upper_poison(void *arg) {                  // (hart 1's half and its sum, by hart 1: matrix_window.h)
    (void)arg;
    matrix_poison((void *)&array[REDUCE_WORDS / 2u], sizeof array / 2u);
    upper_sum = 0xA5A5A5A5u;
}
#endif

static void poison(void) {
#if REDUCE_VERSION == 2 && REDUCE_WORKERS == 2
    aster_smp_dispatch(upper_poison, 0);
    matrix_poison((void *)array, sizeof array / 2u);
    aster_smp_join();
#else
    // (v1's: hart 0 refills the whole array in every iteration; hart 1's sum is one word)
    matrix_poison((void *)array, sizeof array);
    upper_sum = 0xA5A5A5A5u;
#endif
    matrix_stamps_clear();
}

int main(void) {
    static struct v12_record record;
#if REDUCE_VERSION == 2 && REDUCE_WORKERS == 2
    aster_smp_start();                                 // (released and ready before the window)
#endif
    if (!matrix_cold) {
#if REDUCE_VERSION == 1 && REDUCE_WORKERS == 2
        *(volatile uint32_t *)0x20002004u = 1u;        // (warm: hart 1 is running before the timed pass)
#endif
        (void)run_e2e();                               // the warm-up pass
    }
    poison();
    v12_prepare();
#if REDUCE_VERSION == 1 && REDUCE_WORKERS == 2
    if (matrix_cold) *(volatile uint32_t *)0x20002004u = 1u;   // v1's: released just before START
#endif
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t checksum = run_e2e();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    v12_end(&record);
    emit(&record, "e2e", checksum);
    if (!matrix_cold) {
        (void)run_kernel();                            // the kernel window's warm-up
        poison();
        v12_prepare();
        const uint32_t kernel_sum = run_kernel();
        v12_end(&record);
        emit(&record, "kernel", kernel_sum);
    }
    return 0;
}
