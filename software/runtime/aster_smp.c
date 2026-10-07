// The Phase 20 runtime's dispatch and join (milestone 20.2): see aster_smp.h.
#include "aster_smp.h"
#include "aster_multicore.h"

struct aster_smp_state aster_smp __attribute__((aligned(64)));

void aster_smp_start(void) {
    aster_smp.ready.word = 0u;
    aster_smp.done.word = aster_smp.seq.word;
    __asm__ volatile ("fence w,w" ::: "memory");
    aster_secondary_release();
    while (aster_smp.ready.word != 1u) {}
    __asm__ volatile ("fence r,rw" ::: "memory");
}

void aster_smp_stop(void) {
    aster_secondary_reset();
}

void aster_smp_worker(void) {
    uint32_t last = aster_smp.seq.word;
    __asm__ volatile ("fence rw,w" ::: "memory");
    aster_smp.ready.word = 1u;
    for (;;) {
        uint32_t seq;
        while ((seq = aster_smp.seq.word) == last) {}
        __asm__ volatile ("fence r,rw" ::: "memory");
        aster_smp.fn(aster_smp.arg);
        __asm__ volatile ("fence rw,w" ::: "memory");
        aster_smp.done.word = seq;
        last = seq;
    }
}

__attribute__((weak)) void aster_secondary_main(void) {
    aster_smp_worker();
}
