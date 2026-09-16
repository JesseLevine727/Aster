// AsterBench v10: fixed-point radix-2 FFT.
// N=256, Q15 twiddles, int32 data with a 1/2 scale per stage. The host oracle
// recomputes the identical integer transform and checksum.
#include <stdint.h>
#include "workload.h"
#include "workload_fft_twiddle.h"

#ifndef FFT_ITERATIONS
#define FFT_ITERATIONS BENCHMARK_REPETITIONS
#endif

static int32_t re[FFT_N];
static int32_t im[FFT_N];

__attribute__((noinline, noclone, section(".text.benchmark")))
static void fft(void) {
    for (uint32_t i = 1, j = 0; i < FFT_N; ++i) {
        uint32_t bit = FFT_N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            int32_t tr = re[i]; re[i] = re[j]; re[j] = tr;
            int32_t ti = im[i]; im[i] = im[j]; im[j] = ti;
        }
    }
    for (uint32_t length = 2; length <= FFT_N; length <<= 1) {
        const uint32_t half = length >> 1;
        const uint32_t step = FFT_N / length;
        for (uint32_t start = 0; start < FFT_N; start += length) {
            for (uint32_t k = 0; k < half; ++k) {
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
    }
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t run(uint32_t iterations) {
    uint32_t checksum = 0;
    for (uint32_t repetition = 0; repetition < iterations; ++repetition) {
        for (uint32_t i = 0; i < FFT_N; ++i) {
            re[i] = (int32_t)((BENCHMARK_SEED ^ (i * 0x1021u)) & 0xffffu) - 32768;
            im[i] = 0;
        }
        fft();
        for (uint32_t i = 0; i < FFT_N; ++i) checksum = (checksum * 33u) ^ (uint32_t)re[i];
        for (uint32_t i = 0; i < FFT_N; ++i) checksum = (checksum * 33u) ^ (uint32_t)im[i];
    }
    return checksum;
}

int main(void) {
    aster_perf_clear();
    uint32_t checksum = run(FFT_ITERATIONS);
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);
    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    aster_workload_emit("fft", "dsp", FFT_N * 4u, FFT_ITERATIONS, FFT_N, BENCHMARK_SEED,
                        checksum, pass, &snapshot);
    for (;;) { }
}
