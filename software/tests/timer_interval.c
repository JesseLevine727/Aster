// Phase 12.5 firmware interval test. Arms the machine timer for a known interval,
// confirms the exact match, and cross-checks elapsed cycles against the Phase 3
// performance counter over the same window.
#include "aster.h"

// The coherent performance block (ABI 4) keeps its control and identity
// registers above the counter bank, unlike the legacy minimal-SoC map.
#define COHERENT_PERF_CONTROL (*(volatile uint32_t *)0x20003080u)
#define COHERENT_PERF_CLOCK_HZ (*(volatile uint32_t *)0x20003088u)

static void check(int condition, unsigned line) {
    if (!condition) {
        aster_puts("TIMER FAIL line=");
        aster_put_u32(line);
        aster_putc('\n');
        for (;;) __asm__ volatile ("" ::: "memory");
    }
}
#define CHECK(condition) check((condition), __LINE__)

void aster_secondary_main(void) {
    for (;;) __asm__ volatile ("" ::: "memory");
}

static void spin(uint32_t iterations) {
    for (volatile uint32_t i = 0; i < iterations; ++i) __asm__ volatile ("" ::: "memory");
}

int main(void) {
    const uint32_t harts = *(volatile uint32_t *)0x20002008u;
    CHECK(harts == 1 || harts == 2);
    CHECK(*ASTER_TIMER_ABI == 1u);
    CHECK(*ASTER_TIMER_CLOCK_HZ == COHERENT_PERF_CLOCK_HZ);
    CHECK(*ASTER_TIMER_CLOCK_HZ == 31250000u);

    // Reset state is disabled, not pending, and TIME free-runs.
    CHECK((aster_timer_status() & 3u) == 0u);
    const uint64_t first = aster_counter_value(aster_timer_read_time());
    spin(200);
    const uint64_t second = aster_counter_value(aster_timer_read_time());
    CHECK(second > first);

    // A programmed interval matches exactly at TIME == COMPARE.
    aster_timer_control(0, 1);
    const uint64_t start = aster_counter_value(aster_timer_read_time());
    const uint64_t interval = 20000u;
    struct aster_perf_counter deadline;
    const uint64_t deadline_value = start + interval;
    deadline.lo = (uint32_t)deadline_value;
    deadline.hi = (uint32_t)(deadline_value >> 32);
    aster_timer_arm(deadline);
    aster_timer_control(1, 0);
    CHECK((aster_timer_status() & 2u) != 0u);
    while ((aster_timer_status() & 1u) == 0u) __asm__ volatile ("" ::: "memory");
    const uint64_t matched = aster_counter_value(aster_timer_read_time());
    CHECK(matched >= deadline_value);
    aster_puts("MATCH_LATENCY=");
    aster_put_u32((uint32_t)(matched - deadline_value));
    aster_putc('\n');
    CHECK(matched - deadline_value < 512u);

    // Clearing drops pending, and the elapsed compare never re-arms.
    aster_timer_control(1, 1);
    CHECK((aster_timer_status() & 1u) == 0u);
    spin(500);
    CHECK((aster_timer_status() & 1u) == 0u);

    // Disabling holds enabled low without disturbing a clean pending bit.
    aster_timer_control(0, 0);
    CHECK((aster_timer_status() & 3u) == 0u);

    // Cross-check the free-running timer against the Phase 3 cycle counter.
    COHERENT_PERF_CONTROL = 1u;
    const struct aster_perf_counter cycle_begin =
        aster_perf_read_counter(ASTER_PERF_CYCLE_LO, ASTER_PERF_CYCLE_HI);
    const uint64_t timer_begin = aster_counter_value(aster_timer_read_time());
    spin(200000);
    const uint64_t timer_end = aster_counter_value(aster_timer_read_time());
    const struct aster_perf_counter cycle_end =
        aster_perf_read_counter(ASTER_PERF_CYCLE_LO, ASTER_PERF_CYCLE_HI);
    const uint64_t timer_delta = timer_end - timer_begin;
    const uint64_t cycle_delta = aster_counter_value(cycle_end) - aster_counter_value(cycle_begin);
    CHECK(timer_delta > 100000u);
    CHECK(cycle_delta > 100000u);
    const uint64_t skew = timer_delta > cycle_delta ? timer_delta - cycle_delta : cycle_delta - timer_delta;
    CHECK(skew < 256u);

    aster_puts("TIMER PASS\n");
    return 0;
}
