// Phase 20.2: what the fabric's timing levers cost or save in cycles, on two
// two-hart patterns (a measurement, not a gate; phase20.md, 20.2's levers):
// - twin: both harts sum the same 24 KiB array from cold caches, in step, so
//   their data caches refill the same lines of the same banks together (the
//   case 20.1's second chance, D_ON_B, serves);
// - shared: hart 1 rewrites an array while hart 0 sums it, so reads and
//   writes of the same units meet in the banks (the case the read-beside-write
//   bypass serves);
// - private: each hart sums its own half (no sharing; the control);
// - lines: both harts read one word of each line of the same array, in step
//   (refill-bound: the most the two data caches can contend for the banks).
// Each pattern runs from the dispatch to the join, timed on hart 0 (mcycle),
// and prints its cycles; the sums are checked.
#include <stdint.h>

#include "aster.h"
#include "aster_smp.h"

#define WORDS 6144
static uint32_t data[WORDS] __attribute__((aligned(64)));
static uint32_t sums[2] __attribute__((aligned(64)));

static inline uint32_t cycles(void) {
    uint32_t c;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(c));
    return c;
}

static uint32_t sum(uint32_t begin, uint32_t end) {
    uint32_t s = 0;
    for (uint32_t i = begin; i < end; ++i) s += data[i];
    return s;
}

static void twin(void *arg) { (void)arg; sums[1] = sum(0, WORDS); }
static void rewrite(void *arg) { (void)arg; for (uint32_t i = 0; i < WORDS; ++i) data[i] = data[i] + 0u; }
static void upper(void *arg) { (void)arg; sums[1] = sum(WORDS / 2, WORDS); }
static uint32_t lines(void) {
    uint32_t s = 0;
    for (uint32_t i = 0; i < WORDS; i += 4) s += data[i];
    return s;
}
static void lines1(void *arg) { (void)arg; sums[1] = lines(); }

static void evict(void) {                            // hart 0's data cache: another 4 KiB read
    static volatile uint32_t other[1024] __attribute__((aligned(64)));
    uint32_t s = 0;
    for (unsigned i = 0; i < 1024; ++i) s += other[i];
    (void)s;
}
static void evict1(void *arg) { (void)arg; evict(); }

int main(void) {
    for (uint32_t i = 0; i < WORDS; ++i) data[i] = i * 2654435761u;
    const uint32_t expected = sum(0, WORDS);
    aster_smp_start();
    int status = 0;

    aster_smp_dispatch(evict1, 0); evict(); aster_smp_join();
    uint32_t t0 = cycles();
    aster_smp_dispatch(twin, 0);
    sums[0] = sum(0, WORDS);
    aster_smp_join();
    const uint32_t twin_cycles = cycles() - t0;
    if (sums[0] != expected || sums[1] != expected) status = 1;

    aster_smp_dispatch(evict1, 0); evict(); aster_smp_join();
    t0 = cycles();
    aster_smp_dispatch(rewrite, 0);
    sums[0] = sum(0, WORDS);
    aster_smp_join();
    const uint32_t shared_cycles = cycles() - t0;
    if (sums[0] != expected) status = 2;

    aster_smp_dispatch(evict1, 0); evict(); aster_smp_join();
    t0 = cycles();
    aster_smp_dispatch(upper, 0);
    sums[0] = sum(0, WORDS / 2);
    aster_smp_join();
    const uint32_t private_cycles = cycles() - t0;
    if (sums[0] + sums[1] != expected) status = 3;

    aster_smp_dispatch(evict1, 0); evict(); aster_smp_join();
    const uint32_t line_expected = lines();
    aster_smp_dispatch(evict1, 0); evict(); aster_smp_join();
    t0 = cycles();
    aster_smp_dispatch(lines1, 0);
    sums[0] = lines();
    aster_smp_join();
    const uint32_t lines_cycles = cycles() - t0;
    if (sums[0] != line_expected || sums[1] != line_expected) status = 4;

    aster_puts("TWIN twin="); aster_put_u32(twin_cycles);
    aster_puts(" shared="); aster_put_u32(shared_cycles);
    aster_puts(" private="); aster_put_u32(private_cycles);
    aster_puts(" lines="); aster_put_u32(lines_cycles);
    aster_puts(status ? " FAIL\n" : " PASS\n");
    return status;
}
