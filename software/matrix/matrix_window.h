// 20.4's matrix: the windows' helpers (docs/matrix.md §4, "Measurement windows").
//   matrix_open(first)       START for a window's first interval, RESUME for a later one: v12's counters add on
//                            across RESUME, so a window may be the sum of several intervals (FREEZE between).
//   matrix_close()           FREEZE.
//   matrix_poison(p, bytes)  a pass's outputs filled with 0xA5 before the pass, outside every window, so that a
//                            pass which wrote nothing cannot pass on an earlier pass's results. Each hart
//                            poisons what it writes in the pass (hart 1 through a dispatch), so that the caches
//                            hold each line where the warm-up left it: a hart's stores invalidate the other's
//                            copies, and poisoning hart 1's lines from hart 0 would make its warm run colder.
//   matrix_stamp_start(h)    hart h's work interval begun, once a window (matrix_stamps_clear() before it):
//                            a hart that works in several stretches keeps its first start (v12_work_start
//                            overwrites).
//   The two-worker kernel window: the computation alone, without the runtime's dispatch and join. Hart 1 is
//   dispatched before the window and waits at a release flag (matrix_arm); hart 0 opens the window, releases
//   it (matrix_release), runs its own share, waits for hart 1's done flag (matrix_await) and closes the window;
//   aster_smp_join() after. The window holds the shares and the two flags' handoff.
#ifndef MATRIX_WINDOW_H
#define MATRIX_WINDOW_H

#include <stdint.h>

#include "aster_counters.h"
#include "aster_smp.h"
#include "asterbench_v12.h"

static inline void matrix_open(int first) {
    __asm__ volatile ("fence rw,rw" ::: "memory");
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = first ? ASTER_ABI4_START : ASTER_ABI4_RESUME;
    __asm__ volatile ("fence rw,rw" ::: "memory");
}

static inline void matrix_close(void) {
    __asm__ volatile ("fence rw,rw" ::: "memory");
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
}

static inline void matrix_poison(void *p, uint32_t bytes) {
    volatile uint8_t *b = (volatile uint8_t *)p;
    for (uint32_t i = 0; i < bytes; ++i) b[i] = 0xA5u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
}

static volatile uint32_t matrix_stamped[2];

static inline void matrix_stamps_clear(void) { matrix_stamped[0] = matrix_stamped[1] = 0; }

static inline void matrix_stamp_start(uint32_t hart) {
    if (!matrix_stamped[hart & 1u]) { matrix_stamped[hart & 1u] = 1; v12_work_start(hart); }
}

struct matrix_armed_line { volatile uint32_t word; uint32_t pad[3]; } __attribute__((aligned(16)));
static struct matrix_armed_line matrix_go, matrix_done;    // (each in its own line, as the runtime's words)
static aster_smp_fn volatile matrix_armed_fn;
static void *volatile matrix_armed_arg;

__attribute__((unused)) static void matrix_armed_worker(void *unused) {
    (void)unused;
    while (!matrix_go.word) {}
    __asm__ volatile ("fence r,rw" ::: "memory");
    matrix_armed_fn(matrix_armed_arg);
    __asm__ volatile ("fence rw,w" ::: "memory");
    matrix_done.word = 1u;
}

static inline void matrix_arm(aster_smp_fn fn, void *arg) {
    matrix_armed_fn = fn;
    matrix_armed_arg = arg;
    matrix_go.word = 0u;
    matrix_done.word = 0u;
    aster_smp_dispatch(matrix_armed_worker, 0);             // (its fence w,w orders the words above first)
}

static inline void matrix_release(void) {
    __asm__ volatile ("fence rw,w" ::: "memory");
    matrix_go.word = 1u;
}

static inline void matrix_await(void) {
    while (!matrix_done.word) {}
    __asm__ volatile ("fence r,rw" ::: "memory");
}

#endif
