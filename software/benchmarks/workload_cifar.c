// AsterBench v10: tiny quantized CIFAR-10 CNN on the coherent NPU SoC.
// Per image: im2col + NPU convolution, requantize + ReLU + 2x2 max pool, then
// an NPU fully-connected layer and argmax. The host oracle recomputes the same
// integer network from the model artifact.
#include <stdint.h>
#include "aster.h"
#include "aster_npu.h"
#include "workload_coh.h"
#include "cifar_model.h"
#include "cifar_images.h"

#define CIFAR_OH2 (CIFAR_OH * CIFAR_OH)
#define CIFAR_POOL (CIFAR_OH / 2)
#define CIFAR_POOLED (CIFAR_CONV_OUT * CIFAR_POOL * CIFAR_POOL)

_Static_assert(CIFAR_POOLED == CIFAR_FC_IN, "pooled features must match the fc input");
_Static_assert(CIFAR_OH2 <= 1024u && CIFAR_CONV_K <= 1024u && CIFAR_FC_IN <= 1024u,
               "CNN geometry exceeds the NPU bound");

static int8_t image[3 * CIFAR_H * CIFAR_H];
static int8_t im2col[CIFAR_OH2 * CIFAR_CONV_K];
static int32_t conv_acc[CIFAR_OH2 * CIFAR_CONV_OUT];
static int8_t conv_q[CIFAR_CONV_OUT * CIFAR_OH2];
static int8_t pooled[CIFAR_POOLED];
static int32_t fc_acc[CIFAR_FC_OUT];
static int8_t logits[CIFAR_FC_OUT];

static int8_t conv_weights_b[CIFAR_CONV_K * CIFAR_CONV_OUT];
static int8_t fc_weights[CIFAR_FC_OUT * CIFAR_FC_IN];
static int32_t conv_bias_q[CIFAR_CONV_OUT];
static int32_t fc_bias_q[CIFAR_FC_OUT];

void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }

static int32_t round_shift(int64_t product, int shift) {
    if (shift == 0) return (int32_t)product;
    const int64_t half = (int64_t)1 << (shift - 1);
    if (product >= 0) return (int32_t)((product + half) >> shift);
    return (int32_t)(-((-product + half) >> shift));
}
static int8_t requant(int32_t accumulator, int32_t mult, int shift, int32_t bias_q) {
    int32_t value = round_shift((int64_t)accumulator * mult, shift) + bias_q;
    if (value < -128) value = -128;
    if (value > 127) value = 127;
    return (int8_t)value;
}
static void copy_bytes(int8_t *destination, const int8_t *source, uint32_t length) {
    for (uint32_t i = 0; i < length; ++i) destination[i] = source[i];
}

static void build_im2col(void) {
    for (uint32_t oy = 0; oy < CIFAR_OH; ++oy)
        for (uint32_t ox = 0; ox < CIFAR_OH; ++ox) {
            const uint32_t row = oy * CIFAR_OH + ox;
            for (uint32_t c = 0; c < 3u; ++c)
                for (uint32_t ky = 0; ky < CIFAR_CONV_KERNEL; ++ky)
                    for (uint32_t kx = 0; kx < CIFAR_CONV_KERNEL; ++kx) {
                        const uint32_t k = c * 9u + ky * 3u + kx;
                        im2col[row * CIFAR_CONV_K + k] =
                            image[(c * CIFAR_H + oy + ky) * CIFAR_H + (ox + kx)];
                    }
        }
}

static int run_npu(struct aster_npu_gemm *job) {
    struct aster_npu_status status;
    enum aster_npu_result result = aster_npu_submit(job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &status);
    else aster_npu_poll(&status);
    return result == ASTER_NPU_OK ? 0 : -1;
}

int main(void) {
    if (coh_reg(COH_CPU_ABI) != COH_CPU_ABI_VALUE) {
        aster_puts("CIFAR BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    copy_bytes(conv_weights_b, cifar_conv_weights_b, sizeof conv_weights_b);
    copy_bytes(fc_weights, cifar_fc_weights, sizeof fc_weights);
    for (uint32_t i = 0; i < CIFAR_CONV_OUT; ++i) conv_bias_q[i] = cifar_conv_bias_q[i];
    for (uint32_t i = 0; i < CIFAR_FC_OUT; ++i) fc_bias_q[i] = cifar_fc_bias_q[i];
    __asm__ volatile ("fence rw,rw" ::: "memory");

    *(volatile uint32_t *)COH_CONTROL = 1u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint32_t checksum = 0, mismatches = 0;
    for (uint32_t item = 0; item < CIFAR_TEST_COUNT; ++item) {
        copy_bytes(image, &cifar_test_images[item * 3u * CIFAR_H * CIFAR_H], sizeof image);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        build_im2col();

        struct aster_npu_gemm conv_job = {im2col, conv_weights_b, conv_acc,
                                          CIFAR_CONV_K, CIFAR_CONV_OUT, 4u * CIFAR_CONV_OUT,
                                          CIFAR_OH2, CIFAR_CONV_OUT, CIFAR_CONV_K};
        if (run_npu(&conv_job)) { aster_puts("CIFAR CONV FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        for (uint32_t row = 0; row < CIFAR_OH2; ++row)
            for (uint32_t n = 0; n < CIFAR_CONV_OUT; ++n) {
                int8_t value = requant(conv_acc[row * CIFAR_CONV_OUT + n], CIFAR_CONV_MULT,
                                       CIFAR_CONV_SHIFT, conv_bias_q[n]);
                conv_q[n * CIFAR_OH2 + row] = value > 0 ? value : 0;
            }
        for (uint32_t n = 0; n < CIFAR_CONV_OUT; ++n)
            for (uint32_t py = 0; py < CIFAR_POOL; ++py)
                for (uint32_t px = 0; px < CIFAR_POOL; ++px) {
                    int8_t best = -128;
                    for (uint32_t dy = 0; dy < 2u; ++dy)
                        for (uint32_t dx = 0; dx < 2u; ++dx) {
                            const int8_t value = conv_q[n * CIFAR_OH2 + (2u * py + dy) * CIFAR_OH + (2u * px + dx)];
                            if (value > best) best = value;
                        }
                    pooled[n * CIFAR_POOL * CIFAR_POOL + py * CIFAR_POOL + px] = best;
                }
        __asm__ volatile ("fence rw,rw" ::: "memory");

        struct aster_npu_gemm fc_job = {fc_weights, pooled, fc_acc, CIFAR_FC_IN, 1u, 4u,
                                        CIFAR_FC_OUT, 1u, CIFAR_FC_IN};
        if (run_npu(&fc_job)) { aster_puts("CIFAR FC FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        int best = 0;
        for (uint32_t o = 0; o < CIFAR_FC_OUT; ++o) {
            logits[o] = requant(fc_acc[o], CIFAR_FC_MULT, CIFAR_FC_SHIFT, fc_bias_q[o]);
            if (logits[o] > logits[best]) best = (int)o;
        }
        if ((uint32_t)best != cifar_test_classes[item]) ++mismatches;
        for (uint32_t o = 0; o < CIFAR_FC_OUT; ++o) checksum = (checksum * 33u) ^ (uint32_t)(uint8_t)logits[o];
        checksum = (checksum * 33u) ^ (uint32_t)best;
    }
    *(volatile uint32_t *)COH_CONTROL = 2u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    aster_workload_emit_coh("cifar_cnn", "ml", 3u * CIFAR_H * CIFAR_H, CIFAR_TEST_COUNT,
                            CIFAR_CONV_OUT, 0u, checksum, mismatches == 0u ? 1u : 0u);
    for (;;) { }
}
