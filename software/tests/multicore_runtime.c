#include "aster_multicore.h"

static volatile uint32_t initialized = 0x13579bdf;
static volatile uint8_t odd_data[3] = {0x12, 0x56, 0x9a};
static volatile uint32_t zero_data[7];
static volatile uint32_t private0[16] ASTER_PRIVATE0;
static volatile uint32_t private1[16] ASTER_PRIVATE1;
static volatile uint32_t secondary_ok;
static volatile uint32_t payload, result;
static volatile uint32_t primary_canary;

static uint32_t stack_ok(unsigned hart) {
    uintptr_t sp;
    __asm__ volatile ("mv %0,sp" : "=r"(sp));
    const uint32_t top = hart ? 0x10010000 : 0x1000c000;
    return (sp & 15u) == 0 && sp >= top-4096 && sp < top;
}
__attribute__((noinline)) static uint32_t stack_sum(uint32_t n) {
    volatile uint32_t local[7];
    local[0] = n;
    if (n == 0) return local[0];
    return local[0] + stack_sum(n-1);
}
static void check(uint32_t condition) {
    if (!condition) {
        aster_puts("MULTICORE RUNTIME FAIL\n");
        for (;;) {}
    }
}

void aster_secondary_main(void) {
    uint32_t ok = *ASTER_HART_ID == 1 && stack_ok(1) && stack_sum(8) == 36;
    // Primary intentionally changed these after its initialization. A secondary
    // re-running shared data/BSS initialization would destroy the values.
    ok &= initialized == 0x2468ace0 && primary_canary == 0xfaceb00c;
    for (unsigned i = 0; i != 16; ++i) ok &= private1[i] == 0;
    private1[0] = 0x87654321;
    // Denied private writes must not corrupt hart 0's resident cache line or
    // backing storage; denied loads cannot expose it.
    ok &= private0[0] == 0;
    private0[0] = 0;
    secondary_ok = ok;
    aster_publish(ASTER_TO_HART0, 0x80000000);
    uint32_t last = 0;
    for (;;) {
        const uint32_t job = aster_observe(ASTER_TO_HART1);
        if (job == last || job == 0) continue;
        last = job;
        result = payload * 7u + private1[0];
        aster_publish(ASTER_TO_HART0, job);
    }
}

int main(void) {
    check(*ASTER_HART_ID == 0 && stack_ok(0) && stack_sum(8) == 36);
    check(initialized == 0x13579bdf && odd_data[0] == 0x12 && odd_data[1] == 0x56 && odd_data[2] == 0x9a);
    for (unsigned i = 0; i != 7; ++i) check(zero_data[i] == 0);
    for (unsigned i = 0; i != 16; ++i) check(private0[i] == 0);
    private0[0] = 0x12345678;
    initialized = 0x2468ace0;
    primary_canary = 0xfaceb00c;
    // Two release/reset rounds independently clear secondary private BSS but
    // preserve primary/shared state. Test repeated mailbox epochs in each round.
    if (*ASTER_HART_COUNT == 2) for (unsigned boot = 0; boot != 2; ++boot) {
        aster_secondary_release();
        while (aster_observe(ASTER_TO_HART0) != 0x80000000) {}
        check(secondary_ok && primary_canary == 0xfaceb00c && private0[0] == 0x12345678);
        check(private1[0] == 0);
        private1[0] = 0; // must not change the worker's cached/backing value
        for (uint32_t job = 1; job != 9; ++job) {
            payload = 0x123u * job;
            aster_publish(ASTER_TO_HART1, job);
            while (aster_observe(ASTER_TO_HART0) != job) {}
            check(result == payload*7u + 0x87654321);
        }
        aster_secondary_reset();
        check(*ASTER_TO_HART0 == 0 && *ASTER_TO_HART1 == 0);
    }
    // Poison live objects for the harness's next warm boot.
    zero_data[0] = 0xffffffff;
    odd_data[0] = 0xff;
    aster_puts("MULTICORE RUNTIME PASS\n");
    return 0;
}
