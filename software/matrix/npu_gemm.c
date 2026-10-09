// 20.4's matrix: the NPU GEMM sweep (docs/matrix.md §4.6), C (M x N, int32) = A (M x K) B (K x N), signed bytes from
// xorshift32 seeded GEMM_SEED (A, then B: software/matrix/gemm_mc.c's inputs, which scripts/matrix.py's
// gemm_checksum models over every element of C). GEMM_METHOD:
//   0 scalar   v1's xe_scalar_gemm (the shapes under 32^3)
//   1 dot8     v1's xe_dot8_gemm on one worker
//   2 two workers, xe_dot8_gemm on half the rows each
//   3 the NPU  one job (aster_npu2.h)
// Two windows (matrix.md §4.6): the kernel window from START to the job's end (the CPU's compute, for DOT8 and
// scalar; the NPU's descriptor written before it); the e2e window adds the descriptor (and the runtime's dispatch
// and join for two workers); v1's kernels pack nothing apart, and the NPU writes C in place. A cold run
// (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is, for each window, the same code
// untimed first. C and its guard are poisoned before each pass by their writers; the guard is checked after it.
#include <stdint.h>

#include "aster.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "xe_kernels.h"

#ifndef GEMM_M
#define GEMM_M 16u
#endif
#ifndef GEMM_N
#define GEMM_N 16u
#endif
#ifndef GEMM_K
#define GEMM_K 64u
#endif
#ifndef GEMM_METHOD
#define GEMM_METHOD 3
#endif
#ifndef GEMM_SEED
#define GEMM_SEED 0x2545F491u
#endif
#define WORKERS (GEMM_METHOD == 2 ? 2u : 1u)
#define NPU (GEMM_METHOD == 3)
#define GUARD 64u
#define SPLIT ((GEMM_M + 1u) / 2u)

enum { E2E = 1, KERNEL };

static int8_t a[GEMM_M * GEMM_K] __attribute__((aligned(64)));
static int8_t b[GEMM_K * GEMM_N] __attribute__((aligned(64)));
static uint8_t c_buf[GUARD + 4u * GEMM_M * GEMM_N + GUARD] __attribute__((aligned(64)));   // (C between guards)
#define C ((int32_t *)(void *)(c_buf + GUARD))
static volatile uint32_t engine_failed;

static uint32_t random_state = GEMM_SEED;
static uint32_t next_random(void) {
    random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
    return random_state;
}

static struct aster_npu_gemm rows(uint32_t i0, uint32_t i1) {
    const struct aster_npu_gemm job = {a + i0 * GEMM_K, b, C + i0 * GEMM_N, GEMM_K, GEMM_N, 4u * GEMM_N, i1 - i0,
                                       GEMM_N, GEMM_K};
    return job;
}

static __attribute__((noinline, unused)) void cpu_rows(uint32_t i0, uint32_t i1) {   // (the CPU methods)
    const struct aster_npu_gemm job = rows(i0, i1);
#if GEMM_METHOD == 0
    xe_scalar_gemm(&job);
#else
    xe_dot8_gemm(&job);
#endif
}

#if WORKERS == 2
static void hart1_rows(void *arg) {
    (void)arg;
    matrix_stamp_start(1);
    cpu_rows(SPLIT, GEMM_M);
    matrix_stamp_end(1);
}

static void hart1_poison(void *arg) {                  // (its rows of C: matrix_window.h)
    (void)arg;
    matrix_poison(c_buf + GUARD + 4u * SPLIT * GEMM_N, 4u * (GEMM_M - SPLIT) * GEMM_N);
}
#endif

static void poison(void) {
#if WORKERS == 2
    aster_smp_dispatch(hart1_poison, 0);
    matrix_poison(c_buf, GUARD + 4u * SPLIT * GEMM_N);
    matrix_poison(c_buf + GUARD + 4u * GEMM_M * GEMM_N, GUARD);
    aster_smp_join();
#else
    matrix_poison(c_buf, sizeof c_buf);
#endif
}

static __attribute__((noinline)) void pass(int mode) {
    matrix_last = 1;                                   // (one stretch)
#if NPU
    const struct aster_npu2_job job = {a, b, C, GEMM_K, GEMM_N, 4u * GEMM_N, GEMM_M, GEMM_N, GEMM_K, 0, 0, 0, 0, 0};
    if (mode == KERNEL) aster_npu2_describe(&job);
    matrix_open(1);
    if (mode == KERNEL) aster_npu2_go(); else aster_npu2_start(&job);
    const uint32_t status = aster_npu2_wait();
    matrix_close();
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
#elif WORKERS == 2
    if (mode == KERNEL) {
        matrix_arm(hart1_rows, 0);
        matrix_open(1);
        matrix_release();
        cpu_rows(0, SPLIT);
        matrix_stamp_end(0);                           // (hart 0's share ends; it waits)
        matrix_await();
        matrix_close();
        aster_smp_join();
    } else {
        matrix_open(1);
        aster_smp_dispatch(hart1_rows, 0);
        cpu_rows(0, SPLIT);
        matrix_stamp_end(0);
        aster_smp_join();
        matrix_close();
    }
#else
    (void)mode;
    matrix_open(1);
    cpu_rows(0, GEMM_M);
    matrix_close();
#endif
}

static int timed_pass(struct v12_record *record, int mode) {
    poison();
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
#if NPU
    matrix_hart0_none(record);                         // (hart 0 only starts the job and polls it)
#elif WORKERS == 1
    matrix_hart0_whole(record);
#endif
    uint32_t sum = 0, guard_errors = 0;
    for (uint32_t i = 0; i < GEMM_M * GEMM_N; ++i) sum = (sum * 33u) ^ (uint32_t)C[i];
    for (uint32_t i = 0; i < GUARD; ++i)               // (both guards of C; A and B unchanged)
        guard_errors += (c_buf[i] != 0xA5u) + (c_buf[GUARD + 4u * GEMM_M * GEMM_N + i] != 0xA5u);
    random_state = GEMM_SEED;
    for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i) guard_errors += a[i] != (int8_t)next_random();
    for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i) guard_errors += b[i] != (int8_t)next_random();
    static char name[32] = "gemm_";
    if (!name[5]) {                                    // gemm_<M>x<N>x<K>
        char *p = name + 5;
        const uint32_t dims[3] = {GEMM_M, GEMM_N, GEMM_K};
        for (int d = 0; d < 3; ++d) {
            char digits[8]; int n = 0; uint32_t v = dims[d];
            do { digits[n++] = (char)('0' + v % 10u); v /= 10u; } while (v);
            while (n) *p++ = digits[--n];
            if (d < 2) *p++ = 'x';
        }
        *p = 0;
    }
    static const char *const methods[] = {"scalar", "dot8", "multicore", "npu"};
    record->name = name; record->family = "npu_gemm"; record->method = methods[GEMM_METHOD];
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = GEMM_M * GEMM_N; record->iterations = 1; record->param = GEMM_K; record->seed = GEMM_SEED;
    record->checksum = sum; record->workers = WORKERS; record->pass = !engine_failed && guard_errors == 0;
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i) a[i] = (int8_t)next_random();
    for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i) b[i] = (int8_t)next_random();
#if WORKERS == 2
    aster_smp_start();
#endif
    if (!matrix_cold) pass(E2E);                       // the e2e window's warm-up (the same code)
    int failed = timed_pass(&record, E2E);
    if (!matrix_cold) {
        pass(KERNEL);                                  // the kernel window's warm-up
        failed |= timed_pass(&record, KERNEL);
    }
    return failed;
}
