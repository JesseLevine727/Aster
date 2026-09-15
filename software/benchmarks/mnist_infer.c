// Phase 11 quantized INT8 MNIST inference across four execution paths.
//
// P11_METHOD selects scalar (0), multicore (1), dot8 (2) or npu (3). The CPU
// stages the frozen weights into shared RAM and owns staging, requantization,
// ReLU and argmax; only the matmul engine differs. Every path must match
// scripts/phase11_reference.py bit-for-bit.
#include "aster.h"
#include "aster_npu.h"
#include "aster_dot8.h"
#include "xe_kernels.h"
#include "phase11_model.h"
#include "phase11_images.h"
#include <stdatomic.h>

#ifndef P11_METHOD
#define P11_METHOD 3
#endif

#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define P11_PERF_CONTROL 0x20003080u
#define P11_CPU_ABI 0x20003084u
#define P11_NPU_ABI 0x40000008u
#define P11_RELEASE 0x20002004u

_Static_assert(P11_METHOD >= 0 && P11_METHOD <= 3, "unknown Phase 11 method");
_Static_assert(PHASE11_FC1_IN == 784u && PHASE11_FC1_OUT == 32u, "frozen fc1 geometry");
_Static_assert(PHASE11_FC2_IN == 32u && PHASE11_FC2_OUT == 10u, "frozen fc2 geometry");
_Static_assert(PHASE11_TEST_COUNT == 32u, "frozen test subset");

static int8_t w1[PHASE11_FC1_OUT * PHASE11_FC1_IN] __attribute__((section(".p11_w1"), aligned(64)));
static int8_t w2[PHASE11_FC2_OUT * PHASE11_FC2_IN] __attribute__((section(".p11_w2"), aligned(64)));
struct p11_io {
    int8_t input[PHASE11_FC1_IN];
    int32_t acc1[PHASE11_FC1_OUT];
    int8_t hidden[PHASE11_FC1_OUT];
    int32_t acc2[PHASE11_FC2_OUT];
    int8_t logits[PHASE11_FC2_OUT];
};
static struct p11_io io __attribute__((section(".p11_io"), aligned(64)));

static const char *const method_names[] = {"scalar", "multicore", "dot8", "npu"};
#if P11_METHOD == 1
static _Atomic uint32_t p11_epoch, p11_done;
static struct aster_npu_gemm p11_job;
static uint32_t p11_next_epoch;
#endif
#if P11_METHOD == 3
static struct aster_npu_status npu_status;
#endif

static void fence_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }

void aster_secondary_main(void) {
#if P11_METHOD == 1
    uint32_t last = 0;
    for (;;) {
        const uint32_t epoch = atomic_load_explicit(&p11_epoch, memory_order_acquire);
        if (epoch != 0 && epoch != last) {
            last = epoch;
            struct aster_npu_gemm job = p11_job;
            const uint32_t split = (job.m + 1u) / 2u;
            job.a = (const int8_t *)((const uint8_t *)job.a + (uint64_t)split * job.a_stride);
            job.c = (int32_t *)((uint8_t *)job.c + (uint64_t)split * job.c_stride);
            job.m = job.m - split;
            xe_scalar_gemm(&job);
            atomic_store_explicit(&p11_done, epoch, memory_order_release);
        }
    }
#else
    for (;;) __asm__ volatile ("" ::: "memory");
#endif
}

static void copy_bytes(int8_t *destination, const int8_t *source, uint32_t length) {
    for (uint32_t i = 0; i < length; ++i) destination[i] = source[i];
}

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

static void run_layer(const int8_t *a, const int8_t *b, int32_t *c,
                      uint32_t m, uint32_t n, uint32_t k) {
    struct aster_npu_gemm job = {a, b, c, k, n, 4u * n, m, n, k};
#if P11_METHOD == 3
    enum aster_npu_result result = aster_npu_submit(&job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, &npu_status);
    else aster_npu_poll(&npu_status);
    if (result != ASTER_NPU_OK) { aster_puts("MNIST INFER NPU FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
#elif P11_METHOD == 2
    xe_dot8_gemm(&job);
#elif P11_METHOD == 0
    xe_scalar_gemm(&job);
#else
    const uint32_t split = (m + 1u) / 2u;
    p11_job = job;  // full descriptor; the secondary splits it itself
    atomic_store_explicit(&p11_done, 0u, memory_order_relaxed);
    atomic_store_explicit(&p11_epoch, ++p11_next_epoch, memory_order_release);
    fence_io();
    struct aster_npu_gemm local = job;
    local.m = split;
    xe_scalar_gemm(&local);
    while (atomic_load_explicit(&p11_done, memory_order_acquire) != p11_next_epoch) {}
    fence_io();
#endif
}

static uint64_t read_counter(uint32_t address) {
    volatile uint32_t *low = (volatile uint32_t *)(uintptr_t)address;
    volatile uint32_t *high = low + 1;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return ((uint64_t)last << 32) | value;
}

static void decimal(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static void hex64(const char *key, uint64_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x");
    aster_put_hex32((uint32_t)(value >> 32)); aster_put_hex32((uint32_t)value);
}
static void logits_hex(const int8_t *logits) {
    static const char digits[] = "0123456789abcdef";
    aster_puts(",logits=");
    for (uint32_t i = 0; i < PHASE11_FC2_OUT; ++i) {
        const uint8_t byte = (uint8_t)logits[i];
        aster_putc(digits[byte >> 4]);
        aster_putc(digits[byte & 0xfu]);
    }
}

int main(void) {
    if (REG(P11_CPU_ABI) != 4u || REG(P11_NPU_ABI) != ASTER_NPU_ABI ||
        REG(0x4000000cu) != ASTER_NPU_COUNTER_ABI) {
        aster_puts("MNIST INFER BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
#if P11_METHOD == 1
    REG(P11_RELEASE) = 1u;  // release the secondary worker once
    fence_io();
#endif
    copy_bytes(w1, phase11_fc1_weights, sizeof w1);
    copy_bytes(w2, phase11_fc2_weights, sizeof w2);
    fence_io();

    uint32_t class_correct = 0, class_mismatch = 0, logit_mismatch = 0;
    for (uint32_t image = 0; image < PHASE11_TEST_COUNT; ++image) {
        copy_bytes(io.input, &phase11_test_images[image * PHASE11_FC1_IN], PHASE11_FC1_IN);
        fence_io();
        REG(P11_PERF_CONTROL) = 1u;
        fence_io();

        run_layer(w1, io.input, io.acc1, PHASE11_FC1_OUT, 1u, PHASE11_FC1_IN);
        for (uint32_t o = 0; o < PHASE11_FC1_OUT; ++o) {
            int8_t value = requant(io.acc1[o], PHASE11_FC1_MULT, PHASE11_FC1_SHIFT, phase11_fc1_bias_q[o]);
            io.hidden[o] = value > 0 ? value : 0;
        }
        run_layer(w2, io.hidden, io.acc2, PHASE11_FC2_OUT, 1u, PHASE11_FC2_IN);
        int best = 0;
        for (uint32_t o = 0; o < PHASE11_FC2_OUT; ++o) {
            io.logits[o] = requant(io.acc2[o], PHASE11_FC2_MULT, PHASE11_FC2_SHIFT, phase11_fc2_bias_q[o]);
            if (io.logits[o] > io.logits[best]) best = (int)o;
        }
        fence_io();
        REG(P11_PERF_CONTROL) = 2u;
        fence_io();

        const uint32_t expected = phase11_test_classes[image];
        const uint32_t label = phase11_test_labels[image];
        uint32_t logit_ok = 1;
        for (uint32_t o = 0; o < PHASE11_FC2_OUT; ++o)
            if (io.logits[o] != phase11_test_logits[image * PHASE11_FC2_OUT + o]) logit_ok = 0;
        if ((uint32_t)best == expected) ++class_correct; else ++class_mismatch;
        if (!logit_ok) ++logit_mismatch;

        aster_puts("ASTERBENCH,version=9,name=mnist_mlp,method=");
        aster_puts(method_names[P11_METHOD]);
        aster_puts(",status="); aster_puts(logit_ok ? "PASS" : "FAIL");
        aster_puts(",model="); aster_puts(PHASE11_MODEL_HASH);
        decimal("image", image); decimal("label", label); decimal("class", (uint32_t)best);
        decimal("expected", expected); decimal("logit_match", logit_ok);
        logits_hex(io.logits);
        hex64("h0_cycles", read_counter(0x20003000u));
        hex64("h0_retired", read_counter(0x20003010u));
        hex64("h1_cycles", read_counter(0x20003100u));
        hex64("h1_retired", read_counter(0x20003110u));
#if P11_METHOD == 3
        hex64("npu_job_cycles", npu_status.job_cycles);
        hex64("npu_compute_cycles", npu_status.compute_cycles);
        decimal("npu_tiles", npu_status.tiles);
        decimal("npu_bytes_read", npu_status.bytes_read);
        decimal("npu_bytes_written", npu_status.bytes_written);
#else
        hex64("npu_job_cycles", 0u); hex64("npu_compute_cycles", 0u);
        decimal("npu_tiles", 0u); decimal("npu_bytes_read", 0u); decimal("npu_bytes_written", 0u);
#endif
        decimal("clock_hz", REG(0x20003088u));
        decimal("l1", REG(0x2000308cu) & 1u);
        decimal("sync_memory", (REG(0x2000308cu) >> 1) & 1u);
        aster_putc('\n');
    }

    aster_puts("MNIST INFER ");
    aster_puts((class_mismatch || logit_mismatch) ? "FAIL" : "PASS");
    aster_puts(" images="); aster_put_u32(PHASE11_TEST_COUNT);
    aster_puts(" class_correct="); aster_put_u32(class_correct);
    aster_puts(" class_mismatch="); aster_put_u32(class_mismatch);
    aster_puts(" logit_mismatch="); aster_put_u32(logit_mismatch);
    aster_putc('\n');
    if (class_mismatch || logit_mismatch) { __asm__ volatile ("ebreak"); for (;;) {} }
    return 0;
}
