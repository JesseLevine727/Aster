// Milestone 19.4's MLP gate (docs/npu.md §7): the batch-one MNIST MLP
// (784 -> 32 -> 10; software/benchmarks/phase11_model.h, the catalog's only
// MLP) on its 32 test images, by the best CPU code and by the NPU on the Phase 19
// SoC. The window is the workload's (software/benchmarks/mnist_infer.c): from
// the image in memory to its prediction — fc1, requantization and ReLU, fc2,
// requantization, argmax.
//
// The CPU's code (the best CPU code, DOT8 included): each layer a DOT8
// matrix-vector product sixteen neurons at a time, the weights packed once
// (sixteen neurons interleaved a word at a time — the model's CPU layout,
// prepared before the windows as the NPU's row-major one is), the loop
// scheduled by hand for the core (each add two instructions after its dot8,
// each load used three or more later). The
// NPU's: each layer a job (N = 1: the K-split mapping) on the model's own
// row-major weights. Both paths' logits must equal the reference's
// (phase11_images.h, from scripts/phase11_reference.py), and the testbench
// checks every NPU job. One record, then main returns 0:
//   MNIST,images=32,cpu_cycles=..,npu_cycles=..,worst_ratio_x1000=..,fc1_job_cycles=..,status=PASS
#include <stdint.h>
#include "aster_npu2.h"
#include "npu2_soc.h"
#include "phase11_model.h"
#include "phase11_images.h"

enum { FC1_BLOCKS = 2, FC2_BLOCKS = 1, FC1_WORDS = 784 / 4, FC2_WORDS = 32 / 4 };
static uint32_t fc1_packed[FC1_BLOCKS * FC1_WORDS * 16] __attribute__((aligned(64)));
static uint32_t fc2_packed[FC2_BLOCKS * FC2_WORDS * 16] __attribute__((aligned(64)));   // rows 10-15 zero
static struct {
    int8_t input[784];
    int8_t hidden[32];
    int32_t acc1[32];
    int32_t acc2[16];
    int8_t logits[10];
} io __attribute__((aligned(64)));

// The CPU's layout: sixteen neurons interleaved a word at a time (block b,
// word k/4: sixteen words, neuron 16 b + r's four bytes k..k+3).
static void pack(const int8_t *w, uint32_t rows, uint32_t k, uint32_t *out, uint32_t blocks) {
    const uint32_t words = k / 4u;
    for (uint32_t blk = 0; blk < blocks; ++blk)
        for (uint32_t kk = 0; kk < words; ++kk)
            for (uint32_t r = 0; r < 16u; ++r) {
                const uint32_t row = 16u * blk + r;
                uint32_t word = 0;
                for (uint32_t byte = 0; byte < 4u && row < rows; ++byte)
                    word |= (uint32_t)(uint8_t)w[row * k + 4u * kk + byte] << (8u * byte);
                out[(blk * words + kk) * 16u + r] = word;
            }
}

// y[16 b + r] = the dot of packed block b's neuron r with x, for every block:
// a word of x (four k) a step, sixteen neurons' words loaded three ahead of
// their dot8s, each add two after its dot8 — 52 instructions, no stall
// (cpu.md §4), so x is read once a block (twice in fc1, not eight times).
#define DOT8(d, a, b) ".insn r 0x0b, 0, 0, " d ", " a ", " b "\n\t"
static void matvec_dot8(const uint32_t *packed, const int8_t *x, int32_t *y, uint32_t blocks, uint32_t words) {
    for (uint32_t blk = 0; blk < blocks; ++blk) {
        const uint32_t *pw = packed + blk * words * 16u;
        const uint32_t *px = (const uint32_t *)x, *end = px + words;
        int32_t a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15;
        __asm__ volatile (
            "li %[a0], 0\n\t" "li %[a1], 0\n\t" "li %[a2], 0\n\t" "li %[a3], 0\n\t"
            "li %[a4], 0\n\t" "li %[a5], 0\n\t" "li %[a6], 0\n\t" "li %[a7], 0\n\t"
            "li %[a8], 0\n\t" "li %[a9], 0\n\t" "li %[a10], 0\n\t" "li %[a11], 0\n\t"
            "li %[a12], 0\n\t" "li %[a13], 0\n\t" "li %[a14], 0\n\t" "li %[a15], 0\n\t"
            "1:\n\t"
            "lw a7, 0(%[px])\n\t" "lw t3, 0(%[pw])\n\t" "lw t4, 4(%[pw])\n\t" "lw t5, 8(%[pw])\n\t"
            "lw t6, 12(%[pw])\n\t" DOT8("t0", "t3", "a7")
            "lw t3, 16(%[pw])\n\t" DOT8("t1", "t4", "a7") "add %[a0], %[a0], t0\n\t"
            "lw t4, 20(%[pw])\n\t" DOT8("t0", "t5", "a7") "add %[a1], %[a1], t1\n\t"
            "lw t5, 24(%[pw])\n\t" DOT8("t1", "t6", "a7") "add %[a2], %[a2], t0\n\t"
            "lw t6, 28(%[pw])\n\t" DOT8("t0", "t3", "a7") "add %[a3], %[a3], t1\n\t"
            "lw t3, 32(%[pw])\n\t" DOT8("t1", "t4", "a7") "add %[a4], %[a4], t0\n\t"
            "lw t4, 36(%[pw])\n\t" DOT8("t0", "t5", "a7") "add %[a5], %[a5], t1\n\t"
            "lw t5, 40(%[pw])\n\t" DOT8("t1", "t6", "a7") "add %[a6], %[a6], t0\n\t"
            "lw t6, 44(%[pw])\n\t" DOT8("t0", "t3", "a7") "add %[a7], %[a7], t1\n\t"
            "lw t3, 48(%[pw])\n\t" DOT8("t1", "t4", "a7") "add %[a8], %[a8], t0\n\t"
            "lw t4, 52(%[pw])\n\t" DOT8("t0", "t5", "a7") "add %[a9], %[a9], t1\n\t"
            "lw t5, 56(%[pw])\n\t" DOT8("t1", "t6", "a7") "add %[a10], %[a10], t0\n\t"
            "lw t6, 60(%[pw])\n\t" DOT8("t0", "t3", "a7") "add %[a11], %[a11], t1\n\t"
            DOT8("t1", "t4", "a7") "add %[a12], %[a12], t0\n\t"
            DOT8("t0", "t5", "a7") "add %[a13], %[a13], t1\n\t"
            DOT8("t1", "t6", "a7") "add %[a14], %[a14], t0\n\t"
            "addi %[px], %[px], 4\n\t" "addi %[pw], %[pw], 64\n\t" "add %[a15], %[a15], t1\n\t"
            "bne %[px], %[end], 1b\n\t"
            : [a0] "=&r"(a0), [a1] "=&r"(a1), [a2] "=&r"(a2), [a3] "=&r"(a3), [a4] "=&r"(a4), [a5] "=&r"(a5),
              [a6] "=&r"(a6), [a7] "=&r"(a7), [a8] "=&r"(a8), [a9] "=&r"(a9), [a10] "=&r"(a10),
              [a11] "=&r"(a11), [a12] "=&r"(a12), [a13] "=&r"(a13), [a14] "=&r"(a14), [a15] "=&r"(a15),
              [pw] "+r"(pw), [px] "+r"(px)
            : [end] "r"(end)
            : "t0", "t1", "t3", "t4", "t5", "t6", "a7", "memory");
        int32_t *out = y + 16u * blk;
        out[0] = a0; out[1] = a1; out[2] = a2; out[3] = a3; out[4] = a4; out[5] = a5; out[6] = a6; out[7] = a7;
        out[8] = a8; out[9] = a9; out[10] = a10; out[11] = a11; out[12] = a12; out[13] = a13; out[14] = a14;
        out[15] = a15;
    }
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

// The CPU's requantization, ReLU and argmax (the same for both paths).
static void finish_fc1(void) {
    for (uint32_t o = 0; o < 32u; ++o) {
        const int8_t value = requant(io.acc1[o], PHASE11_FC1_MULT, PHASE11_FC1_SHIFT, phase11_fc1_bias_q[o]);
        io.hidden[o] = value > 0 ? value : 0;
    }
}
static int finish_fc2(void) {
    int best = 0;
    for (uint32_t o = 0; o < 10u; ++o) {
        io.logits[o] = requant(io.acc2[o], PHASE11_FC2_MULT, PHASE11_FC2_SHIFT, phase11_fc2_bias_q[o]);
        if (io.logits[o] > io.logits[best]) best = (int)o;
    }
    return best;
}

static int logits_ok(uint32_t image) {
    for (uint32_t o = 0; o < 10u; ++o)
        if (io.logits[o] != phase11_test_logits[image * 10u + o]) return 0;
    return 1;
}

static void run_layer(const void *a, const void *b, void *c, uint32_t m, uint32_t k) {
    const struct aster_npu2_job job = {a, b, c, k, 1, 4, m, 1, k, 0, 0, 0, 0, 0};
    aster_npu2_start(&job);
    aster_npu2_wait();
    aster_npu2_ack();
}

int main(void) {
    if (ASTER_NPU2_REG(ASTER_NPU2_ABI) != 2u) return 1;
    pack(phase11_fc1_weights, 32, 784, fc1_packed, FC1_BLOCKS);
    pack(phase11_fc2_weights, 10, 32, fc2_packed, FC2_BLOCKS);
    uint64_t cpu_total = 0, npu_total = 0, fc1_job_cycles = 0;
    uint32_t worst = 0xFFFFFFFFu, mismatches = 0, wrong_class = 0, correct = 0;
    for (uint32_t image = 0; image < PHASE11_TEST_COUNT; ++image) {
        for (uint32_t i = 0; i < 784u; ++i) io.input[i] = phase11_test_images[image * 784u + i];

        // The CPU.
        const uint64_t cpu_start = npu2_cycles();
        matvec_dot8(fc1_packed, io.input, io.acc1, FC1_BLOCKS, FC1_WORDS);
        finish_fc1();
        matvec_dot8(fc2_packed, io.hidden, io.acc2, FC2_BLOCKS, FC2_WORDS);
        const int cpu_best = finish_fc2();
        const uint64_t cpu_cycles = npu2_cycles() - cpu_start;
        if (!logits_ok(image)) ++mismatches;
        if ((uint32_t)cpu_best != phase11_test_classes[image]) ++wrong_class;
        for (uint32_t o = 0; o < 32u; ++o) io.acc1[o] = (int32_t)0xA5A5A5A5u;
        for (uint32_t o = 0; o < 16u; ++o) io.acc2[o] = (int32_t)0xA5A5A5A5u;
        for (uint32_t o = 0; o < 10u; ++o) io.logits[o] = 0x55;

        // The NPU.
        const uint64_t npu_start = npu2_cycles();
        run_layer(phase11_fc1_weights, io.input, io.acc1, 32, 784);
        finish_fc1();
        run_layer(phase11_fc2_weights, io.hidden, io.acc2, 10, 32);
        const int npu_best = finish_fc2();
        const uint64_t npu_cycles = npu2_cycles() - npu_start;
        if (!logits_ok(image)) ++mismatches;
        if ((uint32_t)npu_best != phase11_test_classes[image]) ++wrong_class;
        if ((uint32_t)npu_best == phase11_test_labels[image]) ++correct;     // the labels' accuracy, reported
        if (image == 0) {
            run_layer(phase11_fc1_weights, io.input, io.acc1, 32, 784);   // fc1 alone, for its counters
            fc1_job_cycles = aster_npu2_counter(ASTER_NPU2_JOB_CYCLES);
        }

        cpu_total += cpu_cycles;
        npu_total += npu_cycles;
        const uint32_t ratio = (uint32_t)cpu_cycles * 1000u / (uint32_t)npu_cycles;   // (a few 10,000 cycles each)
        if (ratio < worst) worst = ratio;
    }
    const int pass = mismatches == 0 && wrong_class == 0;
    npu2_puts("MNIST");
    npu2_field("images", PHASE11_TEST_COUNT);
    npu2_field("cpu_cycles", cpu_total); npu2_field("npu_cycles", npu_total);
    npu2_field("worst_ratio_x1000", worst); npu2_field("fc1_job_cycles", fc1_job_cycles);
    npu2_field("mismatches", mismatches); npu2_field("correct_labels", correct);
    npu2_puts(pass ? ",status=PASS\n" : ",status=FAIL\n");
    return pass ? 0 : 2;
}
