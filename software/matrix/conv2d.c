// 20.4's matrix: Conv2D 32x32, K = 5 (docs/matrix.md §4.5; a v1-retained workload), v1's inputs, computation
// and window (software/benchmarks/workload_conv2d_engine.c): START, then four iterations of building the
// inputs (the image and the kernel; the im2col matrix for an im2col method), the engine, and the output's
// checksum, then FREEZE. CONV_METHOD:
//   1 im2col, scalar (v1's scalar path)          5 direct, scalar (the 5x5 loop on the image)
//   2 im2col, two workers (rows split)           6 direct, two workers
//   3 im2col, DOT8 (v1's xe_dot8_gemm)           7 direct, DOT8 (each kernel row: a dot8 and one product)
//   4 im2col, the NPU (784 x 1 x 25)             8 direct, the NPU (19.3's two-level A addressing)
// The output is the same for all, and workload_reference.conv2d_checksum recomputes its checksum. A warm run's
// untimed pass first; a cold run's first pass is timed.
#include <stdint.h>

#include "aster.h"
#include "aster_dot8.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "xe_kernels.h"

#ifndef CONV_METHOD
#define CONV_METHOD 1
#endif
#define CONV_H 32u
#define CONV_W 32u
#define CONV_K 5u
#define CONV_ITERATIONS 4u
#define CONV_SEED 0x13570000u
#define CONV_OH (CONV_H - CONV_K + 1u)
#define CONV_OW (CONV_W - CONV_K + 1u)
#define CONV_M (CONV_OH * CONV_OW)
#define CONV_KK (CONV_K * CONV_K)
#define IM2COL (CONV_METHOD <= 4)
#define WORKERS ((CONV_METHOD == 2 || CONV_METHOD == 6) ? 2u : 1u)

static int8_t image[CONV_H * CONV_W] __attribute__((aligned(16)));
static int8_t kernel[CONV_KK] __attribute__((aligned(16)));
#if IM2COL
static int8_t im2col[CONV_M * CONV_KK] __attribute__((aligned(16)));
#endif
static int32_t output[CONV_M] __attribute__((aligned(16)));
static volatile uint32_t engine_failed;

static void build_image_and_kernel(void) {
    for (uint32_t i = 0; i < CONV_H * CONV_W; ++i) image[i] = (int8_t)((CONV_SEED ^ (i * 0x1021u)) & 0xffu);
    for (uint32_t i = 0; i < CONV_KK; ++i) kernel[i] = (int8_t)((CONV_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
}

#if IM2COL
__attribute__((unused)) static void build_im2col(uint32_t row0, uint32_t row1) {
    for (uint32_t row = row0; row < row1; ++row) {
        const uint32_t oy = row / CONV_OW, ox = row % CONV_OW;
        for (uint32_t ky = 0; ky < CONV_K; ++ky)
            for (uint32_t kx = 0; kx < CONV_K; ++kx)
                im2col[row * CONV_KK + ky * CONV_K + kx] = image[(oy + ky) * CONV_W + (ox + kx)];
    }
}

__attribute__((unused)) static void gemm_rows_scalar(uint32_t row0, uint32_t row1) {         // v1's scalar path, for a range of rows
    for (uint32_t m = row0; m < row1; ++m) {
        int32_t sum = 0;
        for (uint32_t k = 0; k < CONV_KK; ++k) sum += (int32_t)im2col[m * CONV_KK + k] * (int32_t)kernel[k];
        output[m] = sum;
    }
}
#else
__attribute__((unused)) static void direct_rows(uint32_t row0, uint32_t row1) {              // the 5x5 window on the image itself
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

__attribute__((unused)) static void npu_run(const struct aster_npu2_job *job) {
    aster_npu2_start(job);
    const uint32_t status = aster_npu2_wait();
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
}

#if WORKERS == 2
static void upper_rows(void *arg) {
    (void)arg;
    v12_work_start(1);
#if IM2COL
    build_im2col(CONV_M / 2u, CONV_M);
    gemm_rows_scalar(CONV_M / 2u, CONV_M);
#else
    direct_rows(CONV_M / 2u, CONV_M);
#endif
    v12_work_end(1);
}
#endif

static void iteration_engine(void) {
    build_image_and_kernel();
#if CONV_METHOD == 1
    build_im2col(0, CONV_M);
    gemm_rows_scalar(0, CONV_M);
#elif CONV_METHOD == 3
    build_im2col(0, CONV_M);
    const struct aster_npu_gemm job = {im2col, kernel, output, CONV_KK, 1u, 4u, CONV_M, 1u, CONV_KK};
    xe_dot8_gemm(&job);
#elif CONV_METHOD == 4
    build_im2col(0, CONV_M);
    const struct aster_npu2_job job = {im2col, kernel, output, CONV_KK, 1u, 4u, CONV_M, 1u, CONV_KK, 0, 0, 0, 0, 0};
    npu_run(&job);
#elif CONV_METHOD == 8
    // A(i, k) = image + (i div OW) W + (i mod OW) + (k div K) W + (k mod K): the image read in place
    const struct aster_npu2_job job = {image, kernel, output, 1u, 1u, 4u, CONV_M, 1u, CONV_KK, 0,
                                       CONV_OW, CONV_W, CONV_K, CONV_W};
    npu_run(&job);
#elif WORKERS == 2
    __asm__ volatile ("fence rw, rw" ::: "memory");
    aster_smp_dispatch(upper_rows, 0);
#if IM2COL
    build_im2col(0, CONV_M / 2u);
    gemm_rows_scalar(0, CONV_M / 2u);
#else
    direct_rows(0, CONV_M / 2u);
#endif
    aster_smp_join();
#else
    direct_rows(0, CONV_M);
#endif
    __asm__ volatile ("fence rw, rw" ::: "memory");
}

static uint32_t run(void) {
    uint32_t checksum = 0;
    v12_work_start(0);
    for (uint32_t iteration = 0; iteration < CONV_ITERATIONS; ++iteration) {
        iteration_engine();
        // (v1 folds each output in turn: (checksum * 33) ^ output, across the iterations)
        for (uint32_t i = 0; i < CONV_M; ++i) checksum = (checksum * 33u) ^ (uint32_t)output[i];
    }
    v12_work_end(0);
    return checksum;
}

int main(void) {
    static struct v12_record record;
#if WORKERS == 2
    aster_smp_start();
#endif
#ifndef MATRIX_COLD
    (void)run();                                       // the warm-up pass
#endif
    engine_failed = 0;
    v12_prepare();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t checksum = run();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    v12_end(&record);
    static const char *const names[] = {"", "conv2d_im2col_scalar", "conv2d_im2col_multicore", "conv2d_im2col_dot8",
                                        "conv2d_im2col_npu", "conv2d_direct_scalar", "conv2d_direct_multicore",
                                        "conv2d_direct_dot8", "conv2d_direct_npu"};
    static const char *const methods[] = {"", "scalar", "multicore", "dot8", "npu_im2col", "scalar", "multicore",
                                          "dot8", "npu_direct"};
    record.name = names[CONV_METHOD]; record.family = "dsp"; record.method = methods[CONV_METHOD];
    record.window = "e2e";
#ifdef MATRIX_COLD
    record.cache_state = "cold";
#else
    record.cache_state = "warm";
#endif
    record.size = CONV_H * CONV_W; record.iterations = CONV_ITERATIONS; record.param = CONV_K;
    record.seed = CONV_SEED; record.checksum = checksum; record.workers = WORKERS; record.pass = !engine_failed;
    v12_emit(&record);
    return engine_failed;
}
