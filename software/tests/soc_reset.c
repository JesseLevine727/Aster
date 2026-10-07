// Phase 20.2: hart 1 reset at random points (docs/soc.md §7.3, §10.3), on the
// two-hart SoC's device build. Hart 1 runs a loop of AMOs, lr/sc pairs,
// stores and loads on words hart 0 also uses; hart 0 releases it, works on the
// same words for a random time, and holds it again — sometimes releasing it
// at once (a hold of a cycle or two, so that answers owed to hart 1 are still
// due when it restarts, which the fabric must drop). Every load either hart
// retires is checked by the SoC testbench's memory checker, and every sc
// against the reservation rule; the testbench also counts the resets that
// caught requests, AMOs, refills and reservations in flight.
//
// The program's own check: hart 0's updates are never lost or doubled, though
// hart 1 updates the same words and is reset in the middle of its own:
// - amo.word: hart 0 adds 1 << 16 (amoadd); hart 1 only toggles and sets
//   bits 0-3 (amoxor, amoor, amoand), so bits 16-31 count hart 0's AMOs;
// - lrsc.word: hart 0 adds 1 << 16 with lr/sc; hart 1 increments bits 0-15
//   modulo 2^16 with lr/sc, so bits 16-31 count hart 0's successes;
// - hart 0's own lines, which hart 1 reads but never writes, hold what hart 0
//   last stored.
// It ends with 1 to tohost (pass) or a failure code, and prints a summary.
#include <stdint.h>

#include "aster.h"
#include "aster_multicore.h"

// Build flags: RESET_ROUNDS (holds of hart 1), RESET_SEED.
#ifndef RESET_ROUNDS
#define RESET_ROUNDS 300
#endif

struct line { volatile uint32_t word; uint32_t pad[3]; } __attribute__((aligned(16)));
static struct line amo, lrsc, alive;
static struct line mine[8];                  // hart 0 writes, hart 1 reads
static struct line theirs[8];                // hart 1 writes, hart 0 reads
#ifndef RESET_SEED
#define RESET_SEED 0x2020c0deu
#endif
static uint32_t rng_state = RESET_SEED;

static uint32_t rng(void) {
    rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17; rng_state ^= rng_state << 5;
    return rng_state;
}

static inline void amo_add(volatile uint32_t *p, uint32_t v) {
    __asm__ volatile ("amoadd.w zero, %1, (%0)" :: "r"(p), "r"(v) : "memory");
}

// lr/sc: add `add` to the word, keeping bits outside `mask` (the update is (old + add) & mask | old & ~mask).
static inline uint32_t lrsc_update(volatile uint32_t *p, uint32_t add, uint32_t mask) {
    uint32_t old, tries = 0, fail;
    do {
        __asm__ volatile ("lr.w %0, (%1)" : "=r"(old) : "r"(p) : "memory");
        const uint32_t next = ((old + add) & mask) | (old & ~mask);
        __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(fail) : "r"(p), "r"(next) : "memory");
        ++tries;
    } while (fail);
    return tries;
}

void aster_secondary_main(void) {
    uint32_t x = 0x1234567u + alive.word;
    for (;;) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        switch (x & 7u) {
        case 0: __asm__ volatile ("amoxor.w zero, %1, (%0)" :: "r"(&amo.word), "r"(1u) : "memory"); break;
        case 1: __asm__ volatile ("amoor.w zero, %1, (%0)" :: "r"(&amo.word), "r"(6u) : "memory"); break;
        case 2: __asm__ volatile ("amoand.w zero, %1, (%0)" :: "r"(&amo.word), "r"(~4u) : "memory"); break;
        case 3: case 4: lrsc_update(&lrsc.word, 1u, 0xFFFFu); break;
        case 5: theirs[x >> 29].word = x; break;
        default: (void)mine[(x >> 28) & 7u].word; break;
        }
        alive.word = alive.word + 1u;
    }
}

int main(void) {
    uint32_t my_amo = 0, my_lrsc = 0, tries = 0, quick = 0, seen = 0;
    for (unsigned round = 0; round < RESET_ROUNDS; ++round) {
        *ASTER_SECONDARY_RUN = 1u;
        if (round % 8u == 0u) {              // now and then, let hart 1 get going before the hold
            const uint32_t before = alive.word;
            while (alive.word == before) {}
        }
        const uint32_t steps = rng() % 48u;
        for (uint32_t i = 0; i < steps; ++i) {
            const uint32_t r = rng();
            switch (r & 3u) {
            case 0: amo_add(&amo.word, 1u << 16); ++my_amo; break;
            case 1: tries += lrsc_update(&lrsc.word, 1u << 16, 0xFFFF0000u); ++my_lrsc; break;
            case 2: mine[(r >> 8) & 7u].word = round * 64u + i; break;
            default: seen += theirs[(r >> 8) & 7u].word; break;
            }
        }
        *ASTER_SECONDARY_RUN = 0u;           // held: whatever it had in flight
        if (rng() % 3u == 0u) {              // released at once, and held again
            *ASTER_SECONDARY_RUN = 1u;
            *ASTER_SECONDARY_RUN = 0u;
            ++quick;
        }
        for (unsigned j = 0; j < 8; ++j) mine[j].word = round * 64u + 63u - j;
    }
    __asm__ volatile ("fence rw, rw" ::: "memory");
    int status = 0;
    if ((amo.word >> 16) != (my_amo & 0xFFFFu)) status = 1;
    if ((lrsc.word >> 16) != (my_lrsc & 0xFFFFu)) status = 2;
    for (unsigned j = 0; j < 8; ++j)
        if (mine[j].word != (RESET_ROUNDS - 1u) * 64u + 63u - j) status = 3;
    aster_puts("RESET rounds="); aster_put_u32(RESET_ROUNDS);
    aster_puts(" quick="); aster_put_u32(quick);
    aster_puts(" amos="); aster_put_u32(my_amo);
    aster_puts(" lrsc="); aster_put_u32(my_lrsc);
    aster_puts(" lrsc_tries="); aster_put_u32(tries);
    aster_puts(" hart1_steps="); aster_put_u32(alive.word);
    aster_puts(status ? " FAIL\n" : " PASS\n");
    (void)seen;
    return status;
}
