// Milestone 19.4's coherence tests (docs/npu.md §5.2, cpu.md §9) on the Phase
// 19 SoC, checked by the program itself from what it stored (the testbench
// checks each job against the memory at its START, which a late store would
// change with it):
// - stale lines: before each job the CPU reads C (its lines then cached; the
//   data cache allocates on loads only), and after it reads the NPU's values,
//   each word of C and of the padding between C's rows (the lines' other
//   words) — the snoops must have invalidated every line the NPU wrote;
// - racing reads: in half the jobs the CPU reads C over and over while the
//   job runs (refilling lines the NPU is writing), and between its passes
//   adds to a counter in main memory with an AMO and with lr/sc (the memory
//   side holds the NPU's port for each AMO's write), then the same checks and
//   the counter's total;
// - order across ports: A and B are stored just before START, with no fence
//   (the descriptor first, START right after A's and B's last store);
// - the interrupt (mip.MEIP) high at the end and low after ACK;
// - an error job (its code, nothing written) and an aborted job, each followed
//   by a job that must complete;
// - the reservation: an lr of a word the job then writes, and the sc after it
//   must fail (another device's write ends the reservation, the A extension),
//   the word keeping the NPU's value; an lr of a word the job does not write,
//   and its sc succeeds.
// Jobs of random shapes (M, N <= 20, K <= 40), strides and modes (tiles and
// K-split), one after another. One record, then main returns 0:
//   COHERENCE,jobs=..,racing=..,errors=..,aborts=..,amo_adds=..,reservation=1,status=PASS
#include <stdint.h>
#include "aster_npu2.h"
#include "npu2_soc.h"

enum { MAX_M = 20, MAX_N = 20, MAX_K = 40, JOBS = 160 };
static uint8_t arena[24 * 1024] __attribute__((aligned(64)));
static int8_t a_shadow[MAX_M * MAX_K], b_shadow[MAX_K * MAX_N];
static int32_t want[MAX_M * MAX_N];
static volatile uint32_t sink;
static uint32_t counter, counter_adds;       // the racing jobs' AMO and lr/sc adds

static void amo_adds(void) {
    __asm__ volatile ("amoadd.w zero, %1, (%0)" :: "r"(&counter), "r"(1u) : "memory");
    uint32_t value, failed;
    do {
        __asm__ volatile ("lr.w %0, (%1)" : "=r"(value) : "r"(&counter) : "memory");
        __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(failed) : "r"(&counter), "r"(value + 2u) : "memory");
    } while (failed);
    counter_adds += 3u;
}

static uint32_t random_state = 0x13579BDFu;
static uint32_t next_random(void) {
    random_state ^= random_state << 13; random_state ^= random_state >> 17; random_state ^= random_state << 5;
    return random_state;
}
static uint32_t below(uint32_t n) { return next_random() % n; }

static uint32_t mip(void) { uint32_t v; __asm__ volatile ("csrr %0, mip" : "=r"(v)); return v; }
static int meip_becomes(uint32_t level) {
    for (int tries = 0; tries < 64; ++tries)
        if (((mip() >> 11) & 1u) == level) return 1;
    return 0;
}

static uint32_t failures;
static void fail(const char *what, uint32_t job) {
    if (failures++ < 8) {
        npu2_puts("COHERENCE_FAIL,what="); npu2_puts(what); npu2_field("job", job); npu2_putc('\n');
    }
}

// The descriptor's registers, written without the driver's fences.
static void write_descriptor(const struct aster_npu2_job *job) {
    ASTER_NPU2_REG(ASTER_NPU2_A_BASE) = (uint32_t)(uintptr_t)job->a;
    ASTER_NPU2_REG(ASTER_NPU2_B_BASE) = (uint32_t)(uintptr_t)job->b;
    ASTER_NPU2_REG(ASTER_NPU2_C_BASE) = (uint32_t)(uintptr_t)job->c;
    ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE) = job->a_stride;
    ASTER_NPU2_REG(ASTER_NPU2_B_STRIDE) = job->b_stride;
    ASTER_NPU2_REG(ASTER_NPU2_C_STRIDE) = job->c_stride;
    ASTER_NPU2_REG(ASTER_NPU2_M) = job->m;
    ASTER_NPU2_REG(ASTER_NPU2_N) = job->n;
    ASTER_NPU2_REG(ASTER_NPU2_K) = job->k;
    ASTER_NPU2_REG(ASTER_NPU2_MODE) = job->mode;
    ASTER_NPU2_REG(ASTER_NPU2_A_M0) = 0; ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE_M1) = 0;
    ASTER_NPU2_REG(ASTER_NPU2_A_K0) = 0; ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE_K1) = 0;
}

static uint32_t padding_word(uint32_t job, uint32_t index) { return 0xC0DE0000u ^ (job << 20) ^ index; }

// One job of a random shape: 1 when it completed and checked.
static int random_job(uint32_t index, int racing) {
    const uint32_t m = 1u + below(MAX_M), n = 1u + below(MAX_N), k = 1u + below(MAX_K);
    const uint32_t mode = n == 1u ? below(3) : below(2);
    const uint32_t a_stride = k + below(4), b_stride = n + below(4), c_stride = 4u * (n + below(3));
    int8_t *a = (int8_t *)arena + below(3072);
    int8_t *b = (int8_t *)arena + 4096 + below(3072);
    uint32_t *c = (uint32_t *)(arena + 8192 + 4u * below(512));
    const uint32_t c_words = ((m - 1u) * c_stride) / 4u + n;          // C's span, its padding included

    for (uint32_t i = 0; i < m * k; ++i) a_shadow[i] = (int8_t)next_random();
    for (uint32_t i = 0; i < k * n; ++i) b_shadow[i] = (int8_t)next_random();
    for (uint32_t i = 0; i < m; ++i)
        for (uint32_t j = 0; j < n; ++j) {
            int32_t sum = 0;
            for (uint32_t kk = 0; kk < k; ++kk) sum += (int32_t)a_shadow[i * k + kk] * b_shadow[kk * n + j];
            want[i * n + j] = sum;
        }
    // C and its padding stored, then read (their lines cached).
    for (uint32_t w = 0; w < c_words; ++w) c[w] = padding_word(index, w);
    uint32_t touch = 0;
    for (uint32_t w = 0; w < c_words; ++w) touch += c[w];
    sink = touch;

    const struct aster_npu2_job job = {a, b, c, a_stride, b_stride, c_stride, m, n, k, mode, 0, 0, 0, 0};
    write_descriptor(&job);
    for (uint32_t i = 0; i < m; ++i)
        for (uint32_t kk = 0; kk < k; ++kk) a[i * a_stride + kk] = a_shadow[i * k + kk];
    for (uint32_t kk = 0; kk < k; ++kk)
        for (uint32_t j = 0; j < n; ++j) b[kk * b_stride + j] = b_shadow[kk * n + j];
    ASTER_NPU2_REG(ASTER_NPU2_CONTROL) = ASTER_NPU2_START;

    uint32_t status;
    if (racing) {
        do {
            for (uint32_t w = 0; w < c_words; ++w) touch += ((volatile uint32_t *)c)[w];
            amo_adds();
            status = ASTER_NPU2_REG(ASTER_NPU2_STATUS);
        } while (!(status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)));
        sink = touch;
    } else {
        do status = ASTER_NPU2_REG(ASTER_NPU2_STATUS);
        while (!(status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)));
    }
    if (status != ASTER_NPU2_DONE) { fail("status", index); return 0; }
    if (!meip_becomes(1u)) fail("meip_not_high", index);
    aster_npu2_ack();
    if (!meip_becomes(0u)) fail("meip_not_low", index);

    for (uint32_t i = 0; i < m; ++i)
        for (uint32_t j = 0; j < n; ++j)
            if ((int32_t)c[i * (c_stride / 4u) + j] != want[i * n + j]) { fail("c", index); return 0; }
    for (uint32_t i = 0; i + 1u < m; ++i)
        for (uint32_t w = i * (c_stride / 4u) + n; w < (i + 1u) * (c_stride / 4u); ++w)
            if (c[w] != padding_word(index, w)) { fail("padding", index); return 0; }
    return 1;
}

// lr, a 1 x 1 x 4 job writing C's word, then sc: it must fail when C is the
// reserved word, and succeed when the job writes another.
static uint32_t lr_job_sc(uint32_t *reserved, uint32_t *c, uint32_t value) {
    int8_t *a = (int8_t *)arena, *b = (int8_t *)arena + 4096;
    for (int i = 0; i < 4; ++i) { a[i] = (int8_t)(i + 1); b[i] = 1; }
    const struct aster_npu2_job job = {a, b, c, 4, 1, 4, 1, 1, 4, 0, 0, 0, 0, 0};
    uint32_t old, failed;
    __asm__ volatile ("lr.w %0, (%1)" : "=r"(old) : "r"(reserved) : "memory");
    aster_npu2_start(&job);
    aster_npu2_wait();
    aster_npu2_ack();
    __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(failed) : "r"(reserved), "r"(value) : "memory");
    sink = old;
    return failed;
}
static int reservation_test(void) {
    uint32_t *c = (uint32_t *)(arena + 8192);
    c[0] = 0; c[1] = 0x1111;
    if (lr_job_sc(&c[0], &c[0], 0x5555) == 0 || c[0] != 10u) { fail("sc_after_npu_write", 0); return 0; }
    if (lr_job_sc(&c[1], &c[0], 0x2222) != 0 || c[1] != 0x2222u) { fail("sc_control", 0); return 0; }
    return 1;
}

// A job that must end with an error: its code, and the words at C's address
// that are in memory untouched.
static int error_job(uint32_t index, uint32_t mode, uint32_t c_address, uint32_t expected_code) {
    uint32_t *c = (uint32_t *)(uintptr_t)c_address;
    c[0] = padding_word(index, 0); c[1] = padding_word(index, 1);
    sink = c[0];
    const struct aster_npu2_job job = {arena, arena + 4096, (void *)(uintptr_t)c_address, 4, 2, 8, 2, 2, 4,
                                       mode, 0, 0, 0, 0};
    aster_npu2_start(&job);
    const uint32_t status = aster_npu2_wait();
    const uint32_t code = ASTER_NPU2_REG(ASTER_NPU2_ERROR_CODE);
    if (status != ASTER_NPU2_ERROR || code != expected_code) { fail("error_code", index); return 0; }
    if (!meip_becomes(1u)) fail("meip_not_high", index);
    aster_npu2_ack();
    if (!meip_becomes(0u)) fail("meip_not_low", index);
    if (c[0] != padding_word(index, 0) || c[1] != padding_word(index, 1)) { fail("error_wrote", index); return 0; }
    return 1;
}

// A 64 x 64 x 64 job aborted while it runs.
static int aborted_job(uint32_t index, uint32_t spin) {
    const struct aster_npu2_job job = {arena, arena + 4096, arena + 8192, 64, 64, 256, 64, 64, 64, 0, 0, 0, 0, 0};
    aster_npu2_start(&job);
    for (uint32_t i = 0; i < spin; ++i) sink = i;
    ASTER_NPU2_REG(ASTER_NPU2_CONTROL) = ASTER_NPU2_ABORT;
    const uint32_t status = aster_npu2_wait();
    if (status != ASTER_NPU2_ABORTED) { fail("abort_status", index); return 0; }
    aster_npu2_ack();
    if (!meip_becomes(0u)) fail("meip_not_low", index);
    return 1;
}

int main(void) {
    if (ASTER_NPU2_REG(ASTER_NPU2_ABI) != 2u) return 1;
    uint32_t jobs = 0, racing = 0, errors = 0, aborts = 0, index = 0, reservation = 0;
    for (; index < JOBS; ++index) {
        const int race = (int)(index & 1u);
        if (random_job(index, race)) { ++jobs; racing += (uint32_t)race; }
        if (index == 40u) {               // a C past the window's end; then MODE 3
            errors += (uint32_t)error_job(1000u, 0, 0x80018000u - 8u, 4);
            errors += (uint32_t)error_job(1001u, 3, (uint32_t)(uintptr_t)(arena + 8192), 1);
        }
        if (index == 140u) reservation = (uint32_t)reservation_test();
        if (index == 80u || index == 120u)
            aborts += (uint32_t)aborted_job(2000u + index, index == 80u ? 50u : 300u);   // (mid-job on 8x8 too)
    }
    if (counter != counter_adds || counter_adds == 0) fail("amo_counter", counter);
    const int pass = failures == 0 && jobs == JOBS && errors == 2u && aborts == 2u && reservation == 1u;
    npu2_puts("COHERENCE");
    npu2_field("jobs", jobs); npu2_field("racing", racing); npu2_field("errors", errors); npu2_field("aborts", aborts);
    npu2_field("amo_adds", counter_adds); npu2_field("reservation", reservation);
    npu2_puts(pass ? ",status=PASS\n" : ",status=FAIL\n");
    return pass ? 0 : 2;
}
