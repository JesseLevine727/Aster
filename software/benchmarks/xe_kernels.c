#include "xe_kernels.h"
#include "aster_dot8.h"

// The scalar reference is the Phase 9 independent oracle. Keeping one
// implementation avoids a second, subtly different signed-arithmetic model.
void xe_scalar_gemm(const struct aster_npu_gemm *job) {
    aster_npu_scalar_gemm(job);
}

// Packed Xasterdot8 form of the same descriptor. A is packed four k-values at
// a time; B is gathered with its byte stride. A scalar tail handles the
// remaining 0..3 k positions. All accumulation is unsigned modulo 2^32, which
// is bit-identical to the signed reference after masking.
void xe_dot8_gemm(const struct aster_npu_gemm *job) {
    const uint8_t *a = (const uint8_t *)(const void *)job->a;
    const uint8_t *b = (const uint8_t *)(const void *)job->b;
    uint8_t *c = (uint8_t *)(void *)job->c;
    for (uint32_t i = 0; i < job->m; ++i) {
        for (uint32_t j = 0; j < job->n; ++j) {
            uint32_t sum = 0, k = 0;
            for (; job->k - k >= 4u; k += 4u) {
                const uint8_t *bp = b + (uint64_t)k * job->b_stride + j;
                const uint32_t packed =
                    (uint32_t)bp[0] |
                    ((uint32_t)bp[job->b_stride] << 8) |
                    ((uint32_t)bp[2u * job->b_stride] << 16) |
                    ((uint32_t)bp[3u * job->b_stride] << 24);
                sum += aster_dot8_packed(
                    aster_dot8_pack4(a + (uint64_t)i * job->a_stride + k), packed);
            }
            for (; k < job->k; ++k) {
                sum += (uint32_t)(aster_dot8_signed_byte(a[(uint64_t)i * job->a_stride + k]) *
                                  aster_dot8_signed_byte(b[(uint64_t)k * job->b_stride + j]));
            }
            const uint32_t offset = i * job->c_stride + 4u * j;
            for (uint32_t byte = 0; byte < 4u; ++byte)
                c[offset + byte] = (uint8_t)(sum >> (8u * byte));
        }
    }
}

uint64_t xe_mac_count(const struct aster_npu_gemm *job) {
    return (uint64_t)job->m * job->n * job->k;
}
