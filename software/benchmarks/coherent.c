#include "aster.h"
#include <stdatomic.h>

#ifndef COHERENT_KIND
#define COHERENT_KIND 0
#endif
#ifndef COHERENT_ITEMS
#define COHERENT_ITEMS 64
#endif
#ifndef COHERENT_ROUNDS
#define COHERENT_ROUNDS 4
#endif
#ifndef COHERENT_JOBS
#define COHERENT_JOBS 3
#endif
#ifndef COHERENT_WORKERS
#define COHERENT_WORKERS 2
#endif
#ifndef COHERENT_SEED
#define COHERENT_SEED 0x13570000u
#endif
_Static_assert(COHERENT_KIND >= 0 && COHERENT_KIND < 9, "unknown coherent workload");
_Static_assert(COHERENT_ITEMS >= 2 && COHERENT_ITEMS <= 1024, "items must be 2..1024");
_Static_assert(COHERENT_ROUNDS >= 1 && COHERENT_ROUNDS <= 64, "rounds must be 1..64");
_Static_assert(COHERENT_JOBS >= 1 && COHERENT_JOBS <= 16, "jobs must be 1..16");
_Static_assert(COHERENT_WORKERS == 1 || COHERENT_WORKERS == 2, "workers must be 1 or 2");
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "requires lock-free 32-bit C atomics");

#define REG(addr) (*(volatile uint32_t *)(addr))
#define RELEASE 0x20002004u
#define HARTS 0x20002008u
#define RUNNING 0x2000200cu
#define PERF 0x20003000u
#define CONTROL (PERF+0x80u)
#define ALWAYS_INLINE __attribute__((always_inline)) inline
static const char *const workload_names[] = {
    "atomic_add", "lrsc_counter", "cas_counter", "lock_sum", "false_shared",
    "padded", "ping_pong", "spsc_queue", "shared_mix"
};
static const char *const counter_names[] = {
    "cycles", "retired", "memory", "i_access", "i_miss", "d_access", "d_miss",
    "backing", "atomic", "sc_success", "sc_failure", "intervention", "invalidation", "writeback"
};
static _Atomic uint32_t epoch, done, total, lock, turn, head, tail;
static uint32_t seed_for_job, locked_sum, request[2], reply[2], queue[8][2];
static uint32_t units[2], sums[2], errors[2];
// Adjacent words share a line iff LINE_WORDS > 1. The record reports actual
// addresses; a one-word-line experiment must not be labeled false sharing.
static _Atomic uint32_t adjacent[2] __attribute__((aligned(4096)));
struct padded_word { _Atomic uint32_t value; uint32_t padding[1023]; };
static struct padded_word separate[2] __attribute__((aligned(4096)));
// Public symbols allow independent complete output/snapshot checks, without
// compiler-dependent address guesses or trusting the firmware PASS token.
volatile uint32_t aster_coherent_output[COHERENT_ITEMS];
volatile uint32_t aster_coherent_results[COHERENT_JOBS][8]
    __attribute__((section(".private0")));

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

__attribute__((noipa, section(".text.benchmark")))
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

void aster_secondary_main(void) {
    // Worker is cold-started once per measured job. This removes unmeasured
    // polling during UART from the next job's phase and explicitly measures
    // secondary startup/dispatch overhead. H0 remains running throughout.
    const uint32_t job = atomic_load_explicit(&epoch, memory_order_acquire);
    aster_coherent_kernel(1, seed_for_job);
    atomic_store_explicit(&done, job, memory_order_release);
    for (;;) __asm__ volatile ("" ::: "memory");
}
static void decimal(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static void hex(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static void emit(unsigned job, unsigned pass, struct aster_perf_counter counters[2][14]) {
    volatile uint32_t *result = aster_coherent_results[job-1];
    aster_puts("ASTERBENCH,version=4,name="); aster_puts(workload_names[COHERENT_KIND]);
    aster_puts(",window=dispatch_work_join,status="); aster_puts(pass ? "PASS" : "FAIL");
    decimal("items", COHERENT_ITEMS); decimal("rounds", COHERENT_ROUNDS);
    decimal("jobs", COHERENT_JOBS); decimal("job", job); hex("base_seed", COHERENT_SEED);
    hex("seed", seed_for_job); decimal("harts", REG(HARTS)); decimal("workers", COHERENT_WORKERS);
    decimal("h0_units", result[6]); decimal("h1_units", result[7]);
    hex("result0", result[2]); hex("result1", result[3]); hex("checksum", result[4]);
    decimal("errors", result[5]); decimal("clock_hz", REG(PERF+0x88));
    decimal("l1", REG(PERF+0x8c)&1); decimal("sync_memory", (REG(PERF+0x8c)>>1)&1);
    decimal("line_words", REG(PERF+0x90)); decimal("line_count", REG(PERF+0x94));
    decimal("memory_wait", REG(PERF+0x98));
    const uint32_t p0 = COHERENT_KIND == 4 ? (uint32_t)&adjacent[0] :
                        COHERENT_KIND == 5 ? (uint32_t)&separate[0].value : 0;
    const uint32_t p1 = COHERENT_KIND == 4 ? (uint32_t)&adjacent[1] :
                        COHERENT_KIND == 5 ? (uint32_t)&separate[1].value : 0;
    hex("counter0_addr", p0); hex("counter1_addr", p1);
    for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i) {
        aster_puts(h ? ",h1_" : ",h0_"); aster_puts(counter_names[i]); aster_puts("=0x");
        aster_put_hex64(counters[h][i]);
    }
    aster_putc('\n');
}

int main(void) {
    if (REG(HARTS) < COHERENT_WORKERS || REG(PERF+0x84) != 4 || REG(PERF+0x9c) != 14 ||
        REG(PERF+0x90) > 1024 || !atomic_is_lock_free(&total)) {
        aster_puts("COHERENT CONFIG FAIL\n"); return 1;
    }
    for (unsigned job = 1; job <= COHERENT_JOBS; ++job) {
        seed_for_job = COHERENT_SEED ^ (job * 0x9e3779b9u);
        atomic_store_explicit(&done, 0, memory_order_relaxed);
        atomic_store_explicit(&total, 0, memory_order_relaxed);
        atomic_store_explicit(&lock, 0, memory_order_relaxed);
        atomic_store_explicit(&turn, 0, memory_order_relaxed);
        atomic_store_explicit(&head, 0, memory_order_relaxed);
        atomic_store_explicit(&tail, 0, memory_order_relaxed);
        locked_sum = 0;
        for (unsigned h = 0; h < 2; ++h) {
            units[h] = sums[h] = errors[h] = 0;
            atomic_store_explicit(&adjacent[h], 0, memory_order_relaxed);
            atomic_store_explicit(&separate[h].value, 0, memory_order_relaxed);
        }
        for (unsigned i = 0; i < COHERENT_ITEMS; ++i)
            aster_coherent_output[i] = COHERENT_KIND == 8 ? seed_for_job ^ (i*0x1021u) : 0;
        atomic_store_explicit(&epoch, job, memory_order_release);
        __asm__ volatile ("fence rw, rw" ::: "memory");
        REG(CONTROL) = 1;
        if (COHERENT_WORKERS == 2) REG(RELEASE) = 1;
        aster_coherent_kernel(0, seed_for_job);
        if (COHERENT_WORKERS == 2)
            while (atomic_load_explicit(&done, memory_order_acquire) != job) {}
        __asm__ volatile ("fence rw, rw" ::: "memory"); REG(CONTROL) = 2;
        __asm__ volatile ("fence rw, rw" ::: "memory");
        struct aster_perf_counter counters[2][14];
        for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i) {
            volatile uint32_t *p = (volatile uint32_t *)(PERF+h*256u+i*8u);
            counters[h][i] = aster_perf_read_counter(p, p+1);
        }
        if (COHERENT_WORKERS == 2) {
            REG(RELEASE) = 0; while (REG(RUNNING)&2) {}
        }
        uint32_t result0, result1, expected0, expected1, expected_sum = 0;
        const unsigned split = COHERENT_WORKERS == 2 ? (COHERENT_ITEMS+1u)/2u : COHERENT_ITEMS;
        const unsigned second = COHERENT_ITEMS - split;
        if (COHERENT_KIND <= 2) {
            result0 = atomic_load(&total); result1 = sums[0]+sums[1];
            expected0 = COHERENT_ITEMS; expected1 = COHERENT_ITEMS*(COHERENT_ITEMS-1u)/2u;
            expected_sum = expected1;
        } else if (COHERENT_KIND == 3) {
            result0 = locked_sum; result1 = aster_coherent_output[0];
            expected0 = COHERENT_ITEMS; expected1 = COHERENT_ITEMS*(COHERENT_ITEMS+1u)/2u;
            expected_sum = expected1;
        } else if (COHERENT_KIND <= 5) {
            result0 = atomic_load(COHERENT_KIND == 4 ? &adjacent[0] : &separate[0].value);
            result1 = atomic_load(COHERENT_KIND == 4 ? &adjacent[1] : &separate[1].value);
            expected0 = split; expected1 = second;
            expected_sum = split*(split-1u)/2u + second*(second-1u)/2u;
        } else if (COHERENT_KIND <= 7) {
            result0 = COHERENT_KIND == 6 ? reply[0] : atomic_load(&head);
            result1 = COHERENT_KIND == 6 ? reply[1] : atomic_load(&tail);
            expected0 = COHERENT_KIND == 6 ? payload(seed_for_job, COHERENT_ITEMS-1u)^0xa57e6u : COHERENT_ITEMS;
            expected1 = COHERENT_KIND == 6 ? ~expected0 : COHERENT_ITEMS;
            for (unsigned i = 0; i < COHERENT_ITEMS; ++i) expected_sum += payload(seed_for_job, i);
            expected_sum *= COHERENT_WORKERS;
        } else {
            result0 = aster_coherent_output[0]; result1 = aster_coherent_output[COHERENT_ITEMS-1];
            expected0 = expected1 = 0;
            for (unsigned i = 0; i < COHERENT_ITEMS; ++i) {
                uint32_t x = seed_for_job ^ (i*0x1021u);
                for (unsigned r = 0; r < COHERENT_ROUNDS; ++r) x = mix(x, r);
                errors[0] |= aster_coherent_output[i] != x; expected_sum += x;
                if (!i) expected0 = x;
                if (i == COHERENT_ITEMS-1) expected1 = x;
            }
        }
        const unsigned u0 = COHERENT_KIND == 6 || COHERENT_KIND == 7 ? COHERENT_ITEMS : split;
        const unsigned u1 = COHERENT_KIND == 6 || COHERENT_KIND == 7 ?
                            (COHERENT_WORKERS == 2 ? COHERENT_ITEMS : 0) : second;
        const uint32_t error = errors[0] | errors[1] | (result0 != expected0) | (result1 != expected1) |
            (sums[0]+sums[1] != expected_sum) | (units[0] != u0) | (units[1] != u1);
        volatile uint32_t *record = aster_coherent_results[job-1];
        record[0] = job; record[1] = seed_for_job; record[2] = result0; record[3] = result1;
        record[4] = sums[0]+sums[1]; record[5] = error; record[6] = units[0]; record[7] = units[1];
        emit(job, !error, counters);
    }
    return 0;
}
