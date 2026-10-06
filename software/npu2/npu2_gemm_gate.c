// Milestone 19.4's GEMM gates (docs/npu.md §7): each declared dense GEMM —
// 64x64x64, 96x96x96, 128x64x128 — computed by the best CPU code and by the NPU
// on the Phase 19 SoC, end to end: from A (M x K) and B (K x N), signed bytes
// in memory in npu.md §2's row-major layout, to C (M x N, int32) in memory and
// visible to the CPU. Then the N = 1 cases §7 publishes without a threshold
// (MNIST's first layer, 32x1x784, and Conv2D, 784x1x25), the NPU alone.
//
// The CPU's code (the baseline the owner chose: the best CPU code, DOT8
// included), its packing inside the timed interval: B packed in blocks of four
// columns, each word four consecutive k of one column, in groups of columns of
// at most 3.5 KiB, each group in its own slot, the slots 4 KiB apart (each
// group in the same 3.5 KiB of the 4 KiB direct-mapped data cache, the 512
// bytes left for the stack's top and the globals: the slots start just past
// them in the cache). A needs no packing: four of its rows are 4K contiguous
// bytes, each word four consecutive k of one row, read in place (the kernel
// built for each K, the rows' offsets immediates). For each group, for each
// block of four rows, each 4 x 4 block of C is computed in sixteen registers:
// per eight k, sixteen word loads and thirty-two dot8s and adds (the first
// four k's dot8s straight into the sixteen), scheduled by hand for the core
// (cpu.md §4: each add two instructions after its dot8, each loaded word used
// three or more instructions later), no stall: 85 cycles per eight k, as
// measured on the core. The rest is B's packing, C's stores and the cache's
// misses. The NPU's: the descriptor written, START, its end polled
// (aster_npu2.h).
//
// Each result is checked against the testbench's reference (the CPU's through
// npu2_check_gemm, the NPU's at the job's end), and the NPU's here against the
// CPU's (a checksum: the two do not fit side by side). One record a case, then
// main returns 0:
//   GEMM,m=..,n=..,k=..,cpu_cycles=..,npu_cycles=..,job_cycles=..,job_macs=..,status=PASS
//   N1,m=..,n=1,k=..,npu_cycles=..,job_cycles=..,job_macs=..,status=PASS
#include <stdint.h>
#include "aster_npu2.h"
#include "npu2_soc.h"

enum { SLOT = 4096, GROUP_BYTES = 3584 };

// One arena, carved for each case: A, B, C, then the slots of packed B, the
// first starting in the cache just past the globals (declared next to the
// stack's top). 128x64x128 needs about 70 KiB, the most.
static uint8_t arena[80 * 1024] __attribute__((aligned(64)));
static int8_t *a, *b;
static int32_t *c;
static uint8_t *slots;
static uint32_t group_columns(uint32_t k) {
    const uint32_t blocks = GROUP_BYTES / (16u * (k / 4u));
    return 4u * (blocks ? blocks : 1u);
}
static int carve(uint32_t m, uint32_t n, uint32_t k) {
    uint8_t *next = arena;
    a = (int8_t *)next; next += (m * k + 63u) & ~63u;
    b = (int8_t *)next; next += (k * n + 63u) & ~63u;
    c = (int32_t *)next; next += 4u * m * n;
    const uintptr_t hot_end = ((uintptr_t)(&slots + 1) + 63u) & ~(uintptr_t)63u;     // the globals' end
    slots = (uint8_t *)(((uintptr_t)next + SLOT - 1u) & ~(uintptr_t)(SLOT - 1u)) + (hot_end & (SLOT - 1u));
    const uint32_t groups = (n + group_columns(k) - 1u) / group_columns(k);
    return slots + groups * SLOT <= arena + sizeof arena;
}

static uint32_t random_state = 0x2545F491u;
static uint32_t next_random(void) {
    random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
    return random_state;
}

// B's column block j/4 (in its group's slot): word k/4, column j%4 — B(k..k+3, j),
// each 4 x 4 byte block of B (four rows, four columns: four word loads)
// transposed in registers.
static void pack_b(uint32_t n, uint32_t k) {
    const uint32_t words = k / 4u, group = group_columns(k);
    uint8_t *slot = slots;
    for (uint32_t j = 0, in_group = 0; j < n; j += 4u, in_group += 4u) {
        if (in_group == group) { in_group = 0; slot += SLOT; }
        uint32_t *out = (uint32_t *)(slot + in_group * 4u * words);
        const uint8_t *row = (const uint8_t *)b + j;
        for (uint32_t w = 0; w < words; ++w, row += 4u * n) {
            const uint32_t r0 = *(const uint32_t *)row, r1 = *(const uint32_t *)(row + n);
            const uint32_t r2 = *(const uint32_t *)(row + 2u * n), r3 = *(const uint32_t *)(row + 3u * n);
            const uint32_t t0 = (r0 & 0x00FF00FFu) | ((r1 << 8) & 0xFF00FF00u);   // r0.0 r1.0 r0.2 r1.2
            const uint32_t t1 = ((r0 >> 8) & 0x00FF00FFu) | (r1 & 0xFF00FF00u);   // r0.1 r1.1 r0.3 r1.3
            const uint32_t t2 = (r2 & 0x00FF00FFu) | ((r3 << 8) & 0xFF00FF00u);
            const uint32_t t3 = ((r2 >> 8) & 0x00FF00FFu) | (r3 & 0xFF00FF00u);
            out[4 * w + 0] = (t0 & 0xFFFFu) | (t2 << 16);
            out[4 * w + 1] = (t1 & 0xFFFFu) | (t3 << 16);
            out[4 * w + 2] = (t0 >> 16) | (t2 & 0xFFFF0000u);
            out[4 * w + 3] = (t1 >> 16) | (t3 & 0xFFFF0000u);
        }
    }
}

// Four k of the 4 x 4 block: A's four rows' words at OA (the rows K apart), B's
// four columns' at OB.
#define DOT8(d, x, y) ".insn r 0x0b, 0, 0, " d ", " x ", " y "\n\t"
#define LOADS(OA, OB) \
    "lw a6, " OB "(%[pb])\n\t" "lw t3, " OA "(%[pa])\n\t" "lw t4, %[k1]+" OA "(%[pa])\n\t" \
    "lw t5, %[k2]+" OA "(%[pa])\n\t" "lw t6, %[k3]+" OA "(%[pa])\n\t" "lw a7, " OB "+4(%[pb])\n\t"
#define FOUR_K(OA, OB) LOADS(OA, OB) \
    DOT8("t0", "t3", "a6") DOT8("t1", "t4", "a6") \
    "add %[c00], %[c00], t0\n\t" DOT8("t0", "t5", "a6") \
    "add %[c10], %[c10], t1\n\t" DOT8("t1", "t6", "a6") \
    "add %[c20], %[c20], t0\n\t" "lw a6, " OB "+8(%[pb])\n\t" \
    DOT8("t0", "t3", "a7") "add %[c30], %[c30], t1\n\t" \
    DOT8("t1", "t4", "a7") "add %[c01], %[c01], t0\n\t" \
    DOT8("t0", "t5", "a7") "add %[c11], %[c11], t1\n\t" \
    DOT8("t1", "t6", "a7") "add %[c21], %[c21], t0\n\t" \
    "lw a7, " OB "+12(%[pb])\n\t" \
    DOT8("t0", "t3", "a6") "add %[c31], %[c31], t1\n\t" \
    DOT8("t1", "t4", "a6") "add %[c02], %[c02], t0\n\t" \
    DOT8("t0", "t5", "a6") "add %[c12], %[c12], t1\n\t" \
    DOT8("t1", "t6", "a6") "add %[c22], %[c22], t0\n\t" \
    DOT8("t0", "t3", "a7") "add %[c32], %[c32], t1\n\t" \
    DOT8("t1", "t4", "a7") "add %[c03], %[c03], t0\n\t" \
    DOT8("t0", "t5", "a7") "add %[c13], %[c13], t1\n\t" \
    DOT8("t1", "t6", "a7") "add %[c23], %[c23], t0\n\t" \
    "add %[c33], %[c33], t1\n\t"
// The first four k: the dot8s straight into the sixteen accumulators.
#define FIRST_FOUR_K LOADS("0", "0") \
    DOT8("%[c00]", "t3", "a6") DOT8("%[c10]", "t4", "a6") DOT8("%[c20]", "t5", "a6") DOT8("%[c30]", "t6", "a6") \
    "lw a6, 8(%[pb])\n\t" \
    DOT8("%[c01]", "t3", "a7") DOT8("%[c11]", "t4", "a7") DOT8("%[c21]", "t5", "a7") DOT8("%[c31]", "t6", "a7") \
    "lw a7, 12(%[pb])\n\t" \
    DOT8("%[c02]", "t3", "a6") DOT8("%[c12]", "t4", "a6") DOT8("%[c22]", "t5", "a6") DOT8("%[c32]", "t6", "a6") \
    DOT8("%[c03]", "t3", "a7") DOT8("%[c13]", "t4", "a7") DOT8("%[c23]", "t5", "a7") DOT8("%[c33]", "t6", "a7")

// C = A x B for one K (a multiple of 8, at most 680: the rows' offsets are
// immediates), inlined with K a constant.
static inline __attribute__((always_inline)) void gemm_fixed(uint32_t m, uint32_t n, const uint32_t k) {
    const uint32_t group = group_columns(k);
    const uint8_t *slot = slots;
    for (uint32_t j0 = 0; j0 < n; j0 += group, slot += SLOT)
        for (uint32_t i = 0; i < m; i += 4u) {
            const uint8_t *rows = (const uint8_t *)a + i * k;
            const uint32_t *b_block = (const uint32_t *)slot;
            for (uint32_t j = j0; j < j0 + group && j < n; j += 4u, b_block += k) {
                const uint8_t *pa = rows, *end = rows + k;
                const uint32_t *pb = b_block;
                int32_t c00, c01, c02, c03, c10, c11, c12, c13, c20, c21, c22, c23, c30, c31, c32, c33;
                __asm__ volatile (
                    FIRST_FOUR_K
                    FOUR_K("4", "16")
                    "addi %[pa], %[pa], 8\n\t" "addi %[pb], %[pb], 32\n\t"
                    "beq %[pa], %[end], 2f\n\t"
                    "1:\n\t"
                    FOUR_K("0", "0")
                    FOUR_K("4", "16")
                    "addi %[pa], %[pa], 8\n\t" "addi %[pb], %[pb], 32\n\t"
                    "bne %[pa], %[end], 1b\n\t"
                    "2:\n\t"
                    : [c00] "=&r"(c00), [c01] "=&r"(c01), [c02] "=&r"(c02), [c03] "=&r"(c03),
                      [c10] "=&r"(c10), [c11] "=&r"(c11), [c12] "=&r"(c12), [c13] "=&r"(c13),
                      [c20] "=&r"(c20), [c21] "=&r"(c21), [c22] "=&r"(c22), [c23] "=&r"(c23),
                      [c30] "=&r"(c30), [c31] "=&r"(c31), [c32] "=&r"(c32), [c33] "=&r"(c33),
                      [pa] "+r"(pa), [pb] "+r"(pb)
                    : [end] "r"(end), [k1] "i"(k), [k2] "i"(2u * k), [k3] "i"(3u * k)
                    : "t0", "t1", "t3", "t4", "t5", "t6", "a6", "a7", "memory");
                int32_t *row = c + i * n + j;
                row[0] = c00; row[1] = c01; row[2] = c02; row[3] = c03; row += n;
                row[0] = c10; row[1] = c11; row[2] = c12; row[3] = c13; row += n;
                row[0] = c20; row[1] = c21; row[2] = c22; row[3] = c23; row += n;
                row[0] = c30; row[1] = c31; row[2] = c32; row[3] = c33;
            }
        }
}

// The gate cases' K.
static int gemm_dot8(uint32_t m, uint32_t n, uint32_t k) {
    switch (k) {
    case 64: gemm_fixed(m, n, 64); return 1;
    case 96: gemm_fixed(m, n, 96); return 1;
    case 128: gemm_fixed(m, n, 128); return 1;
    default: return 0;
    }
}

static uint32_t checksum(uint32_t words) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < words; ++i) sum = (sum * 33u) ^ (uint32_t)c[i];
    return sum;
}

// The NPU's job, end to end; its cycles from before the descriptor to after the end.
static uint64_t npu_job(uint32_t m, uint32_t n, uint32_t k, uint32_t *status) {
    const struct aster_npu2_job job = {a, b, c, k, n, 4u * n, m, n, k, 0, 0, 0, 0, 0};
    const uint64_t start = npu2_cycles();
    aster_npu2_start(&job);
    *status = aster_npu2_wait();
    return npu2_cycles() - start;
}

int main(void) {
    static const uint32_t cases[][3] = {{64, 64, 64}, {96, 96, 96}, {128, 64, 128}};
    static const uint32_t n1_cases[][2] = {{32, 784}, {784, 25}};
    if (ASTER_NPU2_REG(ASTER_NPU2_ABI) != 2u) return 1;
    for (uint32_t t = 0; t < sizeof cases / sizeof cases[0]; ++t) {
        const uint32_t m = cases[t][0], n = cases[t][1], k = cases[t][2];
        if (!carve(m, n, k) || m % 4u || n % 4u) return 1;
        for (uint32_t i = 0; i < m * k; ++i) a[i] = (int8_t)next_random();
        for (uint32_t i = 0; i < k * n; ++i) b[i] = (int8_t)next_random();

        // The CPU: B packed, then the blocked DOT8 GEMM.
        const uint64_t cpu_start = npu2_cycles();
        pack_b(n, k);
        const int built = gemm_dot8(m, n, k);
        const uint64_t cpu_cycles = npu2_cycles() - cpu_start;
        npu2_check_gemm(a, b, c, k, n, 4u * n, m, n, k);
        const uint32_t cpu_sum = checksum(m * n);
        for (uint32_t i = 0; i < m * n; ++i) c[i] = (int32_t)0xA5A5A5A5u;    // the NPU's result must be its own

        uint32_t status;
        const uint64_t npu_cycles = npu_job(m, n, k, &status);
        const uint64_t job_cycles = aster_npu2_counter(ASTER_NPU2_JOB_CYCLES);
        const uint64_t job_macs = aster_npu2_counter(ASTER_NPU2_JOB_MACS);
        aster_npu2_ack();
        const int pass = built && status == ASTER_NPU2_DONE && checksum(m * n) == cpu_sum
                         && job_macs == (uint64_t)m * n * k;

        npu2_puts("GEMM");
        npu2_field("m", m); npu2_field("n", n); npu2_field("k", k);
        npu2_field("cpu_cycles", cpu_cycles); npu2_field("npu_cycles", npu_cycles);
        npu2_field("job_cycles", job_cycles); npu2_field("job_macs", job_macs);
        npu2_puts(pass ? ",status=PASS\n" : ",status=FAIL\n");
        if (!pass) return (int)(t + 2);
    }
    for (uint32_t t = 0; t < sizeof n1_cases / sizeof n1_cases[0]; ++t) {
        const uint32_t m = n1_cases[t][0], k = n1_cases[t][1];
        a = (int8_t *)arena; b = (int8_t *)arena + ((m * k + 63u) & ~63u); c = (int32_t *)(b + ((k + 63u) & ~63u));
        for (uint32_t i = 0; i < m * k; ++i) a[i] = (int8_t)next_random();
        for (uint32_t i = 0; i < k; ++i) b[i] = (int8_t)next_random();
        uint32_t status;
        const uint64_t npu_cycles = npu_job(m, 1, k, &status);
        const uint64_t job_cycles = aster_npu2_counter(ASTER_NPU2_JOB_CYCLES);
        const uint64_t job_macs = aster_npu2_counter(ASTER_NPU2_JOB_MACS);
        aster_npu2_ack();
        const int pass = status == ASTER_NPU2_DONE && job_macs == (uint64_t)m * k;
        npu2_puts("N1");
        npu2_field("m", m); npu2_field("n", 1); npu2_field("k", k);
        npu2_field("npu_cycles", npu_cycles); npu2_field("job_cycles", job_cycles); npu2_field("job_macs", job_macs);
        npu2_puts(pass ? ",status=PASS\n" : ",status=FAIL\n");
        if (!pass) return (int)(t + 10);
    }
    return 0;
}
