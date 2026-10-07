// The Phase 20 runtime's dispatch and join (milestone 20.2; docs/soc.md §7.3,
// §9): hart 0 hands hart 1 a function and its argument and later waits for it
// to finish. Main memory is coherent on the Phase 20 SoC (each data cache
// snoops the other's writes, soc.md §4.6), so the handshake is plain shared
// memory, not v1's uncached mailboxes: hart 1 spins on the dispatch word in
// its own cache, and hart 0's store to it invalidates that line, so its next
// poll refills and sees the new job; hart 0 spins on the completion word the
// same way. The three words lie in separate 16-byte lines (the caches' line),
// so neither hart's polling is disturbed by the other's other writes.
//
// Ordering (RVWMO): dispatch writes the job, then `fence w,w`, then the
// sequence number; the worker reads the sequence number, `fence r,rw`, then
// the job and its data. The worker ends with `fence rw,w` before it writes the
// completion; join reads it and `fence r,rw` before hart 0 reads the results.
//
// Use (link software/runtime/aster_smp.c; start_multicore_aster.S):
//   aster_smp_start();                    // hart 0: release hart 1, wait until it is ready
//   aster_smp_dispatch(fn, arg);          // hart 1 runs fn(arg)
//   ...                                   // hart 0's own share
//   aster_smp_join();                     // wait for fn to return
//   aster_smp_stop();                     // hold hart 1 in reset again (optional)
// aster_smp.c's weak aster_secondary_main runs the worker; a program that
// provides its own can call aster_smp_worker(). One job at a time: dispatch
// only after the last job's join.
#ifndef ASTER_SMP_H
#define ASTER_SMP_H

#include <stdint.h>

typedef void (*aster_smp_fn)(void *arg);

struct aster_smp_line {
    volatile uint32_t word;
    uint32_t pad[3];
} __attribute__((aligned(16)));

struct aster_smp_state {
    struct aster_smp_line seq;        // hart 0 writes: the job's number (its fn and arg beside it)
    aster_smp_fn volatile fn;         // (not polled: in the line after seq, read after the fence)
    void *volatile arg;
    uint32_t pad[2];
    struct aster_smp_line done;       // hart 1 writes: the number of the last job finished
    struct aster_smp_line ready;      // hart 1 writes: 1 once it is waiting for jobs
};
extern struct aster_smp_state aster_smp;

void aster_smp_start(void);
void aster_smp_stop(void);
void aster_smp_worker(void) __attribute__((noreturn));

static inline void aster_smp_dispatch(aster_smp_fn fn, void *arg) {
    aster_smp.fn = fn;
    aster_smp.arg = arg;
    __asm__ volatile ("fence w,w" ::: "memory");
    aster_smp.seq.word = aster_smp.seq.word + 1u;
}

static inline void aster_smp_join(void) {
    const uint32_t seq = aster_smp.seq.word;
    while (aster_smp.done.word != seq) {}
    __asm__ volatile ("fence r,rw" ::: "memory");
}

// Whether the last job has finished (join without waiting).
static inline int aster_smp_finished(void) {
    const int finished = aster_smp.done.word == aster_smp.seq.word;
    __asm__ volatile ("fence r,rw" ::: "memory");
    return finished;
}

#endif
