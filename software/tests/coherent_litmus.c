#include "aster.h"
#include <stdatomic.h>

#ifndef LITMUS_EPOCHS
#define LITMUS_EPOCHS 128
#endif
#ifndef LITMUS_STEPS
#define LITMUS_STEPS 64
#endif
#ifndef LITMUS_SEED
#define LITMUS_SEED 0xa57e6u
#endif
_Static_assert(LITMUS_EPOCHS >= 2 && LITMUS_EPOCHS <= 1024, "epochs must be 2..1024");
_Static_assert(LITMUS_STEPS >= 2 && LITMUS_STEPS <= 1024, "steps must be 2..1024");

// Same-line words and deliberately conflicting, separate 4 KiB regions.
struct line { _Atomic uint32_t x, y; uint32_t unused[1022]; };
static struct line lines[3] __attribute__((aligned(4096)));
static _Atomic uint32_t request_epoch, done_epoch, booted, published;
static uint32_t requested_mode, requested_seed, worker_result, worker_error;
static uint32_t payload[16];
// Primary-owned observation transport. Last mailbox word is committed last,
// allowing the independent RTL host to score every trial, not just a PASS sum.
volatile uint32_t aster_litmus_trial[8] __attribute__((section(".private0")));
volatile uint32_t aster_litmus_summary[8][64] __attribute__((section(".private0")));

static void check(int ok) {
    if (!ok) { aster_puts("LITMUS FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
}
static uint32_t pattern(uint32_t seed, unsigned i) {
    return (seed ^ (i*0x1021u)) + 0x9e3779b9u;
}
static void delay(uint32_t seed) {
    for (unsigned i = 0; i < (seed & 31); ++i) __asm__ volatile ("nop" ::: "memory");
}
static uint32_t increment(_Atomic uint32_t *p) {
    uint32_t old, next, failure;
    __asm__ volatile ("1: lr.w.aq %0, (%3)\naddi %1, %0, 1\nsc.w.rl %2, %1, (%3)\nbnez %2, 1b"
                      : "=&r"(old), "=&r"(next), "=&r"(failure) : "r"(p) : "memory");
    return old;
}
__attribute__((noipa, section(".text.benchmark")))
uint32_t aster_litmus_kernel(unsigned hart, unsigned mode, uint32_t seed) {
    _Atomic uint32_t *x = &lines[0].x;
    _Atomic uint32_t *y = (mode & 1) ? &lines[1].x : &lines[0].y;
    delay(seed ^ (hart ? 0x5a5a5a5au : 0xa5a5a5a5u));
    if (mode <= 1) { // seq_cst store buffering: forbid r0=r1=0.
        atomic_store_explicit(hart ? y : x, 1, memory_order_seq_cst);
        return atomic_load_explicit(hart ? x : y, memory_order_seq_cst);
    }
    if (mode <= 3) { // seq_cst load buffering: forbid r0=r1=1.
        uint32_t read = atomic_load_explicit(hart ? y : x, memory_order_seq_cst);
        atomic_store_explicit(hart ? x : y, 1, memory_order_seq_cst);
        return read;
    }
    if (mode == 4) {
        uint32_t sum = 0;
        if (!hart) {
            for (unsigned i = 0; i < 16; ++i) { payload[i] = pattern(seed, i); sum += payload[i]; }
            atomic_store_explicit(&published, 1, memory_order_release);
        } else {
            while (!atomic_load_explicit(&published, memory_order_acquire)) {}
            for (unsigned i = 0; i < 16; ++i) {
                worker_error |= payload[i] != pattern(seed, i); sum += payload[i];
            }
        }
        return sum;
    }
    if (mode == 5) {
        uint32_t read;
        // Native explicit full-fence SB, independent of compiler C11 lowering.
        __asm__ volatile ("sw %1, (%2)\nfence rw, rw\nlw %0, (%3)"
                          : "=&r"(read) : "r"(1), "r"(hart ? y : x), "r"(hart ? x : y) : "memory");
        return read;
    }
    uint32_t sum = 0;
    if (!hart || mode == 7) {
        for (unsigned i = 0; i < LITMUS_STEPS; ++i) sum += increment(x);
    } else {
        for (unsigned i = 0; i < LITMUS_STEPS; ++i) {
            // Never write x: reads, same-line nonoverlapping writes and dirty
            // conflicting evictions must not manufacture SC failures on hart 0.
            sum += atomic_load_explicit(x, memory_order_relaxed);
            atomic_store_explicit(&lines[0].y, seed+i, memory_order_relaxed);
            atomic_store_explicit(&lines[1].x, seed^i, memory_order_relaxed);
            atomic_store_explicit(&lines[2].x, ~seed+i, memory_order_relaxed);
        }
    }
    return sum;
}

void aster_secondary_main(void) {
    atomic_store_explicit(&booted, 1, memory_order_release);
    for (uint32_t epoch = 1; epoch <= 8u*LITMUS_EPOCHS; ++epoch) {
        while (atomic_load_explicit(&request_epoch, memory_order_acquire) != epoch) {}
        worker_error = 0;
        worker_result = aster_litmus_kernel(1, requested_mode, requested_seed);
        atomic_store_explicit(&done_epoch, epoch, memory_order_release);
    }
}
int main(void) {
    check(*(volatile uint32_t *)0x20002008u == 2 && *(volatile uint32_t *)0x20003084u == 4);
    *(volatile uint32_t *)0x20002004u = 1;
    while (!atomic_load_explicit(&booted, memory_order_acquire)) {}
    for (unsigned mode = 0; mode < 8; ++mode) {
        uint32_t histogram[4] = {0, 0, 0, 0};
        __asm__ volatile ("fence rw,rw" ::: "memory");
        *(volatile uint32_t *)0x20003080u = 1;
        for (unsigned i = 0; i < LITMUS_EPOCHS; ++i) {
            const unsigned epoch = mode*LITMUS_EPOCHS+i+1;
            const uint32_t seed = LITMUS_SEED ^ (epoch*0x9e3779b9u);
            for (unsigned n = 0; n < 3; ++n) {
                atomic_store_explicit(&lines[n].x, 0, memory_order_relaxed);
                atomic_store_explicit(&lines[n].y, 0, memory_order_relaxed);
            }
            atomic_store_explicit(&published, 0, memory_order_relaxed);
            requested_mode = mode; requested_seed = seed;
            atomic_store_explicit(&request_epoch, epoch, memory_order_release);
            const uint32_t r0 = aster_litmus_kernel(0, mode, seed);
            while (atomic_load_explicit(&done_epoch, memory_order_acquire) != epoch) {}
            const uint32_t r1 = worker_result;
            check(!worker_error);
            const uint32_t x = atomic_load_explicit(&lines[0].x, memory_order_relaxed);
            const uint32_t y = atomic_load_explicit((mode & 1) ? &lines[1].x : &lines[0].y, memory_order_relaxed);
            unsigned outcome = 0;
            if (mode <= 3 || mode == 5) {
                check(r0 <= 1 && r1 <= 1 && x == 1 && y == 1);
                outcome = (r0 << 1) | r1;
                check(mode == 2 || mode == 3 ? outcome != 3 : outcome != 0);
            } else if (mode == 4) {
                uint32_t expected = 0;
                for (unsigned n = 0; n < 16; ++n) expected += pattern(seed, n);
                check(r0 == expected && r1 == expected && x == 0 && y == 0);
            } else if (mode == 6) {
                check(x == LITMUS_STEPS && y == seed+LITMUS_STEPS-1u);
                check(r0 == LITMUS_STEPS*(LITMUS_STEPS-1u)/2u && r1 <= LITMUS_STEPS*LITMUS_STEPS);
            } else {
                check(x == 2u*LITMUS_STEPS && y == 0);
                check(r0+r1 == LITMUS_STEPS*(2u*LITMUS_STEPS-1u));
            }
            ++histogram[outcome];
            aster_litmus_trial[0] = mode; aster_litmus_trial[1] = i+1;
            aster_litmus_trial[2] = r0; aster_litmus_trial[3] = r1;
            aster_litmus_trial[4] = x; aster_litmus_trial[5] = y;
            aster_litmus_trial[6] = 0; aster_litmus_trial[7] = epoch;
        }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        *(volatile uint32_t *)0x20003080u = 2;
        __asm__ volatile ("fence rw,rw" ::: "memory");
        volatile uint32_t *summary = aster_litmus_summary[mode];
        summary[0] = mode; summary[1] = LITMUS_EPOCHS; summary[2] = LITMUS_SEED;
        for (unsigned n = 0; n < 4; ++n) summary[3+n] = histogram[n];
        summary[7] = 0;
        for (unsigned h = 0; h < 2; ++h) for (unsigned event = 0; event < 14; ++event) {
            volatile uint32_t *p = (volatile uint32_t *)(0x20003000u+h*256u+event*8u);
            const struct aster_perf_counter count = aster_perf_read_counter(p, p+1);
            summary[8+h*28+event*2] = count.lo;
            summary[9+h*28+event*2] = count.hi;
        }
        aster_puts("LITMUS PASS mode="); aster_put_u32(mode); aster_putc('\n');
    }
    *(volatile uint32_t *)0x20002004u = 0;
    while (*(volatile uint32_t *)0x2000200cu & 2) {}
    return 0;
}
