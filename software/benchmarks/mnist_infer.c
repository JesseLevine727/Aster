// Phase 11 quantized INT8 MNIST inference through the 4x4 NPU.
//
// The CPU stages the frozen weights into shared RAM, drives one NPU GEMM per
// fully-connected layer, requantizes and ReLUs on the CPU, and emits an
// AsterBench v9 record per retained image. The integer arithmetic must match
// scripts/phase11_reference.py exactly.
#include "aster.h"
#include "aster_npu.h"
#include "phase11_model.h"
#include "phase11_images.h"

#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define P11_PERF_CONTROL 0x20003080u
#define P11_CPU_ABI 0x20003084u
#define P11_NPU_ABI 0x40000008u

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

static void fence_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }

void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }

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

static int run_npu_layer(const int8_t *a, const int8_t *b, int32_t *c,
                         uint32_t m, uint32_t n, uint32_t k,
                         struct aster_npu_status *status) {
    struct aster_npu_gemm job = {a, b, c, k, n, 4u * n, m, n, k};
    enum aster_npu_result result = aster_npu_submit(&job);
    if (result == ASTER_NPU_PENDING) result = aster_npu_wait(8000000u, status);
    else aster_npu_poll(status);
    return result == ASTER_NPU_OK ? 0 : -1;
}

int main(void) {
    if (REG(P11_CPU_ABI) != 4u || REG(P11_NPU_ABI) != ASTER_NPU_ABI ||
        REG(0x4000000cu) != ASTER_NPU_COUNTER_ABI) {
        aster_puts("MNIST INFER BAD ABI\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    copy_bytes(w1, phase11_fc1_weights, sizeof w1);
    copy_bytes(w2, phase11_fc2_weights, sizeof w2);
    fence_io();

    uint32_t class_correct = 0, class_mismatch = 0, logit_mismatch = 0;
    for (uint32_t image = 0; image < PHASE11_TEST_COUNT; ++image) {
        copy_bytes(io.input, &phase11_test_images[image * PHASE11_FC1_IN], PHASE11_FC1_IN);
        fence_io();
        REG(P11_PERF_CONTROL) = 1u;
        fence_io();

        struct aster_npu_status status1, status2;
        if (run_npu_layer(w1, io.input, io.acc1, PHASE11_FC1_OUT, 1u, PHASE11_FC1_IN, &status1)) {
            aster_puts("MNIST INFER L1 FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
        for (uint32_t o = 0; o < PHASE11_FC1_OUT; ++o) {
            int8_t value = requant(io.acc1[o], PHASE11_FC1_MULT, PHASE11_FC1_SHIFT, phase11_fc1_bias_q[o]);
            io.hidden[o] = value > 0 ? value : 0;
        }
        if (run_npu_layer(w2, io.hidden, io.acc2, PHASE11_FC2_OUT, 1u, PHASE11_FC2_IN, &status2)) {
            aster_puts("MNIST INFER L2 FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
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

        aster_puts("ASTERBENCH,version=9,name=mnist_mlp,method=npu,status=");
        aster_puts(logit_ok ? "PASS" : "FAIL");
        aster_puts(",model="); aster_puts(PHASE11_MODEL_HASH);
        decimal("image", image); decimal("label", label); decimal("class", (uint32_t)best);
        decimal("expected", expected); decimal("logit_match", logit_ok);
        logits_hex(io.logits);
        hex64("h0_cycles", read_counter(0x20003000u));
        hex64("h0_retired", read_counter(0x20003010u));
        hex64("npu_job_cycles", status1.job_cycles + status2.job_cycles);
        hex64("npu_compute_cycles", status1.compute_cycles + status2.compute_cycles);
        decimal("npu_tiles", status1.tiles + status2.tiles);
        decimal("npu_bytes_read", status1.bytes_read + status2.bytes_read);
        decimal("npu_bytes_written", status1.bytes_written + status2.bytes_written);
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
