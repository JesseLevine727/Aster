// Phase 9 CPU-controlled NPU runtime acceptance workload.
//
// The primary hart owns the NPU and DMA descriptors. A and B are generated in
// shared RAM, published into separate shared slots by DMA, and then consumed
// by the coherent NPU device master. C is intentionally touched before the
// accelerator starts so cache-on runs test device-write invalidation rather
// than relying on an uncached, never-before-read destination.
#include "aster.h"
#include "aster_dma.h"
#include "aster_npu.h"

enum {
    POLLS = 8000000u,
    ARENA_BYTES = 28672u,
    STAGE_A = 0u,
    STAGE_B = 2048u,
    SLOT_A = 4096u,
    SLOT_B = 8192u,
    SLOT_C = 12288u,
    SLOT_EXPECTED = 20480u,
    SENTINEL = 0xa5u
};

static uint8_t arena[ARENA_BYTES] __attribute__((aligned(64)));
static volatile uint32_t npu_results[128] __attribute__((section(".npu_summary")));
static uint32_t checks;

static void fail(uint32_t code) {
    npu_results[0] = 0x50415353u;
    npu_results[1] = code;
    npu_results[2] = checks;
    aster_puts("NPU RUNTIME FAIL code=0x");
    aster_put_hex32(code);
    aster_putc('\n');
    __asm__ volatile ("ebreak");
    for (;;) {}
}

static void check(int ok, uint32_t code) {
    ++checks;
    if (!ok) fail(code);
}

static uint32_t span_a(uint32_t m, uint32_t k, uint32_t stride) {
    return m == 0u || k == 0u ? 0u : (m - 1u) * stride + k;
}

static uint32_t span_b(uint32_t n, uint32_t k, uint32_t stride) {
    return n == 0u || k == 0u ? 0u : (k - 1u) * stride + n;
}

static uint32_t span_c(uint32_t m, uint32_t n, uint32_t stride) {
    return m == 0u || n == 0u ? 0u : (m - 1u) * stride + (n - 1u) * 4u + 4u;
}

static int8_t a_pattern(uint32_t index, uint32_t seed) {
    if ((index + seed) % 29u == 0u) return (int8_t)-128;
    if ((index + seed) % 31u == 0u) return (int8_t)127;
    if ((index + seed) % 37u == 0u) return 0;
    return (int8_t)((index * 73u + seed * 19u + (index >> 2)) & 0xffu);
}

static int8_t b_pattern(uint32_t index, uint32_t seed) {
    if ((index + seed) % 23u == 0u) return (int8_t)-128;
    if ((index + seed) % 41u == 0u) return (int8_t)127;
    if ((index + seed) % 43u == 0u) return 0;
    return (int8_t)((index * 29u + seed * 47u + (index >> 1)) & 0xffu);
}

static void fill_bytes(uint32_t offset, uint32_t length, uint8_t value) {
    for (uint32_t i = 0; i < length; ++i) arena[offset + i] = value;
}

static void prepare_inputs(uint32_t m, uint32_t n, uint32_t k,
                           uint32_t a_stride, uint32_t b_stride,
                           uint32_t seed, uint32_t a_offset, uint32_t b_offset,
                           uint32_t c_offset, uint32_t *a_span_out,
                           uint32_t *b_span_out, uint32_t *c_span_out) {
    fill_bytes(0u, ARENA_BYTES, SENTINEL);
    const uint32_t a_span = span_a(m, k, a_stride);
    const uint32_t b_span = span_b(n, k, b_stride);
    const uint32_t c_span = span_c(m, n, (n * 4u) + 4u);
    for (uint32_t i = 0; i < a_span; ++i) arena[STAGE_A + i] = (uint8_t)a_pattern(i, seed);
    for (uint32_t i = 0; i < b_span; ++i) arena[STAGE_B + i] = (uint8_t)b_pattern(i, seed ^ 0x5au);
    *a_span_out = a_span;
    *b_span_out = b_span;
    *c_span_out = c_span;

    if (a_span != 0u)
        check(aster_dma_copy(arena + SLOT_A + a_offset, arena + STAGE_A,
                             a_span, POLLS) == ASTER_DMA_OK, 0x100u);
    if (b_span != 0u)
        check(aster_dma_copy(arena + SLOT_B + b_offset, arena + STAGE_B,
                             b_span, POLLS) == ASTER_DMA_OK, 0x101u);

    // The destination and expected regions start as guards. The scalar model
    // changes only expected's valid C byte lanes; the NPU must change only C's.
    (void)c_offset;
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
}

static void verify_input(uint32_t m, uint32_t n, uint32_t k,
                         uint32_t a_stride, uint32_t b_stride,
                         uint32_t seed, uint32_t a_offset, uint32_t b_offset,
                         uint32_t a_span, uint32_t b_span) {
    for (uint32_t i = 0; i < a_span; ++i)
        check(arena[SLOT_A + a_offset + i] == (uint8_t)a_pattern(i, seed), 0x110u);
    for (uint32_t i = 0; i < b_span; ++i)
        check(arena[SLOT_B + b_offset + i] == (uint8_t)b_pattern(i, seed ^ 0x5au), 0x111u);
    // DMA copied the complete descriptor spans, including row padding. The
    // NPU may read valid operand bytes only and must not alter either input.
    (void)m; (void)n; (void)k; (void)a_stride; (void)b_stride;
}

static void verify_c(const struct aster_npu_gemm *job,
                     const struct aster_npu_gemm *expected_job,
                     uint32_t c_offset, uint32_t c_span,
                     uint32_t npu_bytes_written) {
    const uint8_t *actual = (const uint8_t *)(uintptr_t)job->c;
    const uint8_t *expected = (const uint8_t *)(uintptr_t)expected_job->c;
    for (uint32_t i = 0; i < c_span; ++i) {
        const uint32_t row = job->c_stride == 0u ? 0u : i / job->c_stride;
        const uint32_t in_row = job->c_stride == 0u ? i : i % job->c_stride;
        const int valid = row < job->m && in_row < job->n * 4u;
        if (valid) check(actual[i] == expected[i], 0x120u);
        else check(actual[i] == SENTINEL, 0x121u);
    }
    check(npu_bytes_written == job->m * job->n * 4u, 0x122u);
    (void)c_offset;
}

static uint32_t expected_tiles(uint32_t m, uint32_t n) {
    return ((m + 3u) / 4u) * ((n + 3u) / 4u);
}

static uint32_t expected_reads(uint32_t m, uint32_t n, uint32_t k) {
    uint32_t total = 0;
    for (uint32_t row = 0; row < m; row += 4u)
        for (uint32_t col = 0; col < n; col += 4u) {
            const uint32_t rows = m - row < 4u ? m - row : 4u;
            const uint32_t cols = n - col < 4u ? n - col : 4u;
            total += k * (rows + cols);
        }
    return total;
}

static void run_job(uint32_t m, uint32_t n, uint32_t k,
                    uint32_t a_stride, uint32_t b_stride,
                    uint32_t seed, uint32_t a_offset, uint32_t b_offset,
                    uint32_t c_offset) {
    const uint32_t c_stride = n * 4u + 4u;
    uint32_t a_span, b_span, c_span;
    prepare_inputs(m, n, k, a_stride, b_stride, seed, a_offset, b_offset,
                   c_offset, &a_span, &b_span, &c_span);
    struct aster_npu_gemm job = {
        (const int8_t *)(void *)(arena + SLOT_A + a_offset),
        (const int8_t *)(void *)(arena + SLOT_B + b_offset),
        (int32_t *)(void *)(arena + SLOT_C + c_offset),
        a_stride, b_stride, c_stride, m, n, k
    };
    struct aster_npu_gemm expected = {
        job.a, job.b, (int32_t *)(void *)(arena + SLOT_EXPECTED + c_offset),
        a_stride, b_stride, c_stride, m, n, k
    };
    aster_npu_scalar_gemm(&expected);

    // Read every C byte before START. With ENABLE_L1=1 this deliberately
    // creates cache lines that the device stores must invalidate.
    volatile uint8_t *actual = (volatile uint8_t *)(uintptr_t)job.c;
    for (uint32_t i = 0; i < c_span; ++i) (void)actual[i];
    __asm__ volatile ("fence iorw,iorw" ::: "memory");

    struct aster_npu_status status;
    const enum aster_npu_result submitted = aster_npu_submit(&job);
    if (submitted == ASTER_NPU_PENDING)
        check(aster_npu_wait(POLLS, &status) == ASTER_NPU_OK, 0x131u);
    else
        check(aster_npu_poll(&status) == ASTER_NPU_OK, 0x131u);
    check((status.status & (ASTER_NPU_BUSY | ASTER_NPU_DONE | ASTER_NPU_ERROR |
                           ASTER_NPU_ABORTED)) == ASTER_NPU_DONE, 0x132u);
    check(status.tiles == expected_tiles(m, n), 0x133u);
    check(status.bytes_read == expected_reads(m, n, k), 0x134u);
    verify_input(m, n, k, a_stride, b_stride, seed, a_offset, b_offset,
                 a_span, b_span);
    verify_c(&job, &expected, c_offset, c_span, status.bytes_written);
    check(status.job_cycles != 0u || (m == 0u || n == 0u), 0x135u);
    check(status.compute_cycles == k * expected_tiles(m, n), 0x136u);
}

static void directed_invalids(void) {
    struct aster_npu_gemm valid = {
        (const int8_t *)(void *)(arena + SLOT_A),
        (const int8_t *)(void *)(arena + SLOT_B),
        (int32_t *)(void *)(arena + SLOT_C),
        4u, 4u, 20u, 2u, 3u, 4u
    };
    struct aster_npu_gemm bad = valid;
    bad.m = 1025u;
    check(aster_npu_submit(&bad) == ASTER_NPU_BAD_DIMENSIONS, 0x140u);
    bad = valid; bad.a_stride = 3u;
    check(aster_npu_submit(&bad) == ASTER_NPU_BAD_STRIDE, 0x141u);
    bad = valid; bad.a = (const int8_t *)(uintptr_t)0x10008000u;
    check(aster_npu_submit(&bad) == ASTER_NPU_BAD_A, 0x142u);
    bad = valid; bad.c = (int32_t *)(void *)(arena + SLOT_A + 1u);
    check(aster_npu_submit(&bad) == ASTER_NPU_OVERLAP, 0x143u);
}

void aster_secondary_main(void) {
    // Secondary execution is deliberately independent of NPU/DMA ownership.
    // The SoC scoreboard also requires this hart to retire real instructions.
    for (volatile uint32_t i = 0; i < 64u; ++i) {}
}

int main(void) {
    check(*(volatile uint32_t *)0x20002008u == 1u ||
          *(volatile uint32_t *)0x20002008u == 2u, 0x001u);
    check(*(volatile uint32_t *)(uintptr_t)(ASTER_NPU_BASE + 0x08u) == ASTER_NPU_ABI, 0x002u);
    check(*(volatile uint32_t *)(uintptr_t)(ASTER_NPU_BASE + 0x0cu) == ASTER_NPU_COUNTER_ABI, 0x003u);
    check(*(volatile uint32_t *)(uintptr_t)(ASTER_NPU_BASE + 0x54u) == 0x1fu, 0x004u);
    directed_invalids();

    run_job(1u, 1u, 1u, 3u, 5u, 0x11u, 1u, 2u, 3u);
    run_job(3u, 5u, 8u, 11u, 9u, 0x23u, 0u, 1u, 3u);
    run_job(5u, 7u, 3u, 8u, 10u, 0x35u, 1u, 2u, 0u);
    run_job(7u, 5u, 15u, 19u, 8u, 0x47u, 0u, 2u, 3u);
    run_job(8u, 8u, 31u, 35u, 13u, 0x59u, 1u, 0u, 3u);
    run_job(15u, 3u, 32u, 37u, 7u, 0x6bu, 0u, 1u, 0u);
    run_job(4u, 4u, 0u, 0u, 4u, 0x7du, 0u, 0u, 3u);
    run_job(0u, 4u, 7u, 7u, 4u, 0x8fu, 0u, 0u, 0u);
    run_job(4u, 0u, 7u, 7u, 0u, 0x91u, 0u, 0u, 0u);
    directed_invalids();

    npu_results[0] = 0x4e505539u; // NPU9
    npu_results[1] = checks;
    npu_results[2] = 9u;
    aster_puts("NPU RUNTIME PASS jobs=9 checks=");
    aster_put_u32(checks);
    aster_puts(" summary=0x");
    aster_put_hex32((uint32_t)(uintptr_t)npu_results);
    aster_putc('\n');
    return 0;
}
