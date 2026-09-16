// AsterBench v10: 2D convolution CPU baseline.
// Signed INT8 image and kernel, INT32 accumulation modulo 2^32. The host
// oracle recomputes every output and the checksum independently.
#include <stdint.h>
#include "workload.h"

#ifndef CONV_H
#define CONV_H 32u
#endif
#ifndef CONV_W
#define CONV_W 32u
#endif
#ifndef CONV_K
#define CONV_K 5u
#endif

#define CONV_OH (CONV_H - CONV_K + 1u)
#define CONV_OW (CONV_W - CONV_K + 1u)

_Static_assert(CONV_K >= 1u && CONV_K <= CONV_H && CONV_K <= CONV_W, "bad kernel size");

static int8_t image[CONV_H * CONV_W];
static int8_t kernel[CONV_K * CONV_K];
static int32_t output[CONV_OH * CONV_OW];

__attribute__((noinline, noclone, section(".text.benchmark")))
static void convolve(void) {
    for (uint32_t oy = 0; oy < CONV_OH; ++oy) {
        for (uint32_t ox = 0; ox < CONV_OW; ++ox) {
            int32_t sum = 0;
            for (uint32_t ky = 0; ky < CONV_K; ++ky)
                for (uint32_t kx = 0; kx < CONV_K; ++kx)
                    sum += (int32_t)image[(oy + ky) * CONV_W + (ox + kx)]
                         * (int32_t)kernel[ky * CONV_K + kx];
            output[oy * CONV_OW + ox] = sum;
        }
    }
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t run(uint32_t iterations) {
    uint32_t checksum = 0;
    for (uint32_t repetition = 0; repetition < iterations; ++repetition) {
        for (uint32_t i = 0; i < CONV_H * CONV_W; ++i)
            image[i] = (int8_t)((BENCHMARK_SEED ^ (i * 0x1021u)) & 0xffu);
        for (uint32_t i = 0; i < CONV_K * CONV_K; ++i)
            kernel[i] = (int8_t)((BENCHMARK_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
        convolve();
        for (uint32_t i = 0; i < CONV_OH * CONV_OW; ++i)
            checksum = (checksum * 33u) ^ (uint32_t)output[i];
    }
    return checksum;
}

int main(void) {
    aster_perf_clear();
    uint32_t checksum = run(BENCHMARK_REPETITIONS);
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);
    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    aster_workload_emit("conv2d", "dsp", CONV_H * CONV_W, BENCHMARK_REPETITIONS, CONV_K,
                        BENCHMARK_SEED, checksum, pass, &snapshot);
    for (;;) { }
}
