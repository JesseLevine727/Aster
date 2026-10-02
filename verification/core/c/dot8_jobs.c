// C test (18.5): v1's DOT8 runtime jobs (software/tests/dot8_runtime.c) on one
// hart of the Aster core, in lockstep with Spike. The dot, FIR and GEMM
// kernels of software/benchmarks/dot8_kernels.c, scalar and Xasterdot8, run
// over every alignment of their two inputs (4 x 4) and sizes covering empty
// inputs, the scalar tails and the packed loop; each result is compared with
// an independent scalar reference, the words around the outputs must keep
// their guard values, and the inputs must be unchanged. (v1 runs 736 jobs on
// two harts with DMA beside them; the sizes here are trimmed to keep the
// Spike log small: dot to 64, FIR and GEMM to 13.) Then, as v1's runtime does,
// lr.w, dot8, sc.w sixteen times (the reservation survives the dot8) and a
// dot8 into x0. main returns 0, or the failing check's code.
#include <stdint.h>
#include "aster_dot8.h"
#include "dot8_kernels.h"

enum { BUFFER = 96, OFFSET = 8, YWORDS = 24, YOFFSET = 4 };   // the inputs end by byte 75
static uint8_t input_a[BUFFER] __attribute__((aligned(64)));
static uint8_t input_b[BUFFER] __attribute__((aligned(64)));
static uint32_t output[YWORDS] __attribute__((aligned(64)));
static uint32_t reservation_word;

static const uint32_t dot_sizes[] = {0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 31, 64};
static const uint32_t other_sizes[] = {0, 1, 3, 4, 5, 8, 13};

static uint8_t pattern(uint32_t i, uint32_t seed, uint32_t bank) {
    switch (seed & 7) {
        case 0: return 0;
        case 1: return 128;
        case 2: return bank ? 127 : 128;
        case 3: return (i & 1) ? 127 : 128;
        default: return (uint8_t)((seed >> ((i & 3) * 8)) ^ (i * 73u) ^ (i >> 3) ^ (bank * 0x5bu));
    }
}
static uint32_t guard(uint32_t i, uint32_t seed) { return 0x6d5a0000u ^ (i * 0x01010101u) ^ seed; }
static uint32_t outputs(uint32_t kind) { return kind == 0 ? 1 : kind == 1 ? 8 : 15; }

// Independent scalar interpretation (not the driver's helper): output `out`
// reads A from a_first in steps of 1 and B from b_first in steps of b_step.
static uint32_t reference(const uint8_t *a, const uint8_t *b, uint32_t kind, uint32_t n, uint32_t out) {
    uint32_t b_column = out, a_row = 0;
    while (kind == 2 && b_column >= 5) { b_column -= 5; ++a_row; }   // GEMM: out = 5 * a_row + b_column
    uint32_t a_first = kind == 2 ? a_row * n : kind == 1 ? out : 0, b_first = kind == 2 ? b_column : 0;
    uint32_t b_step = kind == 2 ? 5 : 1, sum = 0;
    for (uint32_t k = 0; k < n; ++k) {
        int32_t x = a[a_first + k], y = b[b_first + k * b_step];
        if (x >= 128) x -= 256;
        if (y >= 128) y -= 256;
        sum += (uint32_t)(x * y);
    }
    return sum;
}

static void invoke(uint32_t kind, const uint8_t *a, const uint8_t *b, uint32_t *y, uint32_t n, int custom) {
    if (kind == 0) { if (custom) aster_dot8_custom_dot(a, b, y, n); else aster_dot8_scalar_dot(a, b, y, n); }
    else if (kind == 1) { if (custom) aster_dot8_custom_fir(a, b, y, n); else aster_dot8_scalar_fir(a, b, y, n); }
    else { if (custom) aster_dot8_custom_gemm(a, b, y, n); else aster_dot8_scalar_gemm(a, b, y, n); }
}

// Both kernels of a kind on one alignment and size; the expected words (the
// reference's results between guards) are computed once.
static int job(uint32_t kind, uint32_t n, uint32_t sa, uint32_t sb, uint32_t seed) {
    static uint32_t expected[YWORDS];
    const uint8_t *a = input_a + OFFSET + sa, *b = input_b + OFFSET + sb;
    for (uint32_t i = 0; i < YWORDS; ++i)
        expected[i] = i >= YOFFSET && i < YOFFSET + outputs(kind) ? reference(a, b, kind, n, i - YOFFSET)
                                                                  : guard(i, seed);
    for (int custom = 0; custom < 2; ++custom) {
        for (uint32_t i = 0; i < YWORDS; ++i) output[i] = guard(i, seed);
        invoke(kind, a, b, output + YOFFSET, n, custom);
        for (uint32_t i = 0; i < YWORDS; ++i)
            if (output[i] != expected[i]) return 2 + custom;     // 2: scalar, 3: Xasterdot8
    }
    return 0;
}

static int reservations(void) {
    for (uint32_t i = 0; i < 16; ++i) {
        uint32_t value = 0x12345678u ^ i, a = 0x80807fffu + i, b = 0xff01807fu, old, dot, failed;
        reservation_word = value;
        __asm__ volatile ("lr.w %0, (%3)\n\t"
                          ".insn r 0x0b, 0, 0, %1, %4, %5\n\t"
                          "sc.w %2, %6, (%3)"
                          : "=&r"(old), "=&r"(dot), "=&r"(failed)
                          : "r"(&reservation_word), "r"(a), "r"(b), "r"(value + 1) : "memory");
        if (old != value || failed || dot != aster_dot8_reference(a, b) || reservation_word != value + 1)
            return 0;
    }
    __asm__ volatile (".insn r 0x0b, 0, 0, x0, %0, %1" :: "r"(0x80808080u), "r"(0x80808080u));
    return 1;
}

int main(void) {
    uint32_t jobs = 0;
    for (uint32_t kind = 0; kind < 3; ++kind) {
        const uint32_t *sizes = kind == 0 ? dot_sizes : other_sizes;
        uint32_t count = kind == 0 ? sizeof dot_sizes / sizeof dot_sizes[0]
                                   : sizeof other_sizes / sizeof other_sizes[0];
        for (uint32_t index = 0; index < count; ++index) {
            uint32_t n = sizes[index], seed = 0xa57e8000u ^ ((kind * 16 + index) * 0x9e3779b9u);
            for (uint32_t i = 0; i < BUFFER; ++i) { input_a[i] = pattern(i, seed, 0); input_b[i] = pattern(i, seed, 1); }
            for (uint32_t sa = 0; sa < 4; ++sa)
                for (uint32_t sb = 0; sb < 4; ++sb) {
                    int failed = job(kind, n, sa, sb, seed);
                    if (failed) return failed;
                    ++jobs;
                }
            for (uint32_t i = 0; i < BUFFER; ++i)
                if (input_a[i] != pattern(i, seed, 0) || input_b[i] != pattern(i, seed, 1)) return 4;
        }
    }
    if (jobs != 16 * (12 + 7 + 7)) return 5;
    if (!reservations()) return 6;
    return 0;
}
