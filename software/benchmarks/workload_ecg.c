// AsterBench v10: streaming ECG heterogeneous pipeline.
// Per chunk: CPU stages a synthetic ECG-like sample window, DMA moves it,
// Xasterdot8 runs the FIR filter, the CPU extracts features and the 4x4 NPU
// classifies them. The primary hart orchestrates while the secondary runs the
// DOT8 FIR. The host oracle reproduces the entire pipeline exactly.
#include <stdint.h>
#include <stdatomic.h>
#include "aster.h"
#include "aster_dma.h"
#include "aster_npu.h"
#include "aster_dot8.h"
#include "xe_kernels.h"
#include "workload_coh.h"

#ifndef ECG_CHUNKS
#define ECG_CHUNKS 16u
#endif
#ifndef ECG_CHUNK
#define ECG_CHUNK 64u
#endif
#ifndef ECG_COEF
#define ECG_COEF 16u
#endif
#ifndef ECG_SEED
#define ECG_SEED 0x13570000u
#endif

#define ECG_FOUT (ECG_CHUNK - ECG_COEF + 1u)
#define ECG_FEATURES 4u
#define ECG_CLASSES 3u

_Static_assert(ECG_CHUNK > ECG_COEF, "chunk must exceed the filter length");
_Static_assert(ECG_FOUT <= 1024u && ECG_COEF <= 1024u, "pipeline exceeds the NPU bound");

static int8_t raw[ECG_CHUNK];
static int8_t dma_buf[ECG_CHUNK];
static int8_t coef[ECG_COEF];
static int32_t filtered[ECG_FOUT];
static int8_t feat_q[ECG_FEATURES];
static int8_t weights[ECG_CLASSES * ECG_FEATURES];
static int32_t scores[ECG_CLASSES];

static _Atomic uint32_t ecg_epoch, ecg_done;

static int8_t sample(uint32_t t) {
    const uint32_t p = t % 32u;
    if (p == 0u) return 120;
    if (p == 1u || p == 31u) return 40;
    if (p == 2u || p == 30u) return -20;
    return (int8_t)((int32_t)((t * 7u) % 9u) - 4);
}

void aster_secondary_main(void) {
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&ecg_epoch, memory_order_acquire);
        if (epoch != 0u && epoch != last) {
            last = epoch;
            struct aster_npu_gemm job = {dma_buf, coef, filtered, 1u, 1u, 4u, ECG_FOUT, 1u, ECG_COEF};
            xe_dot8_gemm(&job);
            atomic_store_explicit(&ecg_done, epoch, memory_order_release);
        }
    }
}

static int32_t trunc_div(int32_t value, int32_t divisor) {
    const int32_t quotient = (value < 0 ? -value : value) / divisor;
    return value < 0 ? -quotient : quotient;
}

static int8_t clamp8(int32_t value) {
    if (value < -128) return -128;
    if (value > 127) return 127;
    return (int8_t)value;
}

int main(void) {
    if (coh_reg(COH_CPU_ABI) != COH_CPU_ABI_VALUE) {
        aster_puts("ECG BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    for (uint32_t i = 0; i < ECG_COEF; ++i)
        coef[i] = (int8_t)((ECG_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
    for (uint32_t c = 0; c < ECG_CLASSES; ++c)
        for (uint32_t f = 0; f < ECG_FEATURES; ++f)
            weights[c * ECG_FEATURES + f] = (int8_t)((ECG_SEED ^ (c * 0x85ebca6bu) ^ (f * 0x1021u)) & 0xffu);
    *(volatile uint32_t *)0x20002004u = 1u;  // release the secondary
    *(volatile uint32_t *)COH_CONTROL = 1u;
    __asm__ volatile ("fence rw,rw" ::: "memory");

    uint32_t checksum = 0;
    for (uint32_t chunk = 0; chunk < ECG_CHUNKS; ++chunk) {
        for (uint32_t i = 0; i < ECG_CHUNK; ++i) raw[i] = sample(chunk * ECG_CHUNK + i);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        if (aster_dma_copy(dma_buf, raw, ECG_CHUNK, 8000000u) != ASTER_DMA_OK) {
            aster_puts("ECG DMA FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        atomic_store_explicit(&ecg_done, 0u, memory_order_relaxed);
        atomic_store_explicit(&ecg_epoch, chunk + 1u, memory_order_release);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        while (atomic_load_explicit(&ecg_done, memory_order_acquire) != chunk + 1u) { }
        __asm__ volatile ("fence rw,rw" ::: "memory");

        int32_t peak = 0, sum_scaled = 0, abs_sum = 0, previous_sign = 0;
        uint32_t zero_crossings = 0;
        for (uint32_t r = 0; r < ECG_FOUT; ++r) {
            const int32_t value = filtered[r];
            const int32_t scaled = value >> 8;
            sum_scaled += scaled;
            const int32_t magnitude = scaled < 0 ? -scaled : scaled;
            abs_sum += magnitude;
            if (magnitude > peak) peak = magnitude;
            const int32_t sign = value > 0 ? 1 : (value < 0 ? -1 : 0);
            if (previous_sign != 0 && sign != 0 && sign != previous_sign) ++zero_crossings;
            if (sign != 0) previous_sign = sign;
        }
        feat_q[0] = clamp8(peak >> 4);
        feat_q[1] = clamp8(trunc_div(abs_sum, (int32_t)ECG_FOUT) >> 4);
        feat_q[2] = clamp8((int32_t)zero_crossings);
        feat_q[3] = clamp8(trunc_div(sum_scaled, (int32_t)ECG_FOUT) >> 4);

        struct aster_npu_gemm classify = {weights, feat_q, scores, ECG_FEATURES, 1u, 4u,
                                          ECG_CLASSES, 1u, ECG_FEATURES};
        struct aster_npu_status status;
        enum aster_npu_result result = aster_npu_submit(&classify);
        if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &status);
        else aster_npu_poll(&status);
        if (result != ASTER_NPU_OK) { aster_puts("ECG NPU FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        int best = 0;
        for (uint32_t c = 1; c < ECG_CLASSES; ++c) if (scores[c] > scores[best]) best = (int)c;

        checksum = (checksum * 33u) ^ (uint32_t)filtered[0];
        checksum = (checksum * 33u) ^ (uint32_t)filtered[ECG_FOUT - 1u];
        for (uint32_t f = 0; f < ECG_FEATURES; ++f) checksum = (checksum * 33u) ^ (uint32_t)(uint8_t)feat_q[f];
        for (uint32_t c = 0; c < ECG_CLASSES; ++c) checksum = (checksum * 33u) ^ (uint32_t)scores[c];
        checksum = (checksum * 33u) ^ (uint32_t)best;
    }
    *(volatile uint32_t *)COH_CONTROL = 2u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    aster_workload_emit_coh("streaming_ecg", "system", ECG_CHUNK, ECG_CHUNKS, ECG_COEF,
                            ECG_SEED, checksum, 1u);
    for (;;) { }
}
