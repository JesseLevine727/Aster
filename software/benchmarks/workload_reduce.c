// AsterBench v10: parallel reduction on the coherent RV32IMA SoC.
// One or two harts sum disjoint halves of a shared-RAM array; the secondary
// publishes its partial and the primary combines. The host oracle recomputes
// the exact total independently.
#include <stdint.h>
#include <stdatomic.h>
#include "aster.h"
#include "workload_coh.h"

#ifndef REDUCE_WORDS
#define REDUCE_WORDS 1024u
#endif
#ifndef REDUCE_WORKERS
#define REDUCE_WORKERS 2u
#endif
#ifndef REDUCE_ITERATIONS
#define REDUCE_ITERATIONS 4u
#endif
#ifndef REDUCE_SEED
#define REDUCE_SEED 0x13570000u
#endif

_Static_assert((REDUCE_WORDS & (REDUCE_WORDS - 1u)) == 0u, "reduce words must be a power of two");
_Static_assert(REDUCE_WORKERS == 1u || REDUCE_WORKERS == 2u, "workers must be 1 or 2");

static volatile uint32_t array[REDUCE_WORDS];
#if REDUCE_WORKERS == 2
static _Atomic uint32_t reduce_epoch, reduce_done;
static volatile uint32_t reduce_partial;
#endif

static uint32_t sum_range(uint32_t begin, uint32_t end) {
    uint32_t sum = 0;
    for (uint32_t i = begin; i < end; ++i) sum += array[i];
    return sum;
}

void aster_secondary_main(void) {
#if REDUCE_WORKERS == 2
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&reduce_epoch, memory_order_acquire);
        if (epoch != 0u && epoch != last) {
            last = epoch;
            reduce_partial = sum_range(REDUCE_WORDS / 2u, REDUCE_WORDS);
            atomic_store_explicit(&reduce_done, epoch, memory_order_release);
        }
    }
#else
    for (;;) __asm__ volatile ("" ::: "memory");
#endif
}

int main(void) {
    if (coh_reg(COH_CPU_ABI) != COH_CPU_ABI_VALUE) {
        aster_puts("REDUCE BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
#if REDUCE_WORKERS == 2
    *(volatile uint32_t *)0x20002004u = 1u;  // release the secondary once
#endif
    *(volatile uint32_t *)COH_CONTROL = 1u;  // start the common counter window
    __asm__ volatile ("fence rw,rw" ::: "memory");

    uint32_t checksum = 0;
    for (uint32_t iteration = 1; iteration <= REDUCE_ITERATIONS; ++iteration) {
        for (uint32_t i = 0; i < REDUCE_WORDS; ++i) array[i] = REDUCE_SEED ^ (i * 0x1021u);
        __asm__ volatile ("fence rw,rw" ::: "memory");
#if REDUCE_WORKERS == 2
        atomic_store_explicit(&reduce_done, 0u, memory_order_relaxed);
        atomic_store_explicit(&reduce_epoch, iteration, memory_order_release);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        const uint32_t left = sum_range(0, REDUCE_WORDS / 2u);
        while (atomic_load_explicit(&reduce_done, memory_order_acquire) != iteration) { }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        const uint32_t total = left + reduce_partial;
#else
        const uint32_t total = sum_range(0, REDUCE_WORDS);
#endif
        checksum = (checksum * 33u) ^ total;
    }
    *(volatile uint32_t *)COH_CONTROL = 2u;  // freeze
    __asm__ volatile ("fence rw,rw" ::: "memory");

#if REDUCE_WORKERS == 2
    aster_workload_emit_coh("reduce_parallel", "cpu", REDUCE_WORDS * 4u, REDUCE_ITERATIONS,
                            2u, REDUCE_SEED, checksum, 1u);
#else
    aster_workload_emit_coh("reduce_scalar", "cpu", REDUCE_WORDS * 4u, REDUCE_ITERATIONS,
                            1u, REDUCE_SEED, checksum, 1u);
#endif
    for (;;) { }
}
