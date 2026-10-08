// 20.4's matrix: Conv2D 32x32, K = 5 (docs/matrix.md §4.5; a v1-retained workload). CONV_METHOD:
//   1 im2col, scalar (v1's scalar path)          5 direct, scalar (the 5x5 loop on the image)
//   2 im2col, two workers (rows split)           6 direct, two workers
//   3 im2col, DOT8 (v1's xe_dot8_gemm)           7 direct, DOT8 (each kernel row: a dot8 and one product)
//   4 im2col, the NPU (784 x 1 x 25)             8 direct, the NPU (19.3's two-level A addressing)
// Two windows (matrix.md §4), each over v1's four iterations:
//   e2e     v1's window (software/benchmarks/workload_conv2d_engine.c), kept for the v1 gate (§5): START, then
//           four times building the inputs (the image and the kernel; the im2col matrix for an im2col method),
//           the engine, and the output's checksum, then FREEZE.
//   kernel  the engine alone, its inputs in place (the im2col matrix built) and the NPU's descriptor written:
//           each iteration's interval from START (RESUME) to the engine's end, the four summed. Two workers'
//           shares are released by a flag inside it, hart 1 armed before it (matrix_window.h).
// A cold run (matrix_cold.h) is v1's window as the first pass after reset; a warm run is, for each window, an
// untimed pass of the same code, then the window (so the window's code is warm too, not only its data). Before
// each timed pass (and each kernel iteration) the outputs are poisoned. The output is the same for every method, and workload_reference.conv2d_checksum recomputes the
// checksum (each window's: the four outputs folded, v1's).
#include <stdint.h>

#include "aster.h"
#include "aster_dot8.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "xe_kernels.h"

#ifndef CONV_METHOD
#define CONV_METHOD 1
#endif
#ifndef CONV_SEED
#define CONV_SEED 0x13570000u                          // v1's
#endif
#define CONV_H 32u
#define CONV_W 32u
#define CONV_K 5u
#define CONV_ITERATIONS 4u
#define CONV_OH (CONV_H - CONV_K + 1u)
#define CONV_OW (CONV_W - CONV_K + 1u)
#define CONV_M (CONV_OH * CONV_OW)
#define CONV_KK (CONV_K * CONV_K)
#define IM2COL (CONV_METHOD <= 4)
#define NPU (CONV_METHOD == 4 || CONV_METHOD == 8)
#define WORKERS ((CONV_METHOD == 2 || CONV_METHOD == 6) ? 2u : 1u)

static int8_t image[CONV_H * CONV_W] __attribute__((aligned(16)));
static int8_t kernel[CONV_KK] __attribute__((aligned(16)));
#if IM2COL
static int8_t im2col[CONV_M * CONV_KK] __attribute__((aligned(16)));
#endif
static int32_t output[CONV_M] __attribute__((aligned(16)));
static volatile uint32_t engine_failed;

// (the computation's functions are compiled out of line, noinline, so that their code is the same in every
// caller and build: inlined, a change elsewhere in the program changed a hot loop's code, matrix.md §10)
static __attribute__((noinline)) void build_image_and_kernel(void) {
    for (uint32_t i = 0; i < CONV_H * CONV_W; ++i) image[i] = (int8_t)((CONV_SEED ^ (i * 0x1021u)) & 0xffu);
    for (uint32_t i = 0; i < CONV_KK; ++i) kernel[i] = (int8_t)((CONV_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
}

#if IM2COL
__attribute__((unused, noinline)) static void build_im2col(uint32_t row0, uint32_t row1) {
    for (uint32_t row = row0; row < row1; ++row) {
        const uint32_t oy = row / CONV_OW, ox = row % CONV_OW;
        for (uint32_t ky = 0; ky < CONV_K; ++ky)
            for (uint32_t kx = 0; kx < CONV_K; ++kx)
                im2col[row * CONV_KK + ky * CONV_K + kx] = image[(oy + ky) * CONV_W + (ox + kx)];
    }
}

__attribute__((unused, noinline)) static void gemm_rows_scalar(uint32_t row0, uint32_t row1) {   // v1's scalar path
    for (uint32_t m = row0; m < row1; ++m) {
        int32_t sum = 0;
        for (uint32_t k = 0; k < CONV_KK; ++k) sum += (int32_t)im2col[m * CONV_KK + k] * (int32_t)kernel[k];
        output[m] = sum;
    }
}
#else
__attribute__((unused, noinline)) static void direct_rows(uint32_t row0, uint32_t row1) {        // the 5x5 window in place
    for (uint32_t row = row0; row < row1; ++row) {
        const uint32_t oy = row / CONV_OW, ox = row % CONV_OW;
        int32_t sum = 0;
#if CONV_METHOD == 7
        for (uint32_t ky = 0; ky < CONV_K; ++ky) {
            const uint8_t *p = (const uint8_t *)image + (oy + ky) * CONV_W + ox;
            const uint8_t *q = (const uint8_t *)kernel + ky * CONV_K;
            sum += (int32_t)aster_dot8_packed(aster_dot8_pack4(p), aster_dot8_pack4(q));
            sum += (int32_t)(int8_t)p[4] * (int32_t)(int8_t)q[4];
        }
#else
        for (uint32_t ky = 0; ky < CONV_K; ++ky)
            for (uint32_t kx = 0; kx < CONV_K; ++kx)
                sum += (int32_t)image[(oy + ky) * CONV_W + ox + kx] * (int32_t)kernel[ky * CONV_K + kx];
#endif
        output[row] = sum;
    }
}
#endif

#if NPU
#if CONV_METHOD == 4
static const struct aster_npu2_job job = {im2col, kernel, output, CONV_KK, 1u, 4u, CONV_M, 1u, CONV_KK, 0, 0, 0, 0, 0};
#else
// A(i, k) = image + (i div OW) W + (i mod OW) + (k div K) W + (k mod K): the image read in place
static const struct aster_npu2_job job = {image, kernel, output, 1u, 1u, 4u, CONV_M, 1u, CONV_KK, 0,
                                          CONV_OW, CONV_W, CONV_K, CONV_W};
#endif

static void npu_outcome(uint32_t status) {             // a job's outcome noted, and acknowledged
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
}

static uint32_t npu_status;                            // the kernel window's job: its end, noted after the window
#endif

#if WORKERS == 2
// hart 1's share: the upper half of the rows (arg nonzero: the e2e window's, its im2col rows too)
static void upper_rows(void *arg) {
    matrix_stamp_start(1);
#if IM2COL
    if (arg) build_im2col(CONV_M / 2u, CONV_M);
    gemm_rows_scalar(CONV_M / 2u, CONV_M);
#else
    (void)arg;
    direct_rows(CONV_M / 2u, CONV_M);
#endif
    matrix_stamp_end(1);
}
#endif

// the engine on inputs in place (the kernel window's; an NPU job's descriptor already written, its outcome
// noted after the window)
static void engine(void) {
#if CONV_METHOD == 1
    gemm_rows_scalar(0, CONV_M);
#elif CONV_METHOD == 3
    const struct aster_npu_gemm dot8_job = {im2col, kernel, output, CONV_KK, 1u, 4u, CONV_M, 1u, CONV_KK};
    xe_dot8_gemm(&dot8_job);
#elif NPU
    aster_npu2_go();
    npu_status = aster_npu2_wait();
#elif WORKERS == 2
    matrix_release();
#if IM2COL
    gemm_rows_scalar(0, CONV_M / 2u);
#else
    direct_rows(0, CONV_M / 2u);
#endif
    matrix_stamp_end(0);                               // (hart 0's last share ends; it waits)
    matrix_await();
#else
    direct_rows(0, CONV_M);
#endif
}

// one iteration of v1's window: the inputs built, the engine, as v1
static void iteration_e2e(void) {
    build_image_and_kernel();
#if NPU
#if IM2COL
    build_im2col(0, CONV_M);
#endif
    aster_npu2_start(&job);
    npu_outcome(aster_npu2_wait());
#elif WORKERS == 2
    __asm__ volatile ("fence rw, rw" ::: "memory");
    aster_smp_dispatch(upper_rows, (void *)1);
#if IM2COL
    build_im2col(0, CONV_M / 2u);
    gemm_rows_scalar(0, CONV_M / 2u);
#else
    direct_rows(0, CONV_M / 2u);
#endif
    aster_smp_join();
#else
#if IM2COL
    build_im2col(0, CONV_M);
#endif
    engine();
#endif
    __asm__ volatile ("fence rw, rw" ::: "memory");
}

static __attribute__((noinline)) uint32_t fold(uint32_t checksum) {              // v1's: each output in turn, across the iterations
    for (uint32_t i = 0; i < CONV_M; ++i) checksum = (checksum * 33u) ^ (uint32_t)output[i];
    return checksum;
}

// the pass's outputs poisoned (matrix_window.h): with two workers, hart 1 its half of the output and, for
// im2col, of the matrix (the e2e window's upper rows), hart 0 the rest
#if WORKERS == 2
static void upper_poison(void *matrix_too) {
#if IM2COL
    if (matrix_too) matrix_poison(&im2col[(CONV_M / 2u) * CONV_KK], (CONV_M - CONV_M / 2u) * CONV_KK);
#else
    (void)matrix_too;
#endif
    matrix_poison(&output[CONV_M / 2u], 4u * (CONV_M - CONV_M / 2u));
}
#endif

static void poison_outputs(int all) {
    (void)all;                                         // (the im2col matrix's flag)
#if WORKERS == 2
    aster_smp_dispatch(upper_poison, (void *)(uintptr_t)all);
#if IM2COL
    if (all) matrix_poison(im2col, (CONV_M / 2u) * CONV_KK);
#endif
    matrix_poison(output, 4u * (CONV_M / 2u));
    aster_smp_join();
#else
#if IM2COL
    if (all) matrix_poison(im2col, sizeof im2col);
#endif
    matrix_poison(output, sizeof output);
#endif
}

static void poison_all(void) {
    matrix_poison(image, sizeof image);
    matrix_poison(kernel, sizeof kernel);
    poison_outputs(1);
}

static uint32_t run_e2e(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 0; iteration < CONV_ITERATIONS; ++iteration) {
        matrix_last = iteration == CONV_ITERATIONS - 1u;
        iteration_e2e();
        checksum = fold(checksum);
    }
    return checksum;
}

static uint32_t run_kernel(void) {
    uint32_t checksum = 0;
    for (uint32_t iteration = 0; iteration < CONV_ITERATIONS; ++iteration) {
        build_image_and_kernel();                      // (outside the window: the inputs put in place)
#if IM2COL
        build_im2col(0, CONV_M);
#endif
        poison_outputs(0);
        matrix_last = iteration == CONV_ITERATIONS - 1u;
#if NPU
        aster_npu2_describe(&job);
#elif WORKERS == 2
        matrix_arm(upper_rows, 0);
#endif
        matrix_open(iteration == 0);
        engine();
        matrix_close();
#if NPU
        npu_outcome(npu_status);
#elif WORKERS == 2
        aster_smp_join();
#endif
        checksum = fold(checksum);
    }
    return checksum;
}

static void emit(struct v12_record *record, const char *window, uint32_t checksum) {
    static const char *const names[] = {"", "conv2d_im2col_scalar", "conv2d_im2col_multicore", "conv2d_im2col_dot8",
                                        "conv2d_im2col_npu", "conv2d_direct_scalar", "conv2d_direct_multicore",
                                        "conv2d_direct_dot8", "conv2d_direct_npu"};
    static const char *const methods[] = {"", "scalar", "multicore", "dot8", "npu_im2col", "scalar", "multicore",
                                          "dot8", "npu_direct"};
    record->name = names[CONV_METHOD]; record->family = "dsp"; record->method = methods[CONV_METHOD];
    record->window = window; record->cache_state = MATRIX_CACHE_STATE;
    record->size = CONV_H * CONV_W; record->iterations = CONV_ITERATIONS; record->param = CONV_K;
    record->seed = CONV_SEED; record->checksum = checksum; record->workers = WORKERS; record->pass = !engine_failed;
    v12_emit(record);
}

int main(void) {
    static struct v12_record record;
#if WORKERS == 2
    aster_smp_start();
#endif
    if (!matrix_cold) (void)run_e2e();                 // the warm-up pass
    poison_all();
    engine_failed = 0;
    matrix_stamps_clear();
    v12_prepare();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t e2e = run_e2e();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    v12_end(&record);
    matrix_hart0_whole(&record);                       // (v1's window ends in hart 0's checksum)
    emit(&record, "e2e", e2e);
    if (!matrix_cold) {
        (void)run_kernel();                            // the kernel window's warm-up
        poison_all();
        matrix_stamps_clear();
        v12_prepare();
        const uint32_t kernel_sum = run_kernel();
        v12_end(&record);
#if NPU
        matrix_hart0_none(&record);                    // (hart 0 only starts the job and polls it)
#elif WORKERS == 1
        matrix_hart0_whole(&record);                   // (the engine is hart 0's, to the interval's end)
#endif
        emit(&record, "kernel", kernel_sum);
    }
    return engine_failed;
}
