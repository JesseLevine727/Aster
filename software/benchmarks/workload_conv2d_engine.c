// AsterBench v10: engine-accelerated 2D convolution on the coherent SoC.
// The signed-INT8 convolution is lowered to an im2col GEMM and executed by the
// Xasterdot8 instruction (CONV_ENGINE=0) or the 4x4 NPU (CONV_ENGINE=1). The
// output must match the scalar conv2d workload's independent oracle.
#include <stdint.h>
#include "aster.h"
#include "aster_npu.h"
#include "aster_dot8.h"
#include "xe_kernels.h"
#include "workload_coh.h"

#ifndef CONV_ENGINE
#define CONV_ENGINE 1
#endif
#ifndef CONV_H
#define CONV_H 32u
#endif
#ifndef CONV_W
#define CONV_W 32u
#endif
#ifndef CONV_K
#define CONV_K 5u
#endif
#ifndef CONV_ITERATIONS
#define CONV_ITERATIONS 4u
#endif
#define CONV_SEED 0x13570000u

#define CONV_OH (CONV_H - CONV_K + 1u)
#define CONV_OW (CONV_W - CONV_K + 1u)
#define CONV_M (CONV_OH * CONV_OW)
#define CONV_KK (CONV_K * CONV_K)

_Static_assert(CONV_M <= 1024u && CONV_KK <= 1024u, "convolution exceeds the NPU bound");

static int8_t image[CONV_H * CONV_W];
static int8_t kernel[CONV_K * CONV_K];
static int8_t im2col[CONV_M * CONV_KK];
static int32_t output[CONV_M];

void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }

static void build_inputs(void) {
    for (uint32_t i = 0; i < CONV_H * CONV_W; ++i)
        image[i] = (int8_t)((CONV_SEED ^ (i * 0x1021u)) & 0xffu);
    for (uint32_t i = 0; i < CONV_K * CONV_K; ++i)
        kernel[i] = (int8_t)((CONV_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
    for (uint32_t oy = 0; oy < CONV_OH; ++oy) {
        for (uint32_t ox = 0; ox < CONV_OW; ++ox) {
            const uint32_t row = oy * CONV_OW + ox;
            for (uint32_t ky = 0; ky < CONV_K; ++ky)
                for (uint32_t kx = 0; kx < CONV_K; ++kx)
                    im2col[row * CONV_KK + ky * CONV_K + kx] = image[(oy + ky) * CONV_W + (ox + kx)];
        }
    }
}

static int run_engine(void) {
    struct aster_npu_gemm job = {im2col, kernel, output, CONV_KK, 1u, 4u, CONV_M, 1u, CONV_KK};
#if CONV_ENGINE == 1
    struct aster_npu_status status;
    enum aster_npu_result result = aster_npu_submit(&job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &status);
    else aster_npu_poll(&status);
    return result == ASTER_NPU_OK ? 0 : -1;
#else
    xe_dot8_gemm(&job);
    return 0;
#endif
}

int main(void) {
    if (coh_reg(COH_CPU_ABI) != COH_CPU_ABI_VALUE) {
        aster_puts("CONV BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    *(volatile uint32_t *)COH_CONTROL = 1u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint32_t checksum = 0;
    for (uint32_t iteration = 0; iteration < CONV_ITERATIONS; ++iteration) {
        build_inputs();
        __asm__ volatile ("fence rw,rw" ::: "memory");
        if (run_engine()) { aster_puts("CONV ENGINE FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        for (uint32_t i = 0; i < CONV_M; ++i) checksum = (checksum * 33u) ^ (uint32_t)output[i];
    }
    *(volatile uint32_t *)COH_CONTROL = 2u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
#if CONV_ENGINE == 1
    aster_workload_emit_coh("conv2d_npu", "dsp", CONV_H * CONV_W, CONV_ITERATIONS, CONV_K,
                            CONV_SEED, checksum, 1u);
#else
    aster_workload_emit_coh("conv2d_dot8", "dsp", CONV_H * CONV_W, CONV_ITERATIONS, CONV_K,
                            CONV_SEED, checksum, 1u);
#endif
    for (;;) { }
}
