// Phase 20.2: the two-hart SoC's devices (docs/soc.md §3, §7, §8), on its
// device build. Each check that fails sets a distinct status (the failing
// check's number), and the program prints one line per group:
//  1-9    hart control: ID per hart, HART_COUNT, STATUS before and after the
//         release, the mailboxes (each written by its sender only, both
//         cleared when hart 0 holds hart 1), hart 1's write to SECONDARY_RUN
//         ignored;
// 10-19   the timer (ABI 1): its ABI and clock words, its count against the
//         cores' time CSR, its compare interrupt to hart 0 through the
//         interrupt controller, taken at mtvec, not before the compare;
// 20-29   the interrupt controller (ABI 1): its ABI and source count, the
//         software source raised to hart 1 only, ACTIVE0/ACTIVE1, PENDING
//         cleared by writing 1;
// 30-49   the counters, ABI 4 (per hart) and the DOT8 and fabric counters,
//         over one window opened with START and closed with FREEZE:
//         metadata; hart 0's retired instructions within a tolerance of the
//         window's code; exact counts of hart 0's AMOs, successful and failed
//         sc's and dot8 instructions, and hart 1's dot8 instructions; the
//         lines hart 0's stores invalidate in hart 1's cache; the fabric's
//         AMOs and the reservation it saw ended; counting stopped after
//         FREEZE and resumed by RESUME; hart 1's command ignored, and a
//         command word other than 1, 2 or 4;
// 50-59   word-only device pages: a byte store, a halfword load and an AMO to
//         device pages each fault (store/AMO or load access fault), and the
//         device is unchanged; a load and a store outside the mapped pages
//         (0x2000_5000, 0x2000_8000) fault;
// 60-69   the NPU on the device build: a GEMM job, its completion interrupt
//         (source 2) to hart 0 (the testbench checks its result).
// Ends with 1 to tohost (pass) or (first failing check << 1) | 1.
#include <stdint.h>

#include "aster.h"
#include "aster_multicore.h"
#include "aster_npu2.h"

#define DEV(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
#define PERF(hart, offset) DEV(0x20003000u + 0x100u * (hart) + (offset))
#define DOT8_CTR(hart, event) DEV(0x20003200u + 8u * (4u * (hart) + (event)))
#define FABRIC_CTR(k) DEV(0x20003300u + 8u * (k))
#define PERF_COMMAND DEV(0x20003080u)

static int status;
static void check(int ok, int code) { if (!ok && !status) status = code; }

// ---- traps: exceptions skip the faulting instruction; interrupts by source ----
static volatile uint32_t last_cause, faults, timer_irqs, soft_irqs1, npu_irqs, active1_seen;
static volatile uint32_t timer_irq_time, npu_done;

void aster_exception(uint32_t mcause, uint32_t mepc, uint32_t mtval) {
    (void)mtval;
    last_cause = mcause;
    ++faults;
    __asm__ volatile ("csrw mepc, %0" :: "r"(mepc + 4u));
}

void aster_irq_dispatch(void) {
    uint32_t hart;
    __asm__ volatile ("csrr %0, mhartid" : "=r"(hart));
    if (hart == 0) {
        const uint32_t active = *ASTER_IRQ_ACTIVE0;
        if (active & ASTER_IRQ_TIMER) {
            timer_irq_time = *ASTER_TIMER_TIME_LO;
            aster_timer_control(0u, 1u);                   // disable, clear pending
            *ASTER_IRQ_PENDING = ASTER_IRQ_TIMER;
            ++timer_irqs;
        }
        if (active & ASTER_IRQ_NPU) {
            npu_done = ASTER_NPU2_REG(ASTER_NPU2_STATUS);
            aster_npu2_ack();
            *ASTER_IRQ_PENDING = ASTER_IRQ_NPU;
            ++npu_irqs;
        }
    } else {
        const uint32_t active = *ASTER_IRQ_ACTIVE1;
        active1_seen = active;
        if (active & ASTER_IRQ_SOFTWARE) {
            *ASTER_IRQ_PENDING = ASTER_IRQ_SOFTWARE;
            ++soft_irqs1;
        }
    }
}

static inline void irq_unmask(void) {
    __asm__ volatile ("csrs mie, %0" :: "r"(1u << 11));
    __asm__ volatile ("csrsi mstatus, 8");
}

static inline uint32_t dot8(uint32_t a, uint32_t b) {
    uint32_t r;
    __asm__ volatile (".insn r 0x0b, 0, 0, %0, %1, %2" : "=r"(r) : "r"(a), "r"(b));
    return r;
}

// ---- hart 1: identity, mailbox, the software interrupt, dot8s, a cached line ----
#define HART1_DOT8S 37u
struct line { volatile uint32_t word; uint32_t pad[3]; } __attribute__((aligned(16)));
static struct line shared_lines[16];
static struct line resv_word, phase, hart1_id, hart1_status;
static volatile uint32_t hart1_dot8_result;

void aster_secondary_main(void) {
    hart1_id.word = *ASTER_HART_ID;
    *ASTER_SECONDARY_RUN = 0u;                       // ignored: hart 0's stores only
    *ASTER_TO_HART0 = 0x100u + hart1_id.word;
    *ASTER_TO_HART1 = 0x77u;                         // ignored: hart 0 writes TO_HART1
    irq_unmask();
    phase.word = 1u;
    // window phase: dot8s, cache the shared lines (hart 0 then invalidates them), hold a reservation
    while (phase.word != 2u) {}
    uint32_t acc = 0;
    for (uint32_t i = 0; i < HART1_DOT8S; ++i) acc += dot8(i * 0x01010101u, 0x01020304u);
    hart1_dot8_result = acc;
    for (unsigned i = 0; i < 16; ++i) (void)shared_lines[i].word;
    uint32_t reserved;
    __asm__ volatile ("lr.w %0, (%1)" : "=r"(reserved) : "r"(&resv_word.word) : "memory");
    PERF_COMMAND = 2u;                               // ignored: hart 0's command only
    phase.word = 3u;
    while (phase.word != 4u) {}
    uint32_t fail;
    __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(fail) : "r"(&resv_word.word), "r"(reserved + 1u) : "memory");
    hart1_status.word = fail;                        // must fail: hart 0 wrote the word
    phase.word = 5u;
    for (;;) {}
}

static uint32_t counter_lo(volatile uint32_t *lo) { return *lo; }

int main(void) {
    // ---- hart control ----
    check(*ASTER_HART_ID == 0u, 1);
    check(*ASTER_HART_COUNT == 2u, 2);
    check(*ASTER_HART_STATUS == 1u, 3);
    *ASTER_TO_HART1 = 0xabcdu;
    check(*ASTER_TO_HART1 == 0xabcdu, 4);
    aster_secondary_release();
    while (phase.word != 1u) {}
    check(hart1_id.word == 1u, 5);
    check(*ASTER_TO_HART0 == 0x101u && *ASTER_TO_HART1 == 0xabcdu, 6);
    check(*ASTER_HART_STATUS == 3u, 7);
    check(*ASTER_SECONDARY_RUN == 1u, 8);
    aster_puts("SOC DEVICES hart control done\n");

    // ---- timer ----
    check(*ASTER_TIMER_ABI == 1u, 10);
    check(*ASTER_TIMER_CLOCK_HZ == 100000000u, 11);
    uint32_t t0 = *ASTER_TIMER_TIME_LO, csr_time;
    __asm__ volatile ("csrr %0, time" : "=r"(csr_time));
    const uint32_t t1 = *ASTER_TIMER_TIME_LO;
    check(t0 < csr_time && csr_time < t1 && t1 - t0 < 64u, 12);
    *ASTER_IRQ_ENABLE0 = ASTER_IRQ_TIMER | ASTER_IRQ_NPU;
    irq_unmask();
    const uint32_t deadline = *ASTER_TIMER_TIME_LO + 500u;
    *ASTER_TIMER_COMPARE_HI = 0u;
    *ASTER_TIMER_COMPARE_LO = deadline;
    aster_timer_control(1u, 1u);
    while (timer_irqs == 0u && (int32_t)(*ASTER_TIMER_TIME_LO - deadline) < 2000) {}
    check(timer_irqs == 1u, 13);
    // not before the compare; the handler's first entry refills its code (about 160 cycles in all)
    check((int32_t)(timer_irq_time - deadline) >= 0 && timer_irq_time - deadline < 1000u, 14);
    check((*ASTER_TIMER_STATUS & 1u) == 0u, 15);
    aster_puts("SOC DEVICES timer done irq_after_deadline=");
    aster_put_u32(timer_irq_time - deadline);
    aster_putc('\n');

    // ---- interrupt controller ----
    check(*ASTER_IRQ_ABI == 1u, 20);
    check(*ASTER_IRQ_SOURCES == 4u, 21);
    *ASTER_IRQ_ENABLE1 = ASTER_IRQ_SOFTWARE;
    *ASTER_IRQ_RAISE = 1u;
    while (soft_irqs1 == 0u) {}
    check(soft_irqs1 == 1u, 22);
    check(active1_seen == ASTER_IRQ_SOFTWARE, 23);
    check((*ASTER_IRQ_PENDING & ASTER_IRQ_SOFTWARE) == 0u, 24);
    check(*ASTER_IRQ_ACTIVE0 == 0u, 25);
    aster_puts("SOC DEVICES interrupts done\n");

    // ---- counters over one window ----
    check(PERF(0, 0x84) == 4u && PERF(1, 0x84) == 4u, 30);
    check(PERF(0, 0x88) == 100000000u && PERF(0, 0x9C) == 14u, 31);
    check(DEV(0x20003284u) == 6u && DEV(0x20003298u) == 2u, 32);
    check(DEV(0x200034F0u) == 1u && DEV(0x200034F4u) == 48u, 33);
    for (unsigned i = 0; i < 16; ++i) shared_lines[i].word = i;
    PERF_COMMAND = 1u;                               // START
    // the window's code: 100 AMOs, 7 successful and 3 failed sc's, 50 dot8s, 16 invalidating stores
    phase.word = 2u;
    uint32_t amo_acc = 0, dot_acc = 0;
    for (unsigned i = 0; i < 100; ++i)
        __asm__ volatile ("amoadd.w %0, %2, (%1)" : "=r"(amo_acc) : "r"(&shared_lines[15].word), "r"(1u) : "memory");
    for (unsigned i = 0; i < 7; ++i) {
        uint32_t v, fail;
        do {
            __asm__ volatile ("lr.w %0, (%1)" : "=r"(v) : "r"(&shared_lines[14].word) : "memory");
            __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(fail) : "r"(&shared_lines[14].word), "r"(v + 1u) : "memory");
        } while (fail);
    }
    for (unsigned i = 0; i < 3; ++i) {
        uint32_t fail;
        __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(fail) : "r"(&shared_lines[13].word), "r"(i) : "memory");
        check(fail != 0u, 34);
    }
    for (uint32_t i = 0; i < 50; ++i) dot_acc += dot8(i, 0x01010101u);
    while (phase.word != 3u) {}                      // hart 1 holds the lines and its reservation
    for (unsigned i = 0; i < 12; ++i) shared_lines[i].word = 100u + i;
    resv_word.word = 7u;                             // ends hart 1's reservation
    phase.word = 4u;
    while (phase.word != 5u) {}
    PERF_COMMAND = 2u;                               // FREEZE
    const uint32_t frozen_cycles = counter_lo(&PERF(0, 0x00));
    for (volatile unsigned i = 0; i < 20; ++i) {}
    check(counter_lo(&PERF(0, 0x00)) == frozen_cycles, 35);
    check(PERF(0, 0x80) == 0u, 36);
    check(PERF(0, 0x40) == 100u + 7u + 3u + 7u, 37);  // completed A instructions: the AMOs, lr's and sc's
    check(PERF(0, 0x48) == 7u, 38);                  // successful sc
    check(PERF(0, 0x50) == 3u, 39);                  // failed sc
    check(DOT8_CTR(0, 0) == 50u && DOT8_CTR(0, 2) == 50u && DOT8_CTR(0, 3) == 50u && DOT8_CTR(0, 1) == 0u, 40);
    check(DOT8_CTR(1, 0) == HART1_DOT8S && DOT8_CTR(1, 3) == HART1_DOT8S, 41);
    check(PERF(0, 0x60) >= 12u, 42);                 // lines hart 0's stores invalidated in hart 1's cache
    check(FABRIC_CTR(40) == 100u, 43);               // the fabric's AMOs
    check(FABRIC_CTR(39) >= 1u, 44);                 // hart 1's reservation ended by another requester
    check(hart1_status.word != 0u, 45);
    check(PERF(1, 0x08) > 0u && PERF(0, 0x08) > 200u, 46);
    check(FABRIC_CTR(1) > 0u && FABRIC_CTR(0) > 0u, 47);
    PERF_COMMAND = 3u;                               // not a command: ignored
    check(PERF(0, 0x80) == 0u && counter_lo(&PERF(0, 0x00)) == frozen_cycles, 49);
    PERF_COMMAND = 4u;                               // RESUME
    for (volatile unsigned i = 0; i < 20; ++i) {}
    check(counter_lo(&PERF(0, 0x00)) > frozen_cycles, 48);
    PERF_COMMAND = 2u;
    aster_puts("SOC DEVICES counters done");
    aster_puts(" retired="); aster_put_u32(PERF(0, 0x08));
    aster_puts(" cycles="); aster_put_u32(PERF(0, 0x00));
    aster_puts(" invalidations="); aster_put_u32(PERF(0, 0x60));
    aster_puts(" d0_accepted="); aster_put_u32(FABRIC_CTR(1));
    aster_puts(" longest_wait="); aster_put_u32(FABRIC_CTR(41));
    aster_putc('\n');
    (void)amo_acc; (void)dot_acc;

    // ---- word-only device pages ----
    const uint32_t compare_before = *ASTER_TIMER_COMPARE_LO;
    faults = 0;
    *(volatile uint8_t *)0x20001008u = 0x55u;        // a byte store to the timer's COMPARE
    check(faults == 1u && last_cause == 7u, 50);
    check(*ASTER_TIMER_COMPARE_LO == compare_before, 51);
    (void)*(volatile uint16_t *)0x20001000u;         // a halfword load of the timer
    check(faults == 2u && last_cause == 5u, 52);
    uint32_t old;
    __asm__ volatile ("amoadd.w %0, %2, (%1)" : "=r"(old) : "r"(ASTER_TIMER_COMPARE_LO), "r"(1u) : "memory");
    check(faults == 3u && last_cause == 7u, 53);
    check(*ASTER_TIMER_COMPARE_LO == compare_before, 54);
    (void)*(volatile uint32_t *)0x20005000u;         // outside the devices' pages
    check(faults == 4u && last_cause == 5u, 55);
    *(volatile uint32_t *)0x20008000u = 1u;
    check(faults == 5u && last_cause == 7u, 56);
    aster_puts("SOC DEVICES word-only done\n");

    // ---- the NPU and its interrupt ----
    static int8_t a[16 * 16] __attribute__((aligned(16))), b[16 * 16] __attribute__((aligned(16)));
    static int32_t c[16 * 16] __attribute__((aligned(16)));
    for (unsigned i = 0; i < 256; ++i) { a[i] = (int8_t)(i * 7u); b[i] = (int8_t)(i * 13u + 5u); }
    check(ASTER_NPU2_REG(ASTER_NPU2_ABI) == 2u, 60);
    const struct aster_npu2_job job = {a, b, c, 16, 16, 64, 16, 16, 16, 0, 0, 0, 0, 0};
    aster_npu2_start(&job);
    while (npu_irqs == 0u) {}
    check(npu_irqs == 1u && (npu_done & ASTER_NPU2_DONE), 61);
    int32_t expect = 0;
    for (unsigned k = 0; k < 16; ++k) expect += (int32_t)a[3 * 16 + k] * (int32_t)b[k * 16 + 5];
    check(c[3 * 16 + 5] == expect, 62);
    aster_puts("SOC DEVICES npu done\n");

    // ---- the mailboxes: each written by its sender only, both cleared by the hold ----
    *ASTER_TO_HART0 = 5u;                            // hart 0 cannot write TO_HART0
    check(*ASTER_TO_HART0 == 0x101u, 9);
    aster_secondary_reset();
    check(*ASTER_TO_HART0 == 0u && *ASTER_TO_HART1 == 0u && *ASTER_HART_STATUS == 1u, 9);

    aster_puts(status ? "SOC DEVICES FAIL check=" : "SOC DEVICES PASS");
    if (status) aster_put_u32((uint32_t)status);
    aster_putc('\n');
    return status;
}
