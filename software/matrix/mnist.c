// 20.4's matrix: the MNIST MLP (784 -> 32 -> 10, the frozen Phase 11 model and its 32 test images; docs/matrix.md
// §4.7; a v1-retained workload), on AsterBench v12. MNIST_METHOD:
//   0 scalar (v1's xe_scalar_gemm)    1 two workers (v1's split of each layer's rows, its epoch handshake)
//   2 DOT8 (v1's xe_dot8_gemm)        3 the NPU, an image at a time (N = 1: the K-split mapping)
//   4 the NPU, MNIST_BATCH images a job: fc1 as images x W1^T, fc2 as hidden x W2^T, the images read in place;
//     the weights stored transposed at build time (software/matrix/phase11_weights_t.h, from
//     scripts/gen_mnist_transposed.py; 19.3's choice for CIFAR's), in place of the original layout, which would
//     not fit beside them. Both layers batched (matrix.md §4.7 names fc1's batching; fc2 follows it).
// Two windows (matrix.md §4), each the sum of intervals (START, then RESUME; FREEZE after each):
//   e2e     v1's (software/benchmarks/mnist_infer.c): an interval per image (per batch for method 4), from the
//           image in RAM through fc1, requantization and ReLU, fc2, requantization and the class; the image is
//           copied into the input buffer before its interval, as v1 did (method 4 reads the images in place).
//   kernel  an interval per layer: the engine alone, its input in place and the NPU's descriptor written
//           (requantization, ReLU and the class outside).
// The weights are read where they are: v1 copied fc1 into RAM only because its ROM was not the NPU's to read;
// v2's memory is one. A cold run (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is,
// for each window, an untimed pass of the same code, then the window; the outputs poisoned before each.
// Each record's checksum folds every logit and class (v1's), which scripts/matrix.py's oracle recomputes with
// phase11_reference's independent model; PASS needs every logit equal to the frozen reference's.
#include <stdint.h>
#include <stdatomic.h>

#include "aster.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "phase11_images.h"
#include "phase11_model.h"
#include "xe_kernels.h"

#ifndef MNIST_METHOD
#define MNIST_METHOD 3
#endif
#ifndef MNIST_BATCH
#define MNIST_BATCH 1u
#endif
#define NPU (MNIST_METHOD >= 3)
#define IMAGES PHASE11_TEST_COUNT
#define FC1_IN PHASE11_FC1_IN
#define FC1_OUT PHASE11_FC1_OUT
#define FC2_IN PHASE11_FC2_IN
#define FC2_OUT PHASE11_FC2_OUT
_Static_assert(MNIST_METHOD != 4 || (IMAGES % MNIST_BATCH) == 0, "a whole number of batches");

enum { UNTIMED, E2E, KERNEL };

static volatile uint32_t engine_failed;
static int8_t logits[IMAGES][FC2_OUT];
static int32_t classes[IMAGES];
static uint32_t window_first;                          // the next interval opens the window (START)

// (hart 0's work interval: matrix_window.h)
static void interval_open(void) {
    matrix_open((int)window_first);
    window_first = 0;
}

static void interval_close(void) { matrix_close(); }

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

#if NPU
static void npu_outcome(uint32_t status) {             // a job's outcome noted, and acknowledged
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
}
#endif

#if MNIST_METHOD == 1
// v1's: hart 1 computes the upper rows of each layer, for each new epoch
static _Atomic uint32_t epoch, done;
static struct aster_npu_gemm shared_job;
static uint32_t next_epoch;

void aster_secondary_main(void) {
    uint32_t last = 0;
    for (;;) {
        const uint32_t e = atomic_load_explicit(&epoch, memory_order_acquire);
        if (e != 0 && e != last) {
            last = e;
            matrix_stamp_start(1);
            struct aster_npu_gemm job = shared_job;
            const uint32_t split = (job.m + 1u) / 2u;
            job.a = (const int8_t *)((const uint8_t *)job.a + (uint64_t)split * job.a_stride);
            job.c = (int32_t *)((uint8_t *)job.c + (uint64_t)split * job.c_stride);
            job.m = job.m - split;
            xe_scalar_gemm(&job);
            matrix_stamp_end(1);
            atomic_store_explicit(&done, e, memory_order_release);
        }
    }
}
#endif

// a layer, C = A B: on the NPU, or the CPU's method; in the kernel window, an interval of its own
static void layer(const struct aster_npu2_job *job, int mode) {
#if NPU
    if (mode == KERNEL) {                              // (from START to the job's end; its outcome after)
        aster_npu2_describe(job);
        interval_open();
        aster_npu2_go();
        const uint32_t status = aster_npu2_wait();
        interval_close();
        npu_outcome(status);
        return;
    }
    aster_npu2_start(job);
    npu_outcome(aster_npu2_wait());
#else
    if (mode == KERNEL) interval_open();
    struct aster_npu_gemm gemm = {(const int8_t *)job->a, (const int8_t *)job->b, (int32_t *)job->c, job->a_stride,
                                  job->b_stride, job->c_stride, job->m, job->n, job->k};
#if MNIST_METHOD == 2
    xe_dot8_gemm(&gemm);
#elif MNIST_METHOD == 0
    xe_scalar_gemm(&gemm);
#else
    const uint32_t split = (gemm.m + 1u) / 2u;
    shared_job = gemm;
    atomic_store_explicit(&done, 0u, memory_order_relaxed);
    atomic_store_explicit(&epoch, ++next_epoch, memory_order_release);
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
    struct aster_npu_gemm local = gemm;
    local.m = split;
    xe_scalar_gemm(&local);
    if (mode == KERNEL) matrix_stamp_end(0);           // (the kernel window's last share ends; hart 0 waits)
    while (atomic_load_explicit(&done, memory_order_acquire) != next_epoch) {}
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
#endif
#endif
    if (mode == KERNEL) interval_close();
}

#if MNIST_METHOD != 4
// ---- an image at a time (v1's window) ----
static const int8_t *const w1 = phase11_fc1_weights;
static const int8_t *const w2 = phase11_fc2_weights;
struct io {
    int8_t input[FC1_IN];
    int32_t acc1[FC1_OUT];
    int8_t hidden[FC1_OUT];
    int32_t acc2[FC2_OUT];
} __attribute__((aligned(64)));
static struct io io __attribute__((aligned(64)));

static void infer(uint32_t image, int mode) {
    for (uint32_t i = 0; i < FC1_IN; ++i) io.input[i] = phase11_test_images[image * FC1_IN + i];
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
    if (mode == E2E) interval_open();
    const struct aster_npu2_job fc1 = {w1, io.input, io.acc1, FC1_IN, 1u, 4u, FC1_OUT, 1u, FC1_IN, 0, 0, 0, 0, 0};
    matrix_last = 0;
    layer(&fc1, mode);
    for (uint32_t o = 0; o < FC1_OUT; ++o) {
        const int8_t value = requant(io.acc1[o], PHASE11_FC1_MULT, PHASE11_FC1_SHIFT, phase11_fc1_bias_q[o]);
        io.hidden[o] = value > 0 ? value : 0;
    }
    const struct aster_npu2_job fc2 = {w2, io.hidden, io.acc2, FC2_IN, 1u, 4u, FC2_OUT, 1u, FC2_IN, 0, 0, 0, 0, 0};
    matrix_last = image == IMAGES - 1u;                // (the window's last stretch: hart 1's end stamp)
    layer(&fc2, mode);
    int best = 0;
    for (uint32_t o = 0; o < FC2_OUT; ++o) {
        logits[image][o] = requant(io.acc2[o], PHASE11_FC2_MULT, PHASE11_FC2_SHIFT, phase11_fc2_bias_q[o]);
        if (logits[image][o] > logits[image][best]) best = (int)o;
    }
    classes[image] = best;
    if (mode == E2E) interval_close();
}

static void pass(int mode) {
    window_first = 1;
    for (uint32_t image = 0; image < IMAGES; ++image) infer(image, mode);
}

static void poison_intermediates(void) { matrix_poison(&io, sizeof io); }
#else
// ---- the NPU, MNIST_BATCH images a job ----
#include "phase11_weights_t.h"
static int32_t acc1[MNIST_BATCH * FC1_OUT] __attribute__((aligned(64)));
static int8_t hidden[MNIST_BATCH * FC1_OUT] __attribute__((aligned(16)));
static int32_t acc2[MNIST_BATCH * FC2_OUT] __attribute__((aligned(16)));

static void infer_batch(uint32_t first, int mode) {
    if (mode == E2E) interval_open();
    // fc1: (batch x 784) images in place x W1^T (784 x 32)
    const struct aster_npu2_job fc1 = {&phase11_test_images[first * FC1_IN], phase11_fc1_weights_t, acc1, FC1_IN,
                                       FC1_OUT, 4u * FC1_OUT, MNIST_BATCH, FC1_OUT, FC1_IN, 0, 0, 0, 0, 0};
    layer(&fc1, mode);
    for (uint32_t i = 0; i < MNIST_BATCH; ++i)
        for (uint32_t o = 0; o < FC1_OUT; ++o) {
            const int8_t value = requant(acc1[i * FC1_OUT + o], PHASE11_FC1_MULT, PHASE11_FC1_SHIFT,
                                         phase11_fc1_bias_q[o]);
            hidden[i * FC1_OUT + o] = value > 0 ? value : 0;
        }
    const struct aster_npu2_job fc2 = {hidden, phase11_fc2_weights_t, acc2, FC2_IN, FC2_OUT, 4u * FC2_OUT, MNIST_BATCH,
                                       FC2_OUT, FC2_IN, 0, 0, 0, 0, 0};
    layer(&fc2, mode);
    for (uint32_t i = 0; i < MNIST_BATCH; ++i) {
        int best = 0;
        for (uint32_t o = 0; o < FC2_OUT; ++o) {
            logits[first + i][o] = requant(acc2[i * FC2_OUT + o], PHASE11_FC2_MULT, PHASE11_FC2_SHIFT,
                                           phase11_fc2_bias_q[o]);
            if (logits[first + i][o] > logits[first + i][best]) best = (int)o;
        }
        classes[first + i] = best;
    }
    if (mode == E2E) interval_close();
}

static void pass(int mode) {
    window_first = 1;
    for (uint32_t first = 0; first < IMAGES; first += MNIST_BATCH) infer_batch(first, mode);
}

static void poison_intermediates(void) {
    matrix_poison(acc1, sizeof acc1);
    matrix_poison(hidden, sizeof hidden);
    matrix_poison(acc2, sizeof acc2);
}
#endif

static void timed_pass(struct v12_record *record, int mode) {
    matrix_poison(logits, sizeof logits);
    matrix_poison(classes, sizeof classes);
    poison_intermediates();
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
    if (mode == E2E) matrix_hart0_whole(record);       // (v1's window ends in hart 0's class)
#if NPU
    else matrix_hart0_none(record);                    // (hart 0 only starts each job and polls it)
#elif MNIST_METHOD != 1
    else matrix_hart0_whole(record);                   // (each interval is hart 0's engine)
#endif
    uint32_t checksum = 0, logits_ok = 1;
    for (uint32_t image = 0; image < IMAGES; ++image) {
        for (uint32_t o = 0; o < FC2_OUT; ++o) {
            checksum = (checksum * 33u) ^ (uint8_t)logits[image][o];
            if (logits[image][o] != phase11_test_logits[image * FC2_OUT + o]) logits_ok = 0;
        }
        checksum = (checksum * 33u) ^ (uint32_t)classes[image];
    }
    static const char *const names[] = {"mnist_mlp_scalar", "mnist_mlp_multicore", "mnist_mlp_dot8", "mnist_mlp_npu",
                                        "mnist_mlp_npu_batched"};
    static const char *const methods[] = {"scalar", "multicore", "dot8", "npu", "npu"};
    record->name = names[MNIST_METHOD]; record->family = "ml"; record->method = methods[MNIST_METHOD];
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = FC1_IN; record->iterations = IMAGES; record->param = MNIST_BATCH; record->seed = 0;
    record->checksum = checksum; record->workers = MNIST_METHOD == 1 ? 2u : 1u;
    record->pass = logits_ok && !engine_failed;
    v12_emit(record);
}

int main(void) {
    static struct v12_record record;
#if MNIST_METHOD == 1
    *(volatile uint32_t *)0x20002004u = 1u;            // v1's: the secondary released once, before any window
#endif
    if (!matrix_cold) pass(E2E);                       // the e2e window's warm-up (the same code)
    engine_failed = 0;
    timed_pass(&record, E2E);
    int failed = !record.pass;
    if (!matrix_cold) {
        pass(KERNEL);                                  // the kernel window's warm-up
        timed_pass(&record, KERNEL);
        failed |= !record.pass;
    }
    return failed;
}
