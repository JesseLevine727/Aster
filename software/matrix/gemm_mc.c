// 20.4's matrix: the multicore GEMM with DOT8 (docs/matrix.md §4.4; the scaling gate's, the owner's choice of
// 7 October 2026): npu.md §7's cases, each its own firmware (GEMM_M, GEMM_N, GEMM_K), on one worker or two.
// The CPU code is 19.4's (software/npu2/npu2_gemm_gate.c): B packed in blocks of four columns into 4 KiB
// slots, then the hand-scheduled 4x4 DOT8 blocks. With two workers (soc.md §9) the harts split B's packing
// (half of its column blocks each), meet at a barrier, then each computes half of C's rows. Packing,
// barrier, dispatch and join are all in the e2e window, as the one-worker run's packing is. The kernel window
// (matrix.md §4.6) is the compute alone: A in place and B packed before it (by the same split, so each half
// lies where its packer left it), two workers' shares released by a flag inside it (matrix_window.h).
// A (M x K) and B (K x N), signed bytes from xorshift32 seeded 0x2545F491 (A, then B), outside the windows;
// each record's checksum is C's ((sum * 33) ^ c), which scripts/matrix.py's oracle recomputes. A cold run
// (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is an untimed pass, the e2e
// window, then the kernel window; C (and, before the e2e window, B's packing) poisoned before each.
#include <stdint.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef GEMM_M
#define GEMM_M 64u
#endif
#ifndef GEMM_N
#define GEMM_N 64u
#endif
#ifndef GEMM_K
#define GEMM_K 64u
#endif
#ifndef GEMM_WORKERS
#define GEMM_WORKERS 2u
#endif
_Static_assert(GEMM_M % 8u == 0 && GEMM_N % 8u == 0 && GEMM_K % 8u == 0 && GEMM_K <= 680u, "the kernel's shapes");

enum { SLOT = 4096, GROUP_BYTES = 3584 };
#define GROUP_COLUMNS (4u * ((GROUP_BYTES / (4u * GEMM_K)) ? (GROUP_BYTES / (4u * GEMM_K)) : 1u))
#define GROUPS ((GEMM_N + GROUP_COLUMNS - 1u) / GROUP_COLUMNS)
#define ARENA_BYTES (((GEMM_M * GEMM_K + 63u) & ~63u) + ((GEMM_K * GEMM_N + 63u) & ~63u) + 4u * GEMM_M * GEMM_N \
                     + GROUPS * SLOT + 2u * SLOT)
static uint8_t arena[ARENA_BYTES] __attribute__((aligned(64)));
static int8_t *a, *b;
static int32_t *c;
static uint8_t *slots;

static void carve(void) {
    uint8_t *next = arena;
    a = (int8_t *)next; next += (GEMM_M * GEMM_K + 63u) & ~63u;
    b = (int8_t *)next; next += (GEMM_K * GEMM_N + 63u) & ~63u;
    c = (int32_t *)next; next += 4u * GEMM_M * GEMM_N;
    const uintptr_t hot_end = ((uintptr_t)(&slots + 1) + 63u) & ~(uintptr_t)63u;     // the globals' end
    slots = (uint8_t *)(((uintptr_t)next + SLOT - 1u) & ~(uintptr_t)(SLOT - 1u)) + (hot_end & (SLOT - 1u));
}

static uint32_t random_state = 0x2545F491u;
static uint32_t next_random(void) {
    random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
    return random_state;
}

// B's column blocks [first, end) (four columns each) into their slots: 19.4's pack_b, for a range
static void pack_blocks(uint32_t first, uint32_t end) {
    const uint32_t words = GEMM_K / 4u, per_group = GROUP_COLUMNS / 4u;
    for (uint32_t blk = first; blk < end; ++blk) {
        const uint32_t j = 4u * blk;
        uint32_t *out = (uint32_t *)(slots + (blk / per_group) * SLOT + (blk % per_group) * 16u * words);
        const uint8_t *row = (const uint8_t *)b + j;
        for (uint32_t w = 0; w < words; ++w, row += 4u * GEMM_N) {
            const uint32_t r0 = *(const uint32_t *)row, r1 = *(const uint32_t *)(row + GEMM_N);
            const uint32_t r2 = *(const uint32_t *)(row + 2u * GEMM_N), r3 = *(const uint32_t *)(row + 3u * GEMM_N);
            const uint32_t t0 = (r0 & 0x00FF00FFu) | ((r1 << 8) & 0xFF00FF00u);
            const uint32_t t1 = ((r0 >> 8) & 0x00FF00FFu) | (r1 & 0xFF00FF00u);
            const uint32_t t2 = (r2 & 0x00FF00FFu) | ((r3 << 8) & 0xFF00FF00u);
            const uint32_t t3 = ((r2 >> 8) & 0x00FF00FFu) | (r3 & 0xFF00FF00u);
            out[4 * w + 0] = (t0 & 0xFFFFu) | (t2 << 16);
            out[4 * w + 1] = (t1 & 0xFFFFu) | (t3 << 16);
            out[4 * w + 2] = (t0 >> 16) | (t2 & 0xFFFF0000u);
            out[4 * w + 3] = (t1 >> 16) | (t3 & 0xFFFF0000u);
        }
    }
}

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
#define FIRST_FOUR_K LOADS("0", "0") \
    DOT8("%[c00]", "t3", "a6") DOT8("%[c10]", "t4", "a6") DOT8("%[c20]", "t5", "a6") DOT8("%[c30]", "t6", "a6") \
    "lw a6, 8(%[pb])\n\t" \
    DOT8("%[c01]", "t3", "a7") DOT8("%[c11]", "t4", "a7") DOT8("%[c21]", "t5", "a7") DOT8("%[c31]", "t6", "a7") \
    "lw a7, 12(%[pb])\n\t" \
    DOT8("%[c02]", "t3", "a6") DOT8("%[c12]", "t4", "a6") DOT8("%[c22]", "t5", "a6") DOT8("%[c32]", "t6", "a6") \
    DOT8("%[c03]", "t3", "a7") DOT8("%[c13]", "t4", "a7") DOT8("%[c23]", "t5", "a7") DOT8("%[c33]", "t6", "a7")

// C's rows [i0, i1) (multiples of four): 19.4's gemm_fixed, for a range of rows
static void gemm_rows(uint32_t i0, uint32_t i1) {
    const uint8_t *slot = slots;
    for (uint32_t j0 = 0; j0 < GEMM_N; j0 += GROUP_COLUMNS, slot += SLOT)
        for (uint32_t i = i0; i < i1; i += 4u) {
            const uint8_t *rows = (const uint8_t *)a + i * GEMM_K;
            const uint32_t *b_block = (const uint32_t *)slot;
            for (uint32_t j = j0; j < j0 + GROUP_COLUMNS && j < GEMM_N; j += 4u, b_block += GEMM_K) {
                const uint8_t *pa = rows, *end = rows + GEMM_K;
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
                    : [end] "r"(end), [k1] "i"(GEMM_K), [k2] "i"(2u * GEMM_K), [k3] "i"(3u * GEMM_K)
                    : "t0", "t1", "t3", "t4", "t5", "t6", "a6", "a7", "memory");
                int32_t *row = c + i * GEMM_N + j;
                row[0] = c00; row[1] = c01; row[2] = c02; row[3] = c03; row += GEMM_N;
                row[0] = c10; row[1] = c11; row[2] = c12; row[3] = c13; row += GEMM_N;
                row[0] = c20; row[1] = c21; row[2] = c22; row[3] = c23; row += GEMM_N;
                row[0] = c30; row[1] = c31; row[2] = c32; row[3] = c33;
            }
        }
}

#define BLOCKS (GEMM_N / 4u)
#if GEMM_WORKERS == 2
static volatile uint32_t packed[2];                   // each hart's half of B packed, in this pass (its number)
static volatile uint32_t pass_number;

static void hart1_half(void *arg) {
    (void)arg;
    const uint32_t pass = pass_number;
    v12_work_start(1);
    pack_blocks(BLOCKS / 2u, BLOCKS);
    __asm__ volatile ("fence rw, rw" ::: "memory");
    packed[1] = pass;
    while (packed[0] != pass) {}                       // the barrier: all of B packed
    __asm__ volatile ("fence rw, rw" ::: "memory");
    gemm_rows(GEMM_M / 2u, GEMM_M);
    v12_work_end(1);
}
#endif

static void run(uint32_t pass) {
#if GEMM_WORKERS == 2
    pass_number = pass;
    __asm__ volatile ("fence rw, rw" ::: "memory");
    aster_smp_dispatch(hart1_half, 0);
    v12_work_start(0);
    pack_blocks(0, BLOCKS / 2u);
    __asm__ volatile ("fence rw, rw" ::: "memory");
    packed[0] = pass;
    while (packed[1] != pass) {}
    __asm__ volatile ("fence rw, rw" ::: "memory");
    gemm_rows(0, GEMM_M / 2u);
    v12_work_end(0);
    aster_smp_join();
#else
    (void)pass;
    v12_work_start(0);
    pack_blocks(0, BLOCKS);
    gemm_rows(0, GEMM_M);
    v12_work_end(0);
#endif
}

// B's packed column blocks [first, end) and C's rows [i0, i1) poisoned, by the hart that writes them
static void poison_part(uint32_t first, uint32_t end, uint32_t i0, uint32_t i1) {
    const uint32_t words = GEMM_K / 4u, per_group = GROUP_COLUMNS / 4u;
    for (uint32_t blk = first; blk < end; ++blk)
        matrix_poison(slots + (blk / per_group) * SLOT + (blk % per_group) * 16u * words, 16u * words);
    matrix_poison(c + i0 * GEMM_N, 4u * (i1 - i0) * GEMM_N);
}

#if GEMM_WORKERS == 2
static void hart1_poison(void *packing) {
    poison_part(packing ? BLOCKS / 2u : 0, packing ? BLOCKS : 0, GEMM_M / 2u, GEMM_M);
}
#endif

static void poison(int packing) {                      // (matrix_window.h: each hart its own lines)
#if GEMM_WORKERS == 2
    aster_smp_dispatch(hart1_poison, (void *)(uintptr_t)packing);
    poison_part(0, packing ? BLOCKS / 2u : 0, 0, GEMM_M / 2u);
    aster_smp_join();
#else
    poison_part(0, packing ? BLOCKS : 0, 0, GEMM_M);
#endif
}

// the kernel window's: B packed outside it, by the e2e window's split
#if GEMM_WORKERS == 2
static void hart1_pack(void *arg) { (void)arg; pack_blocks(BLOCKS / 2u, BLOCKS); }

static void hart1_rows(void *arg) {
    (void)arg;
    v12_work_start(1);
    gemm_rows(GEMM_M / 2u, GEMM_M);
    v12_work_end(1);
}
#endif

static void run_kernel(void) {
#if GEMM_WORKERS == 2
    aster_smp_dispatch(hart1_pack, 0);
    pack_blocks(0, BLOCKS / 2u);
    aster_smp_join();
    poison(0);
    matrix_arm(hart1_rows, 0);
    matrix_open(1);
    v12_work_start(0);
    matrix_release();
    gemm_rows(0, GEMM_M / 2u);
    matrix_await();
    v12_work_end(0);
    matrix_close();
    aster_smp_join();
#else
    pack_blocks(0, BLOCKS);
    poison(0);
    matrix_open(1);
    v12_work_start(0);
    gemm_rows(0, GEMM_M);
    v12_work_end(0);
    matrix_close();
#endif
}

static void emit(struct v12_record *record, const char *window) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < GEMM_M * GEMM_N; ++i) sum = (sum * 33u) ^ (uint32_t)c[i];
    // a spot check in the firmware (the oracle checks all of C): C's first and last element
    int32_t first = 0, last = 0;
    for (uint32_t k = 0; k < GEMM_K; ++k) {
        first += (int32_t)a[k] * (int32_t)b[k * GEMM_N];
        last += (int32_t)a[(GEMM_M - 1u) * GEMM_K + k] * (int32_t)b[k * GEMM_N + GEMM_N - 1u];
    }
    static char name[32] = "gemm_dot8_";
    if (!name[10]) {   // gemm_dot8_<M>x<N>x<K>
        char *p = name + 10;
        const uint32_t dims[3] = {GEMM_M, GEMM_N, GEMM_K};
        for (int d = 0; d < 3; ++d) {
            char digits[8]; int n = 0; uint32_t v = dims[d];
            do { digits[n++] = (char)('0' + v % 10u); v /= 10u; } while (v);
            while (n) *p++ = digits[--n];
            if (d < 2) *p++ = 'x';
        }
        *p = 0;
    }
    record->name = name;
    record->family = "coherence";
    record->method = GEMM_WORKERS == 2 ? "multicore" : "dot8";
    record->window = window;
    record->cache_state = MATRIX_CACHE_STATE;
    record->size = GEMM_M * GEMM_N; record->iterations = 1; record->param = GEMM_K;
    record->seed = 0x2545F491u; record->checksum = sum; record->workers = GEMM_WORKERS;
    record->pass = c[0] == first && c[GEMM_M * GEMM_N - 1u] == last;
    v12_emit(record);
}

int main(void) {
    static struct v12_record record;
    carve();
    for (uint32_t i = 0; i < GEMM_M * GEMM_K; ++i) a[i] = (int8_t)next_random();
    for (uint32_t i = 0; i < GEMM_K * GEMM_N; ++i) b[i] = (int8_t)next_random();
#if GEMM_WORKERS == 2
    aster_smp_start();                                 // (released and ready before the window)
#endif
    if (!matrix_cold) run(1);                          // the warm-up pass
    poison(1);
    v12_prepare();
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_START;
    run(2);
    *(volatile uint32_t *)(uintptr_t)ASTER_ABI4_COMMAND = ASTER_ABI4_FREEZE;
    v12_end(&record);
    emit(&record, "e2e");
    int failed = !record.pass;
    if (!matrix_cold) {
        v12_prepare();
        run_kernel();
        v12_end(&record);
        emit(&record, "kernel");
        failed |= !record.pass;
    }
    return failed;
}
