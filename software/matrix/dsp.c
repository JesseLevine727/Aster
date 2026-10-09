// 20.4's matrix: the DSP cases of docs/matrix.md §4.5 beside Conv2D (software/matrix/conv2d.c). DSP_CASE:
//   1 dot  a 1 x 1 x DSP_K dot product of v1's signed bytes (software/benchmarks/cross_engine.c: a_value, b_value)
//   2 fir  256 outputs of a DSP_K-tap FIR over 256 + DSP_K - 1 samples: C(i) = sum_k x(i + k) coef(k)
//   3 fft  v1's 256-point Q15 radix-2 FFT (software/benchmarks/workload_fft.c), 4 iterations, v1's window
// DSP_METHOD 0 scalar (v1's xe_scalar_gemm), 1 two workers, 2 DOT8 (v1's xe_dot8_gemm), 3 the NPU: the dot as a
// 1 x 1 x K job (the K-split mapping), the FIR as a 256 x 1 x K job whose A rows overlap (A_STRIDE 1: no Toeplitz
// copy). Two workers split the dot's K (hart 1's partial added by hart 0), the FIR's outputs, and each FFT stage's
// butterflies (a barrier between stages).
// Two windows (matrix.md §4.5): the kernel window, the data in place and the NPU's descriptor written, from START to
// the computation's end; the e2e window adds the NPU's set-up, the runtime's dispatch and join, and for the FFT v1's
// fill and checksum in its window. For a one-hart CPU method the two hold the same code. A cold run (matrix_cold.h)
// is the e2e window as the first pass after reset; a warm run is, for each window, the same code untimed first; the
// outputs are poisoned before each pass by their writers. The dot and FIR outputs are checked after the window
// against v1's scalar reference and their guards (v1's check); each record's checksum folds the outputs, which
// scripts/matrix.py's oracle recomputes (workload_reference.fft_checksum for the FFT).
#include <stdint.h>

// (the dot at K = 0 makes some set-up and check loops empty: i < 0u)
#pragma GCC diagnostic ignored "-Wtype-limits"

#include "aster.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "xe_kernels.h"
#if !defined(DSP_CASE) || DSP_CASE == 3
#include "workload_fft_twiddle.h"
#endif

#ifndef DSP_CASE
#define DSP_CASE 1
#endif
#ifndef DSP_METHOD
#define DSP_METHOD 0
#endif
#ifndef DSP_K
#define DSP_K 64u
#endif
#ifndef DSP_SEED
#define DSP_SEED 0x13570000u
#endif
#define WORKERS (DSP_METHOD == 1 ? 2u : 1u)
#define NPU (DSP_METHOD == 3)
#define POISON 0xA5u

enum { E2E = 1, KERNEL };

static volatile uint32_t engine_failed, check_errors;
static uint32_t checksum;

#if DSP_CASE != 3
// ---- the dot product and the FIR: v1's operands, v1's engines ----
#if DSP_CASE == 1
#define M 1u
#define A_BYTES DSP_K
#define A_STRIDE DSP_K
#else
#define M 256u
#define A_BYTES (256u + DSP_K - 1u)
#define A_STRIDE 1u                                    // (the FIR's rows overlap: row i is x + i)
#endif
#define GUARD 64u
static uint8_t a_buf[GUARD + A_BYTES + GUARD] __attribute__((aligned(64)));
static uint8_t b_buf[GUARD + DSP_K + GUARD] __attribute__((aligned(64)));
static uint8_t c_buf[GUARD + 4u * M + GUARD] __attribute__((aligned(64)));
static int32_t reference[M];
static volatile int32_t partial;                       // (the two-worker dot: hart 1's half)

static uint8_t a_value(uint32_t i, uint32_t seed) {    // v1's (cross_engine.c)
    if ((i + seed) % 29u == 0u) return 0x80u;
    if ((i + seed) % 31u == 0u) return 0x7fu;
    return (uint8_t)((i * 73u + seed * 19u + (i >> 2)) & 0xffu);
}
static uint8_t b_value(uint32_t i, uint32_t seed) {
    if ((i + seed) % 23u == 0u) return 0x80u;
    if ((i + seed) % 41u == 0u) return 0x7fu;
    return (uint8_t)((i * 29u + seed * 47u + (i >> 1)) & 0xffu);
}

static struct aster_npu_gemm logical(void) {
    const struct aster_npu_gemm job = {(const int8_t *)(void *)(a_buf + GUARD), (const int8_t *)(void *)(b_buf + GUARD),
                                       (int32_t *)(void *)(c_buf + GUARD), A_STRIDE, 1u, 4u, M, 1u, DSP_K};
    return job;
}

static void setup(void) {
    for (uint32_t i = 0; i < sizeof a_buf; ++i) a_buf[i] = POISON;
    for (uint32_t i = 0; i < sizeof b_buf; ++i) b_buf[i] = POISON;
    for (uint32_t i = 0; i < A_BYTES; ++i) a_buf[GUARD + i] = a_value(i, DSP_SEED);
    for (uint32_t i = 0; i < DSP_K; ++i) b_buf[GUARD + i] = b_value(i, DSP_SEED);
    struct aster_npu_gemm ref = logical();
    ref.c = reference;
    xe_scalar_gemm(&ref);                               // (v1's reference, before any window)
}

#if WORKERS == 2
static void hart1_share(void *arg) {
    (void)arg;
    matrix_stamp_start(1);
    struct aster_npu_gemm job = logical();
#if DSP_CASE == 1
    const uint32_t split = DSP_K / 2u;                 // (v1's: the dot's K halved)
    job.a += split; job.b += split; job.k -= split; job.c = (int32_t *)&partial;
#else
    const uint32_t split = (M + 1u) / 2u;
    job.a += split * A_STRIDE; job.c += split; job.m -= split;
#endif
    xe_scalar_gemm(&job);
    matrix_stamp_end(1);
}

static void hart1_poison(void *arg) {                  // (its own outputs: matrix_window.h)
    (void)arg;
#if DSP_CASE == 1
    partial = (int32_t)0xA5A5A5A5u;
#else
    matrix_poison(c_buf + GUARD + 4u * ((M + 1u) / 2u), 4u * (M - (M + 1u) / 2u));
#endif
}
#endif

static void poison_outputs(void) {
#if WORKERS == 2
    aster_smp_dispatch(hart1_poison, 0);
#if DSP_CASE == 1
    matrix_poison(c_buf, sizeof c_buf);
#else
    matrix_poison(c_buf, GUARD + 4u * ((M + 1u) / 2u));
    matrix_poison(c_buf + GUARD + 4u * M, GUARD);
#endif
    aster_smp_join();
#else
    matrix_poison(c_buf, sizeof c_buf);
#endif
}

#if NPU
static struct aster_npu2_job npu_job(void) {
    const struct aster_npu_gemm g = logical();
    const struct aster_npu2_job job = {g.a, g.b, g.c, g.a_stride, g.b_stride, g.c_stride, g.m, g.n, g.k, 0, 0, 0, 0, 0};
    return job;
}

static void npu_outcome(uint32_t status) {
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
}
#endif

static __attribute__((noinline)) void pass(int mode) {
    matrix_last = 1;                                   // (one stretch)
#if NPU
    const struct aster_npu2_job job = npu_job();
    if (mode == KERNEL) aster_npu2_describe(&job);
    matrix_open(1);
    if (mode == KERNEL) aster_npu2_go(); else aster_npu2_start(&job);
    const uint32_t status = aster_npu2_wait();
    matrix_close();
    npu_outcome(status);
#elif WORKERS == 2
    struct aster_npu_gemm job = logical();
#if DSP_CASE == 1
    job.k = DSP_K / 2u;
#else
    job.m = (M + 1u) / 2u;
#endif
    if (mode == KERNEL) {
        matrix_arm(hart1_share, 0);
        matrix_open(1);
        matrix_release();
        xe_scalar_gemm(&job);
        if (DSP_CASE == 2) matrix_stamp_end(0);        // (the FIR's hart 0 only waits after its share)
        matrix_await();
    } else {
        matrix_open(1);
        aster_smp_dispatch(hart1_share, 0);
        xe_scalar_gemm(&job);
        if (DSP_CASE == 2) matrix_stamp_end(0);
        aster_smp_join();
    }
#if DSP_CASE == 1
    int32_t *c = (int32_t *)(void *)(c_buf + GUARD);   // (hart 0 adds hart 1's half: v1's)
    *c += partial;
#endif
    matrix_close();
    if (mode == KERNEL) aster_smp_join();
#else
    (void)mode;
    const struct aster_npu_gemm job = logical();
    matrix_open(1);
#if DSP_METHOD == 2
    xe_dot8_gemm(&job);
#else
    xe_scalar_gemm(&job);
#endif
    matrix_close();
#endif
}

static void after_pass(void) {                          // v1's check: the outputs, the guards, the operands
    checksum = 0;
    for (uint32_t i = 0; i < M; ++i) {
        int32_t v;
        for (uint32_t byte = 0; byte < 4u; ++byte) ((uint8_t *)&v)[byte] = c_buf[GUARD + 4u * i + byte];
        if (v != reference[i]) ++check_errors;
        checksum = (checksum * 33u) ^ (uint32_t)v;
    }
    for (uint32_t i = 0; i < GUARD; ++i) {
        if (c_buf[i] != POISON || c_buf[GUARD + 4u * M + i] != POISON) ++check_errors;
        if (a_buf[i] != POISON || a_buf[GUARD + A_BYTES + i] != POISON) ++check_errors;
        if (b_buf[i] != POISON || b_buf[GUARD + DSP_K + i] != POISON) ++check_errors;
    }
    for (uint32_t i = 0; i < A_BYTES; ++i) if (a_buf[GUARD + i] != a_value(i, DSP_SEED)) ++check_errors;
    for (uint32_t i = 0; i < DSP_K; ++i) if (b_buf[GUARD + i] != b_value(i, DSP_SEED)) ++check_errors;
}
#else
// ---- the FFT: v1's, and split over two workers stage by stage ----
#define FFT_ITERATIONS 4u
static int32_t re[FFT_N];
static int32_t im[FFT_N];

static void fill(void) {
    for (uint32_t i = 0; i < FFT_N; ++i) {
        re[i] = (int32_t)((DSP_SEED ^ (i * 0x1021u)) & 0xffffu) - 32768;
        im[i] = 0;
    }
}

static __attribute__((noinline)) void bit_reverse(void) {
    for (uint32_t i = 1, j = 0; i < FFT_N; ++i) {
        uint32_t bit = FFT_N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            int32_t tr = re[i]; re[i] = re[j]; re[j] = tr;
            int32_t ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
    }
}

// a stage's butterflies, v1's loops (workload_fft.c): the blocks [s0, s1) of `length` samples, and in each the k
// range [k0, k1) of its half (one worker: every block and every k)
static __attribute__((noinline)) void butterflies(uint32_t length, uint32_t s0, uint32_t s1, uint32_t k0, uint32_t k1) {
    const uint32_t half = length >> 1;
    const uint32_t step = FFT_N / length;
    for (uint32_t start = s0; start < s1; start += length)
        for (uint32_t k = k0; k < k1; ++k) {
        const int32_t wr = fft_twiddle_re[k * step];
        const int32_t wi = fft_twiddle_im[k * step];
        const int32_t xr = re[start + k], xi = im[start + k];
        const int32_t yr = re[start + k + half], yi = im[start + k + half];
        const int64_t tre = (int64_t)wr * yr - (int64_t)wi * yi;
        const int64_t tim = (int64_t)wr * yi + (int64_t)wi * yr;
        const int32_t tr = (int32_t)(tre >> 15);
        const int32_t ti = (int32_t)(tim >> 15);
        re[start + k] = (xr + tr) >> 1;
        im[start + k] = (xi + ti) >> 1;
        re[start + k + half] = (xr - tr) >> 1;
        im[start + k + half] = (xi - ti) >> 1;
    }
}

#if WORKERS == 2
static volatile uint32_t stage_done[2];                // each hart's last stage finished (a barrier a stage)

static void stage_barrier(uint32_t hart, uint32_t stage) {
    __asm__ volatile ("fence rw, rw" ::: "memory");
    stage_done[hart] = stage;
    while (stage_done[hart ^ 1u] < stage) {}
    __asm__ volatile ("fence rw, rw" ::: "memory");
}

static volatile uint32_t stage_base;                   // (this transform's stage numbers start after it)

static void fft_half(uint32_t hart) {
    uint32_t stage = stage_base;
    if (hart == 0) bit_reverse();
    stage_barrier(hart, ++stage);
    for (uint32_t length = 2; length <= FFT_N; length <<= 1) {
        if (length < FFT_N) {                          // half the blocks each
            const uint32_t mid = FFT_N / 2u;
            butterflies(length, hart ? mid : 0, hart ? FFT_N : mid, 0, length >> 1);
        } else {                                       // one block: half its k range each
            const uint32_t q = FFT_N / 4u;
            butterflies(length, 0, FFT_N, hart ? q : 0, hart ? 2u * q : q);
        }
        stage_barrier(hart, ++stage);
    }
}

static void hart1_fft(void *arg) {
    (void)arg;
    matrix_stamp_start(1);
    fft_half(1);
    matrix_stamp_end(1);
}
#endif

// one transform; in the kernel window its own interval (open: START for the first), hart 1 armed before it
static __attribute__((noinline)) void fft(uint32_t number, int last, int mode, int first) {
#if WORKERS == 2
    stage_base = number * 9u;                          // (9 barriers a transform: their numbers never repeat)
    matrix_last = (uint32_t)last;
    __asm__ volatile ("fence rw, rw" ::: "memory");
    if (mode == KERNEL) {
        matrix_arm(hart1_fft, 0);
        matrix_open(first);
        matrix_release();
        fft_half(0);
        matrix_stamp_end(0);                           // (hart 0's last share ends; it waits)
        matrix_await();
        matrix_close();
        aster_smp_join();
    } else {
        aster_smp_dispatch(hart1_fft, 0);
        fft_half(0);
        aster_smp_join();
    }
#else
    (void)number; (void)last;
    if (mode == KERNEL) matrix_open(first);
    bit_reverse();
    for (uint32_t length = 2; length <= FFT_N; length <<= 1) butterflies(length, 0, FFT_N, 0, length >> 1);
    if (mode == KERNEL) matrix_close();
#endif
}

static uint32_t fold(uint32_t c) {
    for (uint32_t i = 0; i < FFT_N; ++i) c = (c * 33u) ^ (uint32_t)re[i];
    for (uint32_t i = 0; i < FFT_N; ++i) c = (c * 33u) ^ (uint32_t)im[i];
    return c;
}

static uint32_t passes;                                // (the barriers' numbers run on across passes)

static void setup(void) {}

static __attribute__((noinline)) void pass(int mode) {
    uint32_t c = 0;
    if (mode == E2E) {                                 // v1's window: fill, transform, checksum, four times
        matrix_open(1);
        for (uint32_t it = 0; it < FFT_ITERATIONS; ++it) {
            fill();
            fft(passes * FFT_ITERATIONS + it, it == FFT_ITERATIONS - 1u, mode, 0);
            c = fold(c);
        }
        matrix_close();
    } else {                                           // the transforms alone, the data in place
        for (uint32_t it = 0; it < FFT_ITERATIONS; ++it) {
            fill();
            fft(passes * FFT_ITERATIONS + it, it == FFT_ITERATIONS - 1u, mode, it == 0);
            c = fold(c);
        }
    }
    ++passes;
    checksum = c;
}

static void poison_outputs(void) { matrix_poison(re, sizeof re); matrix_poison(im, sizeof im); }
static void after_pass(void) {}
#endif

static int timed_pass(struct v12_record *record, int mode) {
    poison_outputs();
    check_errors = 0;
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
#if NPU
    matrix_hart0_none(record);                         // (hart 0 only starts the job and polls it)
#elif WORKERS == 1
    matrix_hart0_whole(record);
#elif DSP_CASE == 3
    if (mode == E2E) matrix_hart0_whole(record);       // (v1's window ends in hart 0's checksum)
#elif DSP_CASE == 1
    matrix_hart0_whole(record);                        // (hart 0 adds the halves, after its wait)
#endif
    after_pass();
    static const char *const names[] = {"", "dot", "fir", "fft"};
    static const char *const methods[] = {"scalar", "multicore", "dot8", "npu"};
    record->name = names[DSP_CASE]; record->family = "dsp"; record->method = methods[DSP_METHOD];
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
#if DSP_CASE == 3
    record->size = FFT_N * 4u; record->iterations = FFT_ITERATIONS; record->param = FFT_N;
#else
    record->size = DSP_CASE == 1 ? DSP_K : 256u; record->iterations = 1; record->param = DSP_CASE == 1 ? 0u : DSP_K;
#endif
    record->seed = DSP_SEED; record->checksum = checksum; record->workers = WORKERS;
    record->pass = !engine_failed && check_errors == 0;
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    setup();
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
