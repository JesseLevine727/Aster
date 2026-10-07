// Phase 20's litmus tests (milestone 20.0; docs/soc.md §10.3): two harts run
// RVWMO's two-hart shapes, and v1's coherent_litmus.c modes, ported, trial
// after trial, each hart after a random delay so that their accesses
// interleave differently. Hart 0 sets up each trial, publishes it, runs its
// side, waits for hart 1's, and counts the outcome — a small key built from
// the registers both harts read and the final memory — in
// litmus_counts[shape][key]. The counts lie between begin_signature and
// end_signature, so Spike (+signature) and the simulations dump them;
// scripts/litmus.py classifies them: an outcome RVWMO forbids fails the run,
// and the counts of the allowed ones are published.
//
// Every shape's instructions are inline assembly, so the compiler cannot
// reorder or merge them — but for v1's seq_cst shapes (_SC), whose point is
// the compiler's C11 lowering. x and y lie in different 16-byte lines
// (different banks of the Phase 20 memory); x2 shares x's 8-byte unit, and
// the _SAME shapes use it as their second location (v1's same-line modes).
// Shapes ending in _F put the fences that make their key outcome forbidden;
// MP, SB, LB, S, R and W22 are their unfenced forms, whose outcomes RVWMO
// allows and which are only counted. v1's eight modes: 0-3 are SB_SC_SAME,
// SB_SC, LB_SC_SAME, LB_SC; 4 PUB; 5 SB_F; 6 LRSC_PEER; 7 LRSC. LRSC_PEER's
// forbidden outcome (an sc failing with no write to its word) is this
// design's contract (soc.md §4.5), not RVWMO's, which allows spurious failures.
// A hart that reads two values returns them a byte each, and key() sends any
// read outside the shape's possible values to KEYS - 1, so keys cannot alias.
//
// Build flags: LITMUS_TRIALS (trials a shape), LITMUS_STEPS (increments a
// hart in the atomic shapes), LITMUS_SEED, LITMUS_PRINT (print the counts on
// the console as well).
#include <stdatomic.h>
#include <stdint.h>

#include "aster.h"

#ifndef LITMUS_TRIALS
#define LITMUS_TRIALS 200
#endif
#ifndef LITMUS_STEPS
#define LITMUS_STEPS 16
#endif
#ifndef LITMUS_SEED
#define LITMUS_SEED 0x5eed2020u
#endif

enum { MP, MP_F, SB, SB_F, LB, LB_F, S_F, R_F, W22_F, CORR, COWR, CORW, COWW, LRSC, LRSC_PEER, AMO, PUB,
       S, R, W22, SB_SC, LB_SC, SB_SC_SAME, LB_SC_SAME, MP_F_SAME, SB_F_SAME, SHAPES };
#define KEYS 16
#define NSHAPES 26                          // SHAPES, for the assembler
#define BAD (KEYS - 1)
_Static_assert(SHAPES == NSHAPES, "NSHAPES must equal SHAPES");
#define STR_(x) #x
#define STR(x) STR_(x)

struct lines {
    volatile uint32_t x, x2, pad0[2];       // line 0
    volatile uint32_t y, pad1[3];           // line 1
} __attribute__((aligned(64)));
static struct lines s;
static volatile uint32_t payload[16] __attribute__((aligned(64)));
static volatile uint32_t flag __attribute__((aligned(64)));
static _Atomic uint32_t go __attribute__((aligned(64)));
static _Atomic uint32_t done __attribute__((aligned(64)));
static _Atomic uint32_t ready __attribute__((aligned(64)));
static volatile uint32_t trial_shape, trial_seed, h1_result;

uint32_t litmus_counts[SHAPES * KEYS] __attribute__((aligned(16)));
__asm__(".globl begin_signature\n.set begin_signature, litmus_counts\n"
        ".globl end_signature\n.set end_signature, litmus_counts + " STR(NSHAPES * KEYS * 4) "\n");

static void delay(uint32_t n) {
    for (uint32_t i = 0; i < n; ++i) __asm__ volatile ("nop" ::: "memory");
}
static uint32_t pattern(uint32_t seed, unsigned i) { return (seed ^ (i * 0x1021u)) + 0x9e3779b9u; }

// lr/sc increments; returns the sc failures seen.
static uint32_t lrsc_increments(volatile uint32_t* p, unsigned n) {
    uint32_t failures = 0;
    for (unsigned i = 0; i < n; ++i) {
        uint32_t old, fail;
        for (;;) {
            __asm__ volatile ("lr.w %0, (%2)\n addi %0, %0, 1\n sc.w %1, %0, (%2)"
                              : "=&r"(old), "=&r"(fail) : "r"(p) : "memory");
            if (!fail) break;
            ++failures;
        }
    }
    return failures;
}

// One hart's side of a shape; returns what it read (hart 0's or hart 1's part of the key).
// Two reads, a byte each; a read beyond a byte makes the pair impossible (key BAD).
static uint32_t two_reads(uint32_t a, uint32_t b) { return a > 0xFFu || b > 0xFFu ? 0xFFFFFFFFu : a | b << 8; }

static int same_unit(unsigned shape) { return shape == SB_SC_SAME || shape == LB_SC_SAME || shape == MP_F_SAME || shape == SB_F_SAME; }

static uint32_t run(unsigned hart, unsigned shape, uint32_t seed) {
    volatile uint32_t *x = &s.x, *y = same_unit(shape) ? &s.x2 : &s.y;
    uint32_t a = 0, b = 0;
    const uint32_t one = 1, two = 2;
    switch (shape) {
    case MP: case MP_F: case MP_F_SAME:
        if (!hart) {
            if (shape == MP) __asm__ volatile ("sw %1, (%0)\n sw %1, (%2)" :: "r"(x), "r"(one), "r"(y) : "memory");
            else __asm__ volatile ("sw %1, (%0)\n fence w, w\n sw %1, (%2)" :: "r"(x), "r"(one), "r"(y) : "memory");
            return 0;
        }
        if (shape == MP) __asm__ volatile ("lw %0, (%2)\n lw %1, (%3)" : "=&r"(a), "=&r"(b) : "r"(y), "r"(x) : "memory");
        else __asm__ volatile ("lw %0, (%2)\n fence r, r\n lw %1, (%3)" : "=&r"(a), "=&r"(b) : "r"(y), "r"(x) : "memory");
        return two_reads(a, b);
    case SB_SC: case SB_SC_SAME:             // v1's modes 0-1: C11 seq_cst, as the compiler lowers it
        __atomic_store_n(hart ? y : x, 1u, __ATOMIC_SEQ_CST);
        return __atomic_load_n(hart ? x : y, __ATOMIC_SEQ_CST);
    case LB_SC: case LB_SC_SAME:             // v1's modes 2-3
        a = __atomic_load_n(hart ? y : x, __ATOMIC_SEQ_CST);
        __atomic_store_n(hart ? x : y, 1u, __ATOMIC_SEQ_CST);
        return a;
    case SB: case SB_F: case SB_F_SAME: {
        volatile uint32_t *mine = hart ? y : x, *theirs = hart ? x : y;
        if (shape == SB) __asm__ volatile ("sw %2, (%1)\n lw %0, (%3)" : "=&r"(a) : "r"(mine), "r"(one), "r"(theirs) : "memory");
        else __asm__ volatile ("sw %2, (%1)\n fence rw, rw\n lw %0, (%3)" : "=&r"(a) : "r"(mine), "r"(one), "r"(theirs) : "memory");
        return a;
    }
    case LB: case LB_F: {
        volatile uint32_t *reads = hart ? y : x, *writes = hart ? x : y;
        if (shape == LB) __asm__ volatile ("lw %0, (%1)\n sw %2, (%3)" : "=&r"(a) : "r"(reads), "r"(one), "r"(writes) : "memory");
        else __asm__ volatile ("lw %0, (%1)\n fence rw, rw\n sw %2, (%3)" : "=&r"(a) : "r"(reads), "r"(one), "r"(writes) : "memory");
        return a;
    }
    case S_F:
        if (!hart) { __asm__ volatile ("sw %1, (%0)\n fence w, w\n sw %2, (%3)" :: "r"(x), "r"(two), "r"(one), "r"(y) : "memory"); return 0; }
        __asm__ volatile ("lw %0, (%1)\n fence r, w\n sw %2, (%3)" : "=&r"(a) : "r"(y), "r"(one), "r"(x) : "memory");
        return a;
    case S:
        if (!hart) { __asm__ volatile ("sw %1, (%0)\n sw %2, (%3)" :: "r"(x), "r"(two), "r"(one), "r"(y) : "memory"); return 0; }
        __asm__ volatile ("lw %0, (%1)\n sw %2, (%3)" : "=&r"(a) : "r"(y), "r"(one), "r"(x) : "memory");
        return a;
    case R_F:
        if (!hart) { __asm__ volatile ("sw %1, (%0)\n fence w, w\n sw %1, (%2)" :: "r"(x), "r"(one), "r"(y) : "memory"); return 0; }
        __asm__ volatile ("sw %1, (%2)\n fence w, r\n lw %0, (%3)" : "=&r"(a) : "r"(two), "r"(y), "r"(x) : "memory");
        return a;
    case R:
        if (!hart) { __asm__ volatile ("sw %1, (%0)\n sw %1, (%2)" :: "r"(x), "r"(one), "r"(y) : "memory"); return 0; }
        __asm__ volatile ("sw %1, (%2)\n lw %0, (%3)" : "=&r"(a) : "r"(two), "r"(y), "r"(x) : "memory");
        return a;
    case W22_F:
        if (!hart) __asm__ volatile ("sw %1, (%0)\n fence w, w\n sw %2, (%3)" :: "r"(x), "r"(one), "r"(two), "r"(y) : "memory");
        else __asm__ volatile ("sw %1, (%0)\n fence w, w\n sw %2, (%3)" :: "r"(y), "r"(one), "r"(two), "r"(x) : "memory");
        return 0;
    case W22:
        if (!hart) __asm__ volatile ("sw %1, (%0)\n sw %2, (%3)" :: "r"(x), "r"(one), "r"(two), "r"(y) : "memory");
        else __asm__ volatile ("sw %1, (%0)\n sw %2, (%3)" :: "r"(y), "r"(one), "r"(two), "r"(x) : "memory");
        return 0;
    case CORR:
        if (!hart) { __asm__ volatile ("sw %1, (%0)" :: "r"(x), "r"(one) : "memory"); return 0; }
        __asm__ volatile ("lw %0, (%2)\n lw %1, (%2)" : "=&r"(a), "=&r"(b) : "r"(x) : "memory");
        return two_reads(a, b);
    case COWR:
        if (!hart) { __asm__ volatile ("sw %1, (%2)\n lw %0, (%2)" : "=&r"(a) : "r"(one), "r"(x) : "memory"); return a; }
        __asm__ volatile ("sw %1, (%0)" :: "r"(x), "r"(two) : "memory");
        return 0;
    case CORW:
        if (!hart) { __asm__ volatile ("lw %0, (%2)\n sw %1, (%2)" : "=&r"(a) : "r"(one), "r"(x) : "memory"); return a; }
        __asm__ volatile ("sw %1, (%0)" :: "r"(x), "r"(two) : "memory");
        return 0;
    case COWW:
        if (!hart) { __asm__ volatile ("sw %1, (%0)\n sw %2, (%0)" :: "r"(x), "r"(one), "r"(two) : "memory"); return 0; }
        __asm__ volatile ("lw %0, (%2)\n lw %1, (%2)" : "=&r"(a), "=&r"(b) : "r"(x) : "memory");
        return two_reads(a, b);
    case LRSC:
        return lrsc_increments(x, LITMUS_STEPS);
    case LRSC_PEER:                          // hart 1 writes the reserved word's line, never the word
        if (!hart) return lrsc_increments(x, LITMUS_STEPS);
        for (unsigned i = 0; i < 4 * LITMUS_STEPS; ++i) { s.x2 = i; s.y = i; }
        return 0;
    case AMO:
        for (unsigned i = 0; i < LITMUS_STEPS; ++i)
            __asm__ volatile ("amoadd.w zero, %1, (%0)" :: "r"(x), "r"(one) : "memory");
        return 0;
    case PUB:
        if (!hart) {
            for (unsigned i = 0; i < 16; ++i) payload[i] = pattern(seed, i);
            __asm__ volatile ("fence rw, w\n sw %1, (%0)" :: "r"(&flag), "r"(one) : "memory");
            return 0;
        }
        while (!flag) {}
        __asm__ volatile ("fence r, rw" ::: "memory");
        for (unsigned i = 0; i < 16; ++i) a |= payload[i] != pattern(seed, i);
        return a;
    default:
        return 0;
    }
}

// The outcome's key (scripts/litmus.py names each one and lists the forbidden):
// any value outside what the shape's stores can leave is BAD.
static int up_to(uint32_t r, uint32_t max) { return r != 0xFFFFFFFFu && (r & 0xFFu) <= max && (r >> 8) <= max; }
static uint32_t pair(uint32_t r, unsigned shift) { return (r & 0xFFu) | (r >> 8) << shift; }
static int one_or_two(uint32_t v) { return v == 1 || v == 2; }
static uint32_t key(unsigned shape, uint32_t r0, uint32_t r1, uint32_t fx, uint32_t fy) {
    switch (shape) {
    case MP: case MP_F: case MP_F_SAME: case CORR: return up_to(r1, 1) ? pair(r1, 1) : BAD;
    case SB: case SB_F: case SB_F_SAME: case LB: case LB_F: case SB_SC: case LB_SC: case SB_SC_SAME: case LB_SC_SAME:
        return r0 <= 1 && r1 <= 1 ? r0 | r1 << 1 : BAD;
    case S: case S_F: return r1 <= 1 && one_or_two(fx) ? r1 | (fx == 2) << 1 : BAD;
    case R: case R_F: return r1 <= 1 && one_or_two(fy) ? r1 | (fy == 2) << 1 : BAD;
    case W22: case W22_F: return one_or_two(fx) && one_or_two(fy) ? (fx == 2) | (fy == 2) << 1 : BAD;
    case COWR: case CORW: return r0 <= 2 && one_or_two(fx) ? r0 | (fx == 2) << 2 : BAD;
    case COWW: return fx == 2 && up_to(r1, 2) ? pair(r1, 2) : BAD;
    case LRSC: return fx != 2 * LITMUS_STEPS;
    case LRSC_PEER: return (r0 != 0) | (fx != LITMUS_STEPS) << 1;
    case AMO: return fx != 2 * LITMUS_STEPS;
    case PUB: return r1 != 0;
    default: return BAD;
    }
}

void aster_secondary_main(void) {
    atomic_store_explicit(&ready, 1, memory_order_release);
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&go, memory_order_acquire);
        if (epoch == last) continue;
        last = epoch;
        const uint32_t seed = trial_seed;
        delay((seed >> 8) & 63);
        h1_result = run(1, trial_shape, seed);
        atomic_store_explicit(&done, epoch, memory_order_release);
    }
}

int main(void) {
    *(volatile uint32_t*)0x20002004u = 1u;  // release hart 1 (SECONDARY_RUN; the shell's plain page ignores it)
    while (!atomic_load_explicit(&ready, memory_order_acquire)) {}
    uint32_t seed = LITMUS_SEED, epoch = 0;
    for (unsigned shape = 0; shape < SHAPES; ++shape)
        for (unsigned t = 0; t < LITMUS_TRIALS; ++t) {
            seed = seed * 1664525u + 1013904223u;
            s.x = 0; s.x2 = 0; s.y = 0; flag = 0;
            trial_shape = shape;
            trial_seed = seed;
            atomic_store_explicit(&go, ++epoch, memory_order_release);
            delay(8 + (seed & 63));
            const uint32_t r0 = run(0, shape, seed);
            while (atomic_load_explicit(&done, memory_order_acquire) != epoch) {}
            const uint32_t k = key(shape, r0, h1_result, s.x, same_unit(shape) ? s.x2 : s.y);
            ++litmus_counts[shape * KEYS + (k < KEYS ? k : KEYS - 1)];
        }
#ifdef LITMUS_PRINT
    for (unsigned shape = 0; shape < SHAPES; ++shape) {
        aster_puts("LITMUS shape=");
        aster_put_u32(shape);
        for (unsigned k = 0; k < KEYS; ++k) {
            aster_putc(' ');
            aster_put_u32(litmus_counts[shape * KEYS + k]);
        }
        aster_putc('\n');
    }
#endif
    return 0;
}
