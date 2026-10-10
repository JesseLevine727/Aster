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
//                            overwrites). A stamp is a call and a counter read, about 20 cycles.
//   matrix_stamp_end(h)      hart h's work interval ended, in the window's last stretch only (hart 0 sets
//                            matrix_last before it hands that stretch out): one stamp a window, not one a stretch.
//   Hart 0's interval (matrix.md §10.7): it opens the window, so it starts at 0; it ends at the window's end when
//   hart 0 works after its last wait (matrix_hart0_whole, set after the window), else at the end of its last
//   share (matrix_stamp_end(0), where it only waits after it); 0 and 0 where hart 0 only starts an engine and
//   polls it
//   (matrix_hart0_none). One interval holds no gaps: where hart 0 polls between its stages, its interval spans
//   the polls.
//   The two-worker kernel window: the computation alone, without the runtime's dispatch and join. Hart 1 is
//   dispatched before the window and waits at a release flag (matrix_arm returns once hart 1 is waiting there,
//   so no part of the dispatch falls in the window); hart 0 opens the window, releases it (matrix_release),
//   runs its own share, waits for hart 1's done flag (matrix_await) and closes the window; aster_smp_join()
//   after. The window holds the shares and the two flags' handoff.
#ifndef MATRIX_WINDOW_H
#define MATRIX_WINDOW_H

#include <stdint.h>

#include "aster.h"
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

static volatile uint32_t matrix_last;                 // the window's last stretch is in hand

static inline void matrix_hart0_whole(struct v12_record *record) {
    record->hart[0].work_start = 0;
    record->hart[0].work_end = (uint32_t)record->hart[0].counter[ASTER_C_CYCLES];
}

static inline void matrix_hart0_none(struct v12_record *record) {
    record->hart[0].work_start = 0;
    record->hart[0].work_end = 0;
}

// 20.5 (tuning.md §6): each hart's share in the window's last stretch, begun and ended, for the overlap gate
// where hart 0's record interval spans its own wait (matrix.md §10.7 leaves the record as it is): printed beside
// the record on a MATRIX_SHARE line (matrix_share_emit). A stamp is a counter read, about 20 cycles in the window.
// A program whose last stretch is too short for a share (MNIST's and CIFAR's last layer) raises matrix_share_on
// around the stretch it stamps instead (their last image's first layer).
static volatile uint32_t matrix_share_at[2][2];
static volatile uint32_t matrix_share_on;

static inline void matrix_stamps_clear(void) {
    matrix_stamped[0] = matrix_stamped[1] = 0; matrix_last = 0; matrix_share_on = 0;
    matrix_share_at[0][0] = matrix_share_at[0][1] = matrix_share_at[1][0] = matrix_share_at[1][1] = 0;
}

static inline void matrix_share_begin(uint32_t hart) {
    if (matrix_last || matrix_share_on) matrix_share_at[hart & 1u][0] = aster_window_now();
}
static inline void matrix_share_end(uint32_t hart) {
    if (matrix_last || matrix_share_on) matrix_share_at[hart & 1u][1] = aster_window_now();
}

static inline void matrix_share_put(const char *key, uint32_t value) {
    char digits[11]; int n = 0;
    aster_putc(','); aster_puts(key); aster_putc('=');
    do { digits[n++] = (char)('0' + value % 10u); value /= 10u; } while (value);
    while (n) aster_putc(digits[--n]);
}

static inline void matrix_share_emit(const char *window) {      // (after the record, outside the window)
    aster_puts("MATRIX_SHARE,window="); aster_puts(window);
    matrix_share_put("h0_begin", matrix_share_at[0][0]); matrix_share_put("h0_end", matrix_share_at[0][1]);
    matrix_share_put("h1_begin", matrix_share_at[1][0]); matrix_share_put("h1_end", matrix_share_at[1][1]);
    aster_putc('\n');
}

static inline void matrix_stamp_start(uint32_t hart) {
    if (!matrix_stamped[hart & 1u]) { matrix_stamped[hart & 1u] = 1; v12_work_start(hart); }
}

static inline void matrix_stamp_end(uint32_t hart) { if (matrix_last) v12_work_end(hart); }

struct matrix_armed_line { volatile uint32_t word; uint32_t pad[3]; } __attribute__((aligned(16)));
static struct matrix_armed_line matrix_go, matrix_done, matrix_waiting;   // (each its own line, as the runtime's)
static aster_smp_fn volatile matrix_armed_fn;
static void *volatile matrix_armed_arg;

__attribute__((unused)) static void matrix_armed_worker(void *unused) {
    (void)unused;
    matrix_waiting.word = 1u;                          // (hart 1 at the release flag)
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
    matrix_waiting.word = 0u;
    aster_smp_dispatch(matrix_armed_worker, 0);             // (its fence w,w orders the words above first)
    while (!matrix_waiting.word) {}
    __asm__ volatile ("fence r,rw" ::: "memory");
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
