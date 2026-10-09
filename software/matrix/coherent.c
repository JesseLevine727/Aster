// 20.4's matrix: the coherence cases of docs/matrix.md §4.4, v1's software/benchmarks/coherent.c ported: its nine
// kernels verbatim (aster_coherent_kernel) and a new producer/consumer. COHERENT_KIND:
//   0 atomic_add   1 lrsc_counter   2 cas_counter   3 lock_sum      (the counters; 1 or 2 workers)
//   4 false_shared 5 padded                                         (adjacent words, and a page apart; 1 or 2)
//   6 ping_pong    7 spsc_queue                                     (2 harts)
//   8 shared_mix                                                    (each worker mixes its half; 1 or 2)
//   9 producer_consumer: hart 0 fills a buffer of COHERENT_ITEMS words and hands it over by a flag; hart 1 waits
//     for the flag, sums the buffer and hands it back by another; COHERENT_ROUNDS rounds (new; 2 harts)
// Two windows (matrix.md §4):
//   e2e     v1's: hart 1 released from reset inside the window (its cold start measured, v1's choice), hart 0's
//           kernel, its wait for hart 1's done; hart 1 held in reset again after it.
//   kernel  the kernels alone, the state reset before: hart 1 under the runtime, armed before the window and
//           released by a flag inside it (matrix_window.h).
// One job, v1's first (its seed COHERENT_SEED ^ 0x9e3779b9). A cold run (matrix_cold.h) is the e2e window as the
// first pass after reset; a warm run is, for each window, the same code untimed first. v1's checks (every count,
// sum and value, both harts' units) give PASS; the checksum is the harts' summed sums, which scripts/matrix.py's
// oracle recomputes.
#include <stdint.h>
#include <stdatomic.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef COHERENT_KIND
#define COHERENT_KIND 0
#endif
#ifndef COHERENT_ITEMS
#define COHERENT_ITEMS 64
#endif
#ifndef COHERENT_ROUNDS
#define COHERENT_ROUNDS 4
#endif
#ifndef COHERENT_WORKERS
#define COHERENT_WORKERS 2
#endif
#ifndef COHERENT_SEED
#define COHERENT_SEED 0x13570000u
#endif
_Static_assert(COHERENT_KIND >= 0 && COHERENT_KIND < 10, "unknown coherent workload");
_Static_assert(COHERENT_ITEMS >= 2 && COHERENT_ITEMS <= 1024, "items must be 2..1024");
_Static_assert(COHERENT_WORKERS == 1 || COHERENT_WORKERS == 2, "workers must be 1 or 2");
_Static_assert(COHERENT_KIND < 6 || COHERENT_KIND == 8 || COHERENT_WORKERS == 2, "a two-hart case");

#define ALWAYS_INLINE __attribute__((always_inline)) inline

// ---- v1's kernels, verbatim (software/benchmarks/coherent.c) ----
static _Atomic uint32_t epoch, done, total, lock, turn, head, tail;
static uint32_t seed_for_job, locked_sum, request[2], reply[2], queue[8][2];
static uint32_t units[2], sums[2], errors[2];
static _Atomic uint32_t adjacent[4] __attribute__((aligned(4096)));   // (two used: the line holds nothing else)
struct padded_word { _Atomic uint32_t value; uint32_t padding[1023]; };
static struct padded_word separate[2] __attribute__((aligned(4096)));
volatile uint32_t aster_coherent_output[COHERENT_ITEMS];

static ALWAYS_INLINE uint32_t payload(uint32_t seed, uint32_t i) {
    return (seed ^ (i * 0x1021u)) + 0x9e3779b9u;
}
static ALWAYS_INLINE uint32_t mix(uint32_t x, uint32_t r) {
    x += r + 0x9e3779b9u;
    x = (x ^ (x >> 16)) * 0x7feb352du;
    x = (x ^ (x >> 15)) * 0x846ca68bu;
    return x ^ (x >> 16);
}
static ALWAYS_INLINE uint32_t lrsc_increment(_Atomic uint32_t *p) {
    uint32_t old, next, failed;
    // Four-instruction constrained retry loop, no intervening loads/stores.
    __asm__ volatile ("1: lr.w.aq %0, (%3)\naddi %1, %0, 1\n"
                      "sc.w.rl %2, %1, (%3)\nbnez %2, 1b"
                      : "=&r"(old), "=&r"(next), "=&r"(failed) : "r"(p) : "memory");
    return old;
}

#if COHERENT_KIND == 9
// ---- producer/consumer (new): a buffer handed over by a flag, and back by another ----
static uint32_t buffer[COHERENT_ITEMS] __attribute__((aligned(64)));
static _Atomic uint32_t full, empty;

static ALWAYS_INLINE uint32_t round_seed(uint32_t seed, uint32_t r) { return seed + r * 0x9e3779b9u; }

__attribute__((noipa))
void aster_coherent_kernel(unsigned hart, uint32_t seed) {
    uint32_t sum = 0, error = 0, count = 0;
    for (uint32_t r = 0; r < COHERENT_ROUNDS; ++r) {
        if (!hart) {
            while (atomic_load_explicit(&empty, memory_order_acquire) != r) {}
            for (uint32_t i = 0; i < COHERENT_ITEMS; ++i) {
                buffer[i] = payload(round_seed(seed, r), i);
                sum += buffer[i];
            }
            atomic_store_explicit(&full, r + 1u, memory_order_release);
        } else {
            while (atomic_load_explicit(&full, memory_order_acquire) != r + 1u) {}
            for (uint32_t i = 0; i < COHERENT_ITEMS; ++i) {
                const uint32_t value = buffer[i];
                error |= value != payload(round_seed(seed, r), i);
                sum += value;
            }
            atomic_store_explicit(&empty, r + 1u, memory_order_release);
        }
        count += COHERENT_ITEMS;
    }
    units[hart] = count; sums[hart] = sum; errors[hart] = error;
}
#else
__attribute__((noipa))
void aster_coherent_kernel(unsigned hart, uint32_t seed) {
    const uint32_t split = COHERENT_WORKERS == 2 ? (COHERENT_ITEMS+1u)/2u : COHERENT_ITEMS;
    const uint32_t begin = hart ? split : 0;
    const uint32_t end = hart ? COHERENT_ITEMS : split;
    uint32_t sum = 0, error = 0, count = 0;
    if (COHERENT_KIND <= 5) {
        for (uint32_t i = begin; i < end; ++i) {
            if (COHERENT_KIND == 0) sum += atomic_fetch_add_explicit(&total, 1, memory_order_relaxed);
            else if (COHERENT_KIND == 1) sum += lrsc_increment(&total);
            else if (COHERENT_KIND == 2) {
                uint32_t old = atomic_load_explicit(&total, memory_order_relaxed);
                while (!atomic_compare_exchange_weak_explicit(&total, &old, old+1u,
                       memory_order_acq_rel, memory_order_relaxed)) {}
                sum += old;
            } else if (COHERENT_KIND == 3) {
                while (atomic_exchange_explicit(&lock, 1, memory_order_acquire)) {}
                // The lock protects ordinary RAM, not an atomic-only demo.
                ++locked_sum; aster_coherent_output[0] += i+1u;
                atomic_store_explicit(&lock, 0, memory_order_release);
                sum += i+1u;
            } else {
                _Atomic uint32_t *p = COHERENT_KIND == 4 ? &adjacent[hart] : &separate[hart].value;
                sum += atomic_fetch_add_explicit(p, 1, memory_order_relaxed);
            }
            ++count;
        }
    } else if (COHERENT_KIND == 6) {
        for (uint32_t i = 0; i < COHERENT_ITEMS; ++i) {
            const uint32_t value = payload(seed, i);
            if (!hart) {
                request[0] = value; request[1] = ~value;
                atomic_store_explicit(&turn, 1, memory_order_release);
            }
            if (hart || COHERENT_WORKERS == 1) {
                while (atomic_load_explicit(&turn, memory_order_acquire) != 1) {}
                error |= request[0] != value || request[1] != ~value;
                reply[0] = request[0] ^ 0xa57e6u; reply[1] = ~reply[0];
                atomic_store_explicit(&turn, 0, memory_order_release);
            }
            if (!hart) {
                while (atomic_load_explicit(&turn, memory_order_acquire) != 0) {}
                error |= reply[0] != (value ^ 0xa57e6u) || reply[1] != ~(value ^ 0xa57e6u);
            }
            sum += value; ++count;
        }
    } else if (COHERENT_KIND == 7) {
        for (uint32_t i = 0; i < COHERENT_ITEMS; ++i) {
            const uint32_t value = payload(seed, i);
            if (!hart) {
                while (i - atomic_load_explicit(&tail, memory_order_acquire) == 8) {}
                queue[i % 8][0] = value; queue[i % 8][1] = ~value;
                atomic_store_explicit(&head, i+1u, memory_order_release);
            }
            if (hart || COHERENT_WORKERS == 1) {
                while (atomic_load_explicit(&head, memory_order_acquire) <= i) {}
                error |= queue[i % 8][0] != value || queue[i % 8][1] != ~value;
                atomic_store_explicit(&tail, i+1u, memory_order_release);
            }
            sum += value; ++count;
        }
    } else {
        for (uint32_t r = 0; r < COHERENT_ROUNDS; ++r)
            for (uint32_t i = begin; i < end; ++i)
                aster_coherent_output[i] = mix(aster_coherent_output[i], r);
        for (uint32_t i = begin; i < end; ++i) { sum += aster_coherent_output[i]; ++count; }
    }
    units[hart] = count; sums[hart] = sum; errors[hart] = error;
}
#endif

#if COHERENT_KIND == 8 && COHERENT_WORKERS == 2
// shared_mix's hart 1 resets its own half (matrix.md §10.4: a hart's stores invalidate the other's warm lines)
static void hart1_reset(void *arg) {
    (void)arg;
    for (unsigned i = (COHERENT_ITEMS + 1u) / 2u; i < COHERENT_ITEMS; ++i)
        aster_coherent_output[i] = seed_for_job ^ (i * 0x1021u);
}
#endif

// ---- the harness ----
enum { UNTIMED, E2E, KERNEL };

// SECONDARY_RUN (soc.md §7.3): 1 releases hart 1 from reset, 0 holds it there (aster_multicore.h's, whose names
// collide with aster_counters.h's)
static inline void secondary_run(uint32_t run) {
    __asm__ volatile ("fence rw, rw" ::: "memory");
    *(volatile uint32_t *)0x20002004u = run;
    __asm__ volatile ("fence rw, rw" ::: "memory");
}
static volatile uint32_t kernel_phase;                 // hart 1 boots into the runtime's worker (the kernel window)

// v1's per-job state reset (its main)
static __attribute__((noinline)) void reset_state(uint32_t job) {
    seed_for_job = COHERENT_SEED ^ (job * 0x9e3779b9u);
    atomic_store_explicit(&done, 0, memory_order_relaxed);
    atomic_store_explicit(&total, 0, memory_order_relaxed);
    atomic_store_explicit(&lock, 0, memory_order_relaxed);
    atomic_store_explicit(&turn, 0, memory_order_relaxed);
    atomic_store_explicit(&head, 0, memory_order_relaxed);
    atomic_store_explicit(&tail, 0, memory_order_relaxed);
#if COHERENT_KIND == 9
    atomic_store_explicit(&full, 0, memory_order_relaxed);
    atomic_store_explicit(&empty, 0, memory_order_relaxed);
    request[0] = reply[0] = queue[0][0] = 0;           // (v1's kernels' state, unused here)
#endif
    locked_sum = 0;
    for (unsigned h = 0; h < 2; ++h) {
        units[h] = sums[h] = errors[h] = 0;
        atomic_store_explicit(&adjacent[h], 0, memory_order_relaxed);
        atomic_store_explicit(&separate[h].value, 0, memory_order_relaxed);
    }
#if COHERENT_KIND == 8 && COHERENT_WORKERS == 2
    if (kernel_phase) {                                // (hart 1 under the runtime: its half by hart 1)
        aster_smp_dispatch(hart1_reset, 0);
        for (unsigned i = 0; i < (COHERENT_ITEMS + 1u) / 2u; ++i) aster_coherent_output[i] = seed_for_job ^ (i*0x1021u);
        aster_smp_join();
    } else
#endif
    for (unsigned i = 0; i < COHERENT_ITEMS; ++i)
        aster_coherent_output[i] = COHERENT_KIND == 8 ? seed_for_job ^ (i*0x1021u) : 0;
    atomic_store_explicit(&epoch, job, memory_order_release);
    __asm__ volatile ("fence rw, rw" ::: "memory");
}

void aster_secondary_main(void) {
    if (kernel_phase) aster_smp_worker();              // (the kernel window: the runtime's worker)
    // v1's: cold-started once per measured job
    const uint32_t job = atomic_load_explicit(&epoch, memory_order_acquire);
    matrix_stamp_start(1);
    aster_coherent_kernel(1, seed_for_job);
    matrix_stamp_end(1);
    atomic_store_explicit(&done, job, memory_order_release);
    for (;;) __asm__ volatile ("" ::: "memory");
}

#if COHERENT_WORKERS == 2
static void hart1_kernel(void *arg) {
    (void)arg;
    matrix_stamp_start(1);
    aster_coherent_kernel(1, seed_for_job);
    matrix_stamp_end(1);
}
#endif

static __attribute__((noinline)) void pass(int mode) {
    reset_state(1);
    matrix_last = 1;                                   // (one stretch a window)
    if (mode == KERNEL) {
#if COHERENT_WORKERS == 2
        matrix_arm(hart1_kernel, 0);
        matrix_open(1);
        matrix_release();
        aster_coherent_kernel(0, seed_for_job);
        matrix_stamp_end(0);
        matrix_await();
        matrix_close();
        aster_smp_join();
#else
        matrix_open(1);
        aster_coherent_kernel(0, seed_for_job);
        matrix_close();
#endif
        return;
    }
    // v1's window: START, hart 1 released, hart 0's kernel, its wait for done, FREEZE; hart 1 held again
    if (mode == E2E) *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
#if COHERENT_WORKERS == 2
    secondary_run(1);
#endif
    aster_coherent_kernel(0, seed_for_job);
#if COHERENT_WORKERS == 2
    matrix_stamp_end(0);
    while (atomic_load_explicit(&done, memory_order_acquire) != 1u) {}
#endif
    __asm__ volatile ("fence rw, rw" ::: "memory");
    if (mode == E2E) *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    __asm__ volatile ("fence rw, rw" ::: "memory");
#if COHERENT_WORKERS == 2
    secondary_run(0);
    while (*(volatile uint32_t *)0x2000200cu & 2u) {}  // (hart 1 stopped, v1's)
#endif
}

// v1's checks, and the checksum: the harts' summed sums
static uint32_t check(uint32_t *error_out) {
    const uint32_t n = COHERENT_ITEMS;
    const uint32_t split = COHERENT_WORKERS == 2 ? (n + 1u) / 2u : n, second = n - split;
    uint32_t result0, result1, expected0, expected1, expected_sum = 0, u0 = split, u1 = second;
    if (COHERENT_KIND <= 2) {
        result0 = atomic_load(&total); result1 = sums[0] + sums[1];
        expected0 = n; expected1 = n * (n - 1u) / 2u; expected_sum = expected1;
    } else if (COHERENT_KIND == 3) {
        result0 = locked_sum; result1 = aster_coherent_output[0];
        expected0 = n; expected1 = n * (n + 1u) / 2u; expected_sum = expected1;
    } else if (COHERENT_KIND <= 5) {
        result0 = atomic_load(COHERENT_KIND == 4 ? &adjacent[0] : &separate[0].value);
        result1 = atomic_load(COHERENT_KIND == 4 ? &adjacent[1] : &separate[1].value);
        expected0 = split; expected1 = second;
        expected_sum = split * (split - 1u) / 2u + (second ? second * (second - 1u) / 2u : 0u);
    } else if (COHERENT_KIND <= 7) {
        result0 = COHERENT_KIND == 6 ? reply[0] : atomic_load(&head);
        result1 = COHERENT_KIND == 6 ? reply[1] : atomic_load(&tail);
        expected0 = COHERENT_KIND == 6 ? payload(seed_for_job, n - 1u) ^ 0xa57e6u : n;
        expected1 = COHERENT_KIND == 6 ? ~expected0 : n;
        for (unsigned i = 0; i < n; ++i) expected_sum += payload(seed_for_job, i);
        expected_sum *= COHERENT_WORKERS;
        u0 = n; u1 = COHERENT_WORKERS == 2 ? n : 0;
    } else if (COHERENT_KIND == 8) {
        result0 = aster_coherent_output[0]; result1 = aster_coherent_output[n - 1u];
        expected0 = expected1 = 0;
        for (unsigned i = 0; i < n; ++i) {
            uint32_t x = seed_for_job ^ (i * 0x1021u);
            for (unsigned r = 0; r < COHERENT_ROUNDS; ++r) x = mix(x, r);
            errors[0] |= aster_coherent_output[i] != x; expected_sum += x;
            if (!i) expected0 = x;
            if (i == n - 1u) expected1 = x;
        }
    } else {
#if COHERENT_KIND == 9
        result0 = atomic_load(&full); result1 = atomic_load(&empty);
        expected0 = expected1 = COHERENT_ROUNDS;
        for (unsigned r = 0; r < COHERENT_ROUNDS; ++r)
            for (unsigned i = 0; i < n; ++i) expected_sum += payload(round_seed(seed_for_job, r), i);
        expected_sum *= 2u;
        u0 = u1 = n * COHERENT_ROUNDS;
#else
        result0 = result1 = expected0 = expected1 = 0;
#endif
    }
    *error_out = errors[0] | errors[1] | (result0 != expected0) | (result1 != expected1) |
                 (sums[0] + sums[1] != expected_sum) | (units[0] != u0) | (units[1] != u1);
    return sums[0] + sums[1];
}

static int timed_pass(struct v12_record *record, int mode) {
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
#if COHERENT_WORKERS == 1
    matrix_hart0_whole(record);
#endif
    uint32_t error;
    const uint32_t checksum = check(&error);
    static const char *const names[] = {"atomic_add", "lrsc_counter", "cas_counter", "lock_sum", "false_shared",
                                        "padded", "ping_pong", "spsc_queue", "shared_mix", "producer_consumer"};
    record->name = names[COHERENT_KIND]; record->family = "coherence";
    record->method = COHERENT_WORKERS == 2 ? "multicore" : "scalar";
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = COHERENT_ITEMS; record->iterations = 1; record->param = COHERENT_ROUNDS;
    record->seed = seed_for_job; record->checksum = checksum; record->workers = COHERENT_WORKERS;
    record->pass = !error;
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    if (!matrix_cold) pass(E2E);                       // the e2e window's warm-up (the same code)
    int failed = timed_pass(&record, E2E);
    if (!matrix_cold) {
#if COHERENT_WORKERS == 2
        kernel_phase = 1;
        __asm__ volatile ("fence rw, rw" ::: "memory");
        aster_smp_start();                             // (hart 1 boots into the runtime's worker)
#endif
        pass(KERNEL);                                  // the kernel window's warm-up
        failed |= timed_pass(&record, KERNEL);
    }
    return failed;
}
