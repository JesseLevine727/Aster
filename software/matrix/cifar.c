// 20.4's matrix: the CIFAR-10 CNN (16x16x3; conv 3x3 to 16, ReLU, 2x2 max pool; conv 3x3 to 32, ReLU, pool; fc
// 128 -> 10; the frozen model and its 20 test images; docs/matrix.md §4.7; a v1-retained workload), on
// AsterBench v12. CIFAR_METHOD:
//   0 scalar      im2col and v1's xe_scalar_gemm for each convolution and the fc layer
//   1 two workers the same, each layer's rows split: hart 1 builds and computes the upper rows, hart 0 the rest
//   2 DOT8        im2col and v1's xe_dot8_gemm
//   3 NPU im2col  v1's (software/benchmarks/workload_cifar.c): im2col, each layer an NPU job
//   4 NPU direct  19.3's two-level A addressing on channels-last activations (docs/npu.md, the 19.3
//                 clarifications): the images and convolution weights stored channels last at build time
//                 (software/matrix/cifar_hwc.h, scripts/gen_cifar_hwc.py), pool1 written channels last
// Two windows (matrix.md §4):
//   e2e     v1's: one interval over the 20 images with their staging (each image copied into its buffer),
//           im2col, the three layers, requantization, ReLU, pooling and the class.
//   kernel  an interval per layer (60): the engine alone, its input in place (staged, im2col built, the NPU's
//           descriptor written); two workers' shares released inside it (matrix_window.h).
// The weights are read where they are (v1 copied them out of a ROM the NPU could not read). A cold run
// (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is, for each window, an untimed
// pass of the same code, then the window. Hart 1 stamps its work interval; hart 0's follows matrix_window.h.
// Each timed pass's outputs (every image's logits and class) are
// poisoned before it, by hart 0, which alone writes them; the intermediates are rewritten for every image.
// Each record's checksum folds every logit and class (v1's), which scripts/matrix.py's oracle recomputes with
// cifar_reference's independent model; PASS needs every logit and class equal to the frozen reference's.
#include <stdint.h>

#include "aster.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "cifar_images.h"
#include "cifar_model.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "xe_kernels.h"

#ifndef CIFAR_METHOD
#define CIFAR_METHOD 3
#endif
#define NPU (CIFAR_METHOD >= 3)
#define DIRECT (CIFAR_METHOD == 4)
#define WORKERS (CIFAR_METHOD == 1 ? 2u : 1u)
#if DIRECT
#include "cifar_hwc.h"
#endif

#define IMAGES CIFAR_TEST_COUNT
#define IMAGE_BYTES (3u * CIFAR_H * CIFAR_H)
#define C1_OH 14u
#define C2_OH 5u
#define C1_M (C1_OH * C1_OH)
#define C2_M (C2_OH * C2_OH)
#define POOL1 (CIFAR_CONV1_OUT * 7u * 7u)
#define POOL2 (CIFAR_CONV2_OUT * 2u * 2u)

enum { UNTIMED, E2E, KERNEL };

static int8_t image[IMAGE_BYTES] __attribute__((aligned(16)));
#if !DIRECT
static int8_t im2col[C1_M * CIFAR_CONV1_K] __attribute__((aligned(16)));
#endif
static int32_t conv_acc[C1_M * CIFAR_CONV1_OUT] __attribute__((aligned(16)));
static int8_t conv_q[CIFAR_CONV2_OUT * C1_M];
static int8_t pool1[POOL1] __attribute__((aligned(16)));
static int8_t pool2[POOL2] __attribute__((aligned(16)));
static int32_t fc_acc[CIFAR_FC_OUT] __attribute__((aligned(16)));
static int8_t logits[IMAGES][CIFAR_FC_OUT];
static int32_t classes[IMAGES];
static volatile uint32_t engine_failed;
static uint32_t window_first;                          // the kernel window's next interval opens it (START)

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

#if !DIRECT
// v1's im2col over C planes of H x W, for output rows [row0, row1) of an out_w-wide output
// (the computation's functions out of line, noinline, so that their code is the same in every caller and build)
static __attribute__((noinline)) void build_im2col(const int8_t *planes, uint32_t channels, uint32_t height,
                                                   uint32_t width, uint32_t out_w, uint32_t row0, uint32_t row1) {
    for (uint32_t row = row0; row < row1; ++row) {
        const uint32_t oy = row / out_w, ox = row % out_w;
        for (uint32_t c = 0; c < channels; ++c)
            for (uint32_t ky = 0; ky < 3u; ++ky)
                for (uint32_t kx = 0; kx < 3u; ++kx)
                    im2col[row * (channels * 9u) + c * 9u + ky * 3u + kx] =
                        planes[(c * height + oy + ky) * width + (ox + kx)];
    }
}

// a layer's im2col rows [row0, row1): 1 conv1's, 2 conv2's, 0 none
static __attribute__((noinline)) void build_layer_rows(int layer, uint32_t row0, uint32_t row1) {
    if (layer == 1) build_im2col(image, 3u, CIFAR_H, CIFAR_H, C1_OH, row0, row1);
    else if (layer == 2) build_im2col(pool1, CIFAR_CONV1_OUT, 7u, 7u, C2_OH, row0, row1);
}
#endif

#if NPU
static void npu_outcome(uint32_t status) {             // a job's outcome noted, and acknowledged
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
}
#endif

#if WORKERS == 2
static struct aster_npu_gemm shared_job;               // the layer in hand (hart 1 takes its upper rows)

static void upper_rows(void *layer) {                  // (layer: its im2col rows built too; 0 in the kernel window)
    matrix_stamp_start(1);
    const uint32_t split = (shared_job.m + 1u) / 2u;
    build_layer_rows((int)(uintptr_t)layer, split, shared_job.m);
    struct aster_npu_gemm job = shared_job;
    job.a += split * job.a_stride;
    job.c = (int32_t *)((uint8_t *)job.c + split * job.c_stride);
    job.m -= split;
    xe_scalar_gemm(&job);
    matrix_stamp_end(1);
}
#endif

// one layer, C = A B, on the method's engine (layer: whose im2col rows the e2e window builds with it, 1 or 2;
// 0 for fc). In the kernel window, an interval of its own: the im2col matrix built and the descriptor written
// before it, two workers' shares released inside it.
static __attribute__((noinline)) void run_layer(const struct aster_npu2_job *job, int layer, int mode) {
#if NPU
#if DIRECT
    (void)layer;
#else
    build_layer_rows(layer, 0, job->m);                // (v1's: im2col, then the job)
#endif
    if (mode == KERNEL) {
        aster_npu2_describe(job);
        matrix_open((int)window_first);
        window_first = 0;
        aster_npu2_go();
        const uint32_t status = aster_npu2_wait();
        matrix_close();
        npu_outcome(status);
        return;
    }
    aster_npu2_start(job);
    npu_outcome(aster_npu2_wait());
#else
    struct aster_npu_gemm gemm = {(const int8_t *)job->a, (const int8_t *)job->b, (int32_t *)job->c, job->a_stride,
                                  job->b_stride, job->c_stride, job->m, job->n, job->k};
#if WORKERS == 2
    const uint32_t split = (gemm.m + 1u) / 2u;
    shared_job = gemm;
    struct aster_npu_gemm lower = gemm;
    lower.m = split;
    if (mode == KERNEL) {
        build_layer_rows(layer, 0, gemm.m);
        matrix_arm(upper_rows, 0);
        matrix_open((int)window_first);
        window_first = 0;
        matrix_release();
        xe_scalar_gemm(&lower);
        matrix_stamp_end(0);                           // (the window's last share ends; hart 0 waits)
        matrix_await();
        matrix_close();
        aster_smp_join();
    } else {
        __asm__ volatile ("fence rw,rw" ::: "memory");
        aster_smp_dispatch(upper_rows, (void *)(uintptr_t)layer);
        build_layer_rows(layer, 0, split);
        xe_scalar_gemm(&lower);
        aster_smp_join();
    }
#else
    build_layer_rows(layer, 0, gemm.m);
    if (mode == KERNEL) {
        matrix_open((int)window_first);
        window_first = 0;
    }
#if CIFAR_METHOD == 2
    xe_dot8_gemm(&gemm);
#else
    xe_scalar_gemm(&gemm);
#endif
    if (mode == KERNEL) matrix_close();
#endif
#endif
}

static uint32_t checksum, mismatches;                 // v1's, folded in its window as each image ends

static __attribute__((noinline)) void infer(uint32_t item, int mode) {
#if DIRECT
    const int8_t *source = &cifar_test_images_hwc[item * IMAGE_BYTES];
#else
    const int8_t *source = &cifar_test_images[item * IMAGE_BYTES];
#endif
    for (uint32_t i = 0; i < IMAGE_BYTES; ++i) image[i] = source[i];      // the staging (v1's, in its window)
    __asm__ volatile ("fence rw,rw" ::: "memory");

    // conv1: M = 196 output pixels, N = 16, K = 27
#if DIRECT
    // A(i, k) = image + (i div 14) 48 + (i mod 14) 3 + (k div 9) 48 + (k mod 9): a kernel row is 3 pixels x 3
    // channels, contiguous channels last
    const struct aster_npu2_job conv1 = {image, cifar_conv1_weights_hwc, conv_acc, 3u, CIFAR_CONV1_OUT,
                                         4u * CIFAR_CONV1_OUT, C1_M, CIFAR_CONV1_OUT, CIFAR_CONV1_K, 0,
                                         C1_OH, 3u * CIFAR_H, 9u, 3u * CIFAR_H};
#else
    const struct aster_npu2_job conv1 = {im2col, cifar_conv1_weights_b, conv_acc, CIFAR_CONV1_K, CIFAR_CONV1_OUT,
                                         4u * CIFAR_CONV1_OUT, C1_M, CIFAR_CONV1_OUT, CIFAR_CONV1_K, 0, 0, 0, 0, 0};
#endif
    matrix_last = 0;
    run_layer(&conv1, 1, mode);
    for (uint32_t row = 0; row < C1_M; ++row)
        for (uint32_t n = 0; n < CIFAR_CONV1_OUT; ++n) {
            const int8_t value = requant(conv_acc[row * CIFAR_CONV1_OUT + n], CIFAR_CONV1_MULT, CIFAR_CONV1_SHIFT,
                                         cifar_conv1_bias_q[n]);
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
#if DIRECT
                pool1[(py * 7u + px) * CIFAR_CONV1_OUT + n] = best;                    // channels last
#else
                pool1[n * 49u + py * 7u + px] = best;
#endif
            }
    __asm__ volatile ("fence rw,rw" ::: "memory");

    // conv2: M = 25, N = 32, K = 144 over pool1
#if DIRECT
    // A(i, k) = pool1 + (i div 5) 112 + (i mod 5) 16 + (k div 48) 112 + (k mod 48): 3 pixels x 16 channels a row
    const struct aster_npu2_job conv2 = {pool1, cifar_conv2_weights_hwc, conv_acc, CIFAR_CONV1_OUT, CIFAR_CONV2_OUT,
                                         4u * CIFAR_CONV2_OUT, C2_M, CIFAR_CONV2_OUT, CIFAR_CONV2_K, 0,
                                         C2_OH, 7u * CIFAR_CONV1_OUT, 3u * CIFAR_CONV1_OUT, 7u * CIFAR_CONV1_OUT};
#else
    const struct aster_npu2_job conv2 = {im2col, cifar_conv2_weights_b, conv_acc, CIFAR_CONV2_K, CIFAR_CONV2_OUT,
                                         4u * CIFAR_CONV2_OUT, C2_M, CIFAR_CONV2_OUT, CIFAR_CONV2_K, 0, 0, 0, 0, 0};
#endif
    run_layer(&conv2, 2, mode);
    for (uint32_t row = 0; row < C2_M; ++row)
        for (uint32_t n = 0; n < CIFAR_CONV2_OUT; ++n) {
            const int8_t value = requant(conv_acc[row * CIFAR_CONV2_OUT + n], CIFAR_CONV2_MULT, CIFAR_CONV2_SHIFT,
                                         cifar_conv2_bias_q[n]);
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

    // fc: M = 10, N = 1, K = 128
    const struct aster_npu2_job fc = {cifar_fc_weights, pool2, fc_acc, CIFAR_FC_IN, 1u, 4u, CIFAR_FC_OUT, 1u,
                                      CIFAR_FC_IN, 0, 0, 0, 0, 0};
    matrix_last = item == IMAGES - 1u;                 // (the window's last stretch: the end stamps)
    run_layer(&fc, 0, mode);
    int best = 0;
    for (uint32_t o = 0; o < CIFAR_FC_OUT; ++o) {
        logits[item][o] = requant(fc_acc[o], CIFAR_FC_MULT, CIFAR_FC_SHIFT, cifar_fc_bias_q[o]);
        if (logits[item][o] > logits[item][best]) best = (int)o;
    }
    classes[item] = best;
    if ((uint32_t)best != cifar_test_classes[item]) ++mismatches;
    for (uint32_t o = 0; o < CIFAR_FC_OUT; ++o) checksum = (checksum * 33u) ^ (uint32_t)(uint8_t)logits[item][o];
    checksum = (checksum * 33u) ^ (uint32_t)best;
}

static void pass(int mode) {
    window_first = 1;
    checksum = 0;
    mismatches = 0;
    if (mode == E2E) matrix_open(1);
    for (uint32_t item = 0; item < IMAGES; ++item) infer(item, mode);
    if (mode == E2E) matrix_close();
}

static int timed_pass(struct v12_record *record, int mode) {
    matrix_poison(logits, sizeof logits);
    matrix_poison(classes, sizeof classes);
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
    if (mode == E2E) matrix_hart0_whole(record);       // (v1's window ends in hart 0's class)
#if NPU
    else matrix_hart0_none(record);                    // (hart 0 only starts each job and polls it)
#elif WORKERS == 1
    else matrix_hart0_whole(record);                   // (each interval is hart 0's engine)
#endif
    uint32_t logits_ok = 1;                            // (the checksum is the pass's, folded in its window)
    for (uint32_t item = 0; item < IMAGES; ++item)
        for (uint32_t o = 0; o < CIFAR_FC_OUT; ++o)
            if (logits[item][o] != cifar_test_logits[item * CIFAR_FC_OUT + o]) logits_ok = 0;
    static const char *const names[] = {"cifar_cnn_scalar", "cifar_cnn_multicore", "cifar_cnn_dot8",
                                        "cifar_cnn_npu_im2col", "cifar_cnn_npu_direct"};
    static const char *const methods[] = {"scalar", "multicore", "dot8", "npu_im2col", "npu_direct"};
    record->name = names[CIFAR_METHOD]; record->family = "ml"; record->method = methods[CIFAR_METHOD];
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = IMAGE_BYTES; record->iterations = IMAGES; record->param = CIFAR_CONV1_OUT; record->seed = 0;
    record->checksum = checksum; record->workers = WORKERS;
    record->pass = logits_ok && mismatches == 0 && !engine_failed;     // (v1's: every class the reference's)
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
#if WORKERS == 2
    aster_smp_start();
#endif
    if (!matrix_cold) pass(E2E);                       // the e2e window's warm-up (the same code)
    engine_failed = 0;
    int failed = timed_pass(&record, E2E);
    if (!matrix_cold) {
        pass(KERNEL);                                  // the kernel window's warm-up
        failed |= timed_pass(&record, KERNEL);
    }
    return failed;
}
