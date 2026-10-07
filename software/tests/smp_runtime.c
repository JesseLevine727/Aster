// Phase 20.2: the runtime's dispatch and join (software/runtime/aster_smp.h)
// on the two-hart SoC's device build. Hart 0 starts hart 1, then:
// - times an empty job's dispatch and join, SMP_TRIALS times (rdcycle: the
//   cycles from before the dispatch to after the join), and prints the
//   fewest, the most and the mean;
// - splits a fill-and-sum (each half: fill its words from a seed, then sum
//   them) between the harts, SMP_TRIALS times with a different seed each, and
//   checks the total against hart 0's own sum of the whole array, made after
//   the join (so hart 1's writes must be visible to it);
// - dispatches SMP_TRIALS jobs that each add their argument to a counter,
//   back to back with joins, and checks the counter;
// - stops hart 1, starts it again and dispatches once more (a restarted
//   worker).
// The SoC testbench checks every load either hart retires against memory.
// Ends with 1 to tohost (pass) or a failure code.
#include <stdint.h>

#include "aster.h"
#include "aster_smp.h"

#ifndef SMP_TRIALS
#define SMP_TRIALS 64
#endif
#define WORDS 1024

static uint32_t data[WORDS] __attribute__((aligned(64)));
struct half { uint32_t begin, end, seed, sum; } __attribute__((aligned(16)));
static struct half halves[2];
static volatile uint32_t counter __attribute__((aligned(16)));

static inline uint32_t cycles(void) {
    uint32_t c;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(c));
    return c;
}

static void nothing(void *arg) { (void)arg; }

static void fill_and_sum(void *arg) {
    struct half *h = arg;
    uint32_t x = h->seed ^ h->begin, sum = 0;
    for (uint32_t i = h->begin; i < h->end; ++i) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        data[i] = x;
    }
    for (uint32_t i = h->begin; i < h->end; ++i) sum += data[i];
    h->sum = sum;
}

static void add(void *arg) { counter = counter + (uint32_t)(uintptr_t)arg; }

int main(void) {
    aster_smp_start();
    uint32_t fewest = ~0u, most = 0, total = 0;
    for (unsigned t = 0; t < SMP_TRIALS; ++t) {
        const uint32_t before = cycles();
        aster_smp_dispatch(nothing, 0);
        aster_smp_join();
        const uint32_t spent = cycles() - before;
        fewest = spent < fewest ? spent : fewest;
        most = spent > most ? spent : most;
        total += spent;
    }
    int status = 0;
    for (unsigned t = 0; t < SMP_TRIALS && !status; ++t) {
        halves[0] = (struct half){0, WORDS / 2, 0x9e3779b9u * (t + 1), 0};
        halves[1] = (struct half){WORDS / 2, WORDS, 0x9e3779b9u * (t + 1), 0};
        aster_smp_dispatch(fill_and_sum, &halves[1]);
        fill_and_sum(&halves[0]);
        aster_smp_join();
        uint32_t whole = 0;
        for (unsigned i = 0; i < WORDS; ++i) whole += data[i];
        if (halves[0].sum + halves[1].sum != whole) status = 1;
        struct half check = {WORDS / 2, WORDS, 0x9e3779b9u * (t + 1), 0};
        uint32_t x = check.seed ^ check.begin, expected = 0;
        for (uint32_t i = check.begin; i < check.end; ++i) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; expected += x; }
        if (halves[1].sum != expected) status = 2;
    }
    uint32_t added = 0;
    for (unsigned t = 0; t < SMP_TRIALS && !status; ++t) {
        aster_smp_dispatch(add, (void *)(uintptr_t)(t + 1));
        aster_smp_join();
        added += t + 1;
        if (counter != added) status = 3;
    }
    aster_smp_stop();
    aster_smp_start();
    aster_smp_dispatch(add, (void *)(uintptr_t)1000u);
    aster_smp_join();
    if (!status && counter != added + 1000u) status = 4;
    aster_puts("SMP dispatch_join_cycles fewest="); aster_put_u32(fewest);
    aster_puts(" most="); aster_put_u32(most);
    aster_puts(" mean="); aster_put_u32(total / SMP_TRIALS);
    aster_puts(status ? " FAIL\n" : " PASS\n");
    return status;
}
