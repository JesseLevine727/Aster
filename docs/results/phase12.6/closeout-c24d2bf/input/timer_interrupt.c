// Phase 12.6 interrupt firmware test. Enables the controller, takes a software
// interrupt and a machine-timer interrupt on hart 0, and confirms the handler
// services and clears each source while the interrupted loop resumes.
#include "aster.h"

static void check(int condition, unsigned line) {
    if (!condition) {
        aster_puts("TIMER IRQ FAIL line=");
        aster_put_u32(line);
        aster_putc('\n');
        for (;;) __asm__ volatile ("" ::: "memory");
    }
}
#define CHECK(condition) check((condition), __LINE__)

volatile uint32_t irq_count;
volatile uint32_t irq_seen;
volatile uint32_t loop_ticks;
volatile uint32_t irq_time_lo;
volatile uint32_t irq_time_hi;

void aster_irq_dispatch(void) {
    const uint32_t pending = *ASTER_IRQ_PENDING;
    irq_seen |= pending;
    if (pending & ASTER_IRQ_TIMER) {
        const uint64_t now = aster_counter_value(aster_timer_read_time());
        irq_time_lo = (uint32_t)now;
        irq_time_hi = (uint32_t)(now >> 32);
        aster_timer_control(1, 1);
    }
    *ASTER_IRQ_PENDING = pending;
    ++irq_count;
}

void aster_secondary_main(void) {
    for (;;) __asm__ volatile ("" ::: "memory");
}

int main(void) {
    const uint32_t harts = *(volatile uint32_t *)0x20002008u;
    CHECK(harts == 1 || harts == 2);
    CHECK(*ASTER_IRQ_ABI == 1u);
    CHECK(*ASTER_IRQ_SOURCES == 4u);
    CHECK(*ASTER_IRQ_ENABLE0 == 0u && *ASTER_IRQ_ENABLE1 == 0u);
    CHECK(*ASTER_IRQ_PENDING == 0u);

    // Software interrupt round-trip on hart 0.
    *ASTER_IRQ_ENABLE0 = ASTER_IRQ_SOFTWARE;
    aster_irq_unmask();
    const uint32_t before_software = irq_count;
    aster_irq_raise(ASTER_IRQ_SOFTWARE);
    while (irq_count == before_software) ++loop_ticks;
    CHECK((irq_seen & ASTER_IRQ_SOFTWARE) != 0u);
    CHECK(irq_count == before_software + 1u);
    CHECK(*ASTER_IRQ_PENDING == 0u);
    CHECK(*ASTER_IRQ_ACTIVE0 == 0u);
    *ASTER_IRQ_ENABLE0 = 0u;

    // Machine-timer interrupt on hart 0.
    aster_timer_control(0, 1);
    const uint64_t start = aster_counter_value(aster_timer_read_time());
    struct aster_perf_counter deadline;
    const uint64_t deadline_value = start + 20000u;
    deadline.lo = (uint32_t)deadline_value;
    deadline.hi = (uint32_t)(deadline_value >> 32);
    aster_timer_arm(deadline);
    aster_timer_control(1, 0);
    *ASTER_IRQ_ENABLE0 = ASTER_IRQ_TIMER;
    const uint32_t before_timer = irq_count;
    while (irq_count == before_timer) ++loop_ticks;
    CHECK((irq_seen & ASTER_IRQ_TIMER) != 0u);
    CHECK(irq_count == before_timer + 1u);
    CHECK((aster_timer_status() & 1u) == 0u);
    CHECK(*ASTER_IRQ_PENDING == 0u);
    const uint64_t fired = aster_counter_value(aster_timer_read_time());
    CHECK(fired >= deadline_value);
    // The handler context save/restore runs through the coherent fabric, so the
    // observed delivery latency is bounded but not tiny.
    CHECK(fired - deadline_value < 8192u);
    *ASTER_IRQ_ENABLE0 = 0u;
    aster_timer_control(0, 0);

    // The interrupted loops ran and resumed.
    CHECK(loop_ticks > 0u);
    CHECK(irq_count == 2u);
    CHECK(irq_seen == (ASTER_IRQ_SOFTWARE | ASTER_IRQ_TIMER));
    const uint64_t handled = ((uint64_t)irq_time_hi << 32) | irq_time_lo;
    aster_puts("TIMER IRQ PASS latency=");
    aster_put_u32((uint32_t)(handled - deadline_value));
    aster_putc('\n');
    return 0;
}
