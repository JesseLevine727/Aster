// Phase 20.4: the one-hart build (HARTS = 1, a variant of 20.4's matrix, docs/matrix.md §2). What the
// parameter changes, checked: the hart count reads 1; SECONDARY_RUN stays 0 whatever hart 0 writes, so hart
// 1 is never released; and in a v12 window hart 1 retires nothing and the fabric sees nothing of it (its
// I and D ports accept nothing). Two v12 records (cold, then a kernel window) for scripts/soc_tests.py to
// validate, as soc_v12.c's. Ends with 1 to tohost (pass) or (first failing check << 1) | 1.
#include <stdint.h>

#include "aster.h"
#include "asterbench_v12.h"

static int status;
static void check(int ok, int code) { if (!ok && !status) status = code; }

static uint32_t words[1024];
static uint32_t mix(uint32_t x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; return x; }

static uint32_t scalar_checksum(void) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 1024; ++i) sum = sum * 31u + words[i];
    return sum;
}

static void window(struct v12_record *r, const char *name, const char *window, const char *cache_state,
                   uint32_t want) {
    v12_begin();
    v12_work_start(0);
    const uint32_t got = scalar_checksum();
    v12_work_end(0);
    v12_end(r);
    check(got == want, 4);
    // hart 1 and its ports, silent
    check(r->hart[1].counter[ASTER_C_RETIRED] == 0 && r->hart[1].counter[ASTER_C_I_ACCESSES] == 0, 5);
    check(r->fabric[ASTER_F_ACCEPTED + ASTER_REQ_I1] == 0 && r->fabric[ASTER_F_ACCEPTED + ASTER_REQ_D1] == 0, 6);
    check(r->hart[1].dot8[0] == 0 && r->hart[1].dot8[3] == 0, 7);
    r->name = name; r->family = "cpu"; r->method = "scalar"; r->window = window; r->cache_state = cache_state;
    r->size = sizeof words; r->iterations = 1; r->param = 0; r->seed = 0x5eed0001u; r->checksum = got;
    r->workers = 1; r->pass = got == want;
    v12_emit(r);
}

int main(void) {
    static struct v12_record r;
    for (uint32_t i = 0; i < 1024; ++i) words[i] = mix(i + 1);
    uint32_t want = 0;
    for (uint32_t i = 0; i < 1024; ++i) want = want * 31u + mix(i + 1);

    window(&r, "h1_selftest_cold", "e2e", "cold", want);
    check(ASTER_REG32(ASTER_HART_COUNT) == 1u, 1);
    ASTER_REG32(0x20002004u) = 1u;                          // SECONDARY_RUN: ignored with one hart
    for (volatile uint32_t i = 0; i < 200; ++i) {}
    check(ASTER_REG32(0x20002004u) == 0u, 2);
    window(&r, "h1_selftest_kernel", "kernel", "warm", want);
    check(r.hart[1].counter[ASTER_C_RETIRED] == 0, 3);      // (nothing ran, released or not)

    aster_puts(status ? "SOC H1 FAIL check=" : "SOC H1 PASS");
    if (status) aster_put_u32((uint32_t)status);
    aster_putc('\n');
    return status;
}
