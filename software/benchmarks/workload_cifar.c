// AsterBench v10: tiny quantized CIFAR-10 CNN (two conv layers) on the NPU SoC.
// Per image: im2col + NPU conv1, requant + ReLU + 2x2 pool, im2col + NPU conv2,
// requant + ReLU + 2x2 pool, then an NPU fully-connected layer and argmax.
#include <stdint.h>
#include "aster.h"
#include "aster_npu.h"
#include "workload_coh.h"
#include "cifar_model.h"
#include "cifar_images.h"

#define C1_OH 14
#define C2_OH 5
#define C1_M (C1_OH * C1_OH)
#define C2_M (C2_OH * C2_OH)
#define C1_Q (CIFAR_CONV1_OUT * C1_M)
#define C2_Q (CIFAR_CONV2_OUT * C2_M)
#define POOL1 (CIFAR_CONV1_OUT * 7 * 7)
#define POOL2 (CIFAR_CONV2_OUT * 2 * 2)

_Static_assert(C1_M <= 1024u && CIFAR_CONV1_K <= 1024u, "conv1 exceeds the NPU bound");
_Static_assert(C2_M <= 1024u && CIFAR_CONV2_K <= 1024u, "conv2 exceeds the NPU bound");
_Static_assert(CIFAR_FC_IN <= 1024u && POOL2 == CIFAR_FC_IN, "fc geometry mismatch");

static int8_t image[3 * CIFAR_H * CIFAR_H];
static int8_t im2col[C1_M * CIFAR_CONV1_K];
static int32_t conv_acc[C1_M * CIFAR_CONV1_OUT];
static int8_t conv_q[CIFAR_CONV2_OUT * C1_M];
static int8_t pool1[POOL1];
static int8_t pool2[POOL2];
static int32_t fc_acc[CIFAR_FC_OUT];
static int8_t logits[CIFAR_FC_OUT];

static int8_t conv1_weights_b[CIFAR_CONV1_K * CIFAR_CONV1_OUT];
static int8_t conv2_weights_b[CIFAR_CONV2_K * CIFAR_CONV2_OUT];
static int8_t fc_weights[CIFAR_FC_OUT * CIFAR_FC_IN];
static int32_t conv1_bias_q[CIFAR_CONV1_OUT];
static int32_t conv2_bias_q[CIFAR_CONV2_OUT];
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
static int run_npu(struct aster_npu_gemm *job) {
    struct aster_npu_status status;
    enum aster_npu_result result = aster_npu_submit(job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &status);
    else aster_npu_poll(&status);
    return result == ASTER_NPU_OK ? 0 : -1;
}

// im2col over a CxHxW int8 plane set into a C*9-wide matrix.
static void build_im2col(const int8_t *planes, uint32_t channels, uint32_t height,
                         uint32_t width, uint32_t out_h, uint32_t out_w) {
    for (uint32_t oy = 0; oy < out_h; ++oy)
        for (uint32_t ox = 0; ox < out_w; ++ox) {
            const uint32_t row = oy * out_w + ox;
            for (uint32_t c = 0; c < channels; ++c)
                for (uint32_t ky = 0; ky < 3u; ++ky)
                    for (uint32_t kx = 0; kx < 3u; ++kx)
                        im2col[row * (channels * 9u) + c * 9u + ky * 3u + kx] =
                            planes[(c * height + oy + ky) * width + (ox + kx)];
        }
}

int main(void) {
    if (coh_reg(COH_CPU_ABI) != COH_CPU_ABI_VALUE) {
        aster_puts("CIFAR BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    copy_bytes(conv1_weights_b, cifar_conv1_weights_b, sizeof conv1_weights_b);
    copy_bytes(conv2_weights_b, cifar_conv2_weights_b, sizeof conv2_weights_b);
    copy_bytes(fc_weights, cifar_fc_weights, sizeof fc_weights);
    for (uint32_t i = 0; i < CIFAR_CONV1_OUT; ++i) conv1_bias_q[i] = cifar_conv1_bias_q[i];
    for (uint32_t i = 0; i < CIFAR_CONV2_OUT; ++i) conv2_bias_q[i] = cifar_conv2_bias_q[i];
    for (uint32_t i = 0; i < CIFAR_FC_OUT; ++i) fc_bias_q[i] = cifar_fc_bias_q[i];
    __asm__ volatile ("fence rw,rw" ::: "memory");

    *(volatile uint32_t *)COH_CONTROL = 1u;
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint32_t checksum = 0, mismatches = 0;
    for (uint32_t item = 0; item < CIFAR_TEST_COUNT; ++item) {
        copy_bytes(image, &cifar_test_images[item * 3u * CIFAR_H * CIFAR_H], sizeof image);
        __asm__ volatile ("fence rw,rw" ::: "memory");

        // conv1: M=C1_M, N=CONV1_OUT, K=CONV1_K
        build_im2col(image, 3u, CIFAR_H, CIFAR_H, C1_OH, C1_OH);
        struct aster_npu_gemm conv1_job = {im2col, conv1_weights_b, conv_acc,
                                           CIFAR_CONV1_K, CIFAR_CONV1_OUT, 4u * CIFAR_CONV1_OUT,
                                           C1_M, CIFAR_CONV1_OUT, CIFAR_CONV1_K};
        if (run_npu(&conv1_job)) { aster_puts("CIFAR CONV1 FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        for (uint32_t row = 0; row < C1_M; ++row)
            for (uint32_t n = 0; n < CIFAR_CONV1_OUT; ++n) {
                int8_t value = requant(conv_acc[row * CIFAR_CONV1_OUT + n], CIFAR_CONV1_MULT,
                                       CIFAR_CONV1_SHIFT, conv1_bias_q[n]);
                conv_q[n * C1_M + row] = value > 0 ? value : 0;
            }
        for (uint32_t n = 0; n < CIFAR_CONV1_OUT; ++n)
            for (uint32_t py = 0; py < 7u; ++py)
                for (uint32_t px = 0; px < 7u; ++px) {
                    int8_t best = -128;
                    for (uint32_t dy = 0; dy < 2u; ++dy)
                        for (uint32_t dx = 0; dx < 2u; ++dx) {
                            const int8_t v = conv_q[n * C1_M + (2u * py + dy) * C1_OH + (2u * px + dx)];
                            if (v > best) best = v;
                        }
                    pool1[n * 49u + py * 7u + px] = best;
                }
        __asm__ volatile ("fence rw,rw" ::: "memory");

        // conv2: M=C2_M, N=CONV2_OUT, K=CONV2_K over pool1
        build_im2col(pool1, CIFAR_CONV1_OUT, 7u, 7u, C2_OH, C2_OH);
        struct aster_npu_gemm conv2_job = {im2col, conv2_weights_b, conv_acc,
                                           CIFAR_CONV2_K, CIFAR_CONV2_OUT, 4u * CIFAR_CONV2_OUT,
                                           C2_M, CIFAR_CONV2_OUT, CIFAR_CONV2_K};
        if (run_npu(&conv2_job)) { aster_puts("CIFAR CONV2 FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
        for (uint32_t row = 0; row < C2_M; ++row)
            for (uint32_t n = 0; n < CIFAR_CONV2_OUT; ++n) {
                int8_t value = requant(conv_acc[row * CIFAR_CONV2_OUT + n], CIFAR_CONV2_MULT,
                                       CIFAR_CONV2_SHIFT, conv2_bias_q[n]);
                conv_q[n * C2_M + row] = value > 0 ? value : 0;
            }
        for (uint32_t n = 0; n < CIFAR_CONV2_OUT; ++n)
            for (uint32_t py = 0; py < 2u; ++py)
                for (uint32_t px = 0; px < 2u; ++px) {
                    int8_t best = -128;
                    for (uint32_t dy = 0; dy < 2u; ++dy)
                        for (uint32_t dx = 0; dx < 2u; ++dx) {
                            const int8_t v = conv_q[n * C2_M + (2u * py + dy) * C2_OH + (2u * px + dx)];
                            if (v > best) best = v;
                        }
                    pool2[n * 4u + py * 2u + px] = best;
                }
        __asm__ volatile ("fence rw,rw" ::: "memory");

        struct aster_npu_gemm fc_job = {fc_weights, pool2, fc_acc, CIFAR_FC_IN, 1u, 4u,
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
                            CIFAR_CONV1_OUT, 0u, checksum, mismatches == 0u ? 1u : 0u);
    for (;;) { }
}
