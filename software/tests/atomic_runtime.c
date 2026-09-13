// Phase 6 uncached bring-up: real compiled RV32IMA, not a timing benchmark.
#include "aster.h"
#include <stdatomic.h>

static volatile uint32_t directed_word;
static _Atomic uint32_t start_epoch, done[2], counter, cas_counter, lock_word;
static volatile uint32_t lrsc_counter;
static uint32_t protected_counter, protected_sum;
volatile uint32_t probe_results[8] __attribute__((section(".private0")));

static void check(int condition) {
    if (!condition) {
        aster_puts("RV32A FAIL\n");
        __asm__ volatile ("ebreak");
        for (;;) {}
    }
}

#define AMO_INSN(NAME, ORDER) \
    __asm__ volatile (NAME ".w" ORDER " %0, %2, (%1)" \
                      : "=r"(old) : "r"(&directed_word), "r"(operand) : "memory")
#define AMO_CASE(NUMBER, NAME) \
    case NUMBER: \
        switch (order) { \
            case 0: AMO_INSN(NAME, ""); break; \
            case 1: AMO_INSN(NAME, ".rl"); break; \
            case 2: AMO_INSN(NAME, ".aq"); break; \
            default: AMO_INSN(NAME, ".aqrl"); break; \
        } \
        break

static uint32_t run_amo(unsigned op, unsigned order, uint32_t operand) {
    uint32_t old = 0;
    switch (op) {
        AMO_CASE(0, "amoswap"); AMO_CASE(1, "amoadd"); AMO_CASE(2, "amoxor");
        AMO_CASE(3, "amoand"); AMO_CASE(4, "amoor"); AMO_CASE(5, "amomin");
        AMO_CASE(6, "amomax"); AMO_CASE(7, "amominu"); AMO_CASE(8, "amomaxu");
    }
    return old;
}
static uint32_t reference(unsigned op, uint32_t old, uint32_t operand) {
    switch (op) {
        case 0: return operand;
        case 1: return old + operand;
        case 2: return old ^ operand;
        case 3: return old & operand;
        case 4: return old | operand;
        case 5: return (int32_t)old < (int32_t)operand ? old : operand;
        case 6: return (int32_t)old > (int32_t)operand ? old : operand;
        case 7: return old < operand ? old : operand;
        default: return old > operand ? old : operand;
    }
}
static void directed(void) {
    static const uint32_t values[] = {0, 1, 0xffffffffu, 0x7fffffffu, 0x80000000u, 0xaaaa5555u};
    check(atomic_is_lock_free(&counter));
    for (unsigned op = 0; op < 9; ++op) for (unsigned order = 0; order < 4; ++order)
    for (unsigned i = 0; i < 6; ++i) for (unsigned j = 0; j < 6; ++j) {
        directed_word = values[i];
        check(run_amo(op, order, values[j]) == values[i]);
        check(directed_word == reference(op, values[i], values[j]));
    }
    uint32_t old, failure;
    directed_word = 9;
    __asm__ volatile ("lr.w %0, (%2)\nsc.w %1, %0, (%2)"
                      : "=&r"(old), "=&r"(failure) : "r"(&directed_word) : "memory");
    check(old == 9 && failure == 0 && directed_word == 9);
    __asm__ volatile ("sc.w %0, zero, (%1)" : "=r"(failure) : "r"(&directed_word) : "memory");
    check(failure != 0 && directed_word == 9);
    // Overlapping same-value byte write must break the reservation.
    __asm__ volatile ("lr.w %0, (%2)\nsb %0, 0(%2)\nsc.w %1, zero, (%2)"
                      : "=&r"(old), "=&r"(failure) : "r"(&directed_word) : "memory");
    check(failure != 0 && directed_word == 9);
    aster_puts("RV32A DIRECTED PASS\n");
}

static void lrsc_increment(void) {
    uint32_t old, next, failure;
    // Four-instruction constrained LR/SC loop; no intervening memory access,
    // fence, M instruction, or backward branch before SC.
    __asm__ volatile ("1: lr.w.aq %0, (%3)\naddi %1, %0, 1\n"
                      "sc.w.rl %2, %1, (%3)\nbnez %2, 1b"
                      : "=&r"(old), "=&r"(next), "=&r"(failure)
                      : "r"(&lrsc_counter) : "memory");
}
static void worker(unsigned hart) {
    for (unsigned i = 0; i < 128; ++i) {
        atomic_fetch_add_explicit(&counter, 1, memory_order_relaxed);
        lrsc_increment();
        uint32_t old = atomic_load_explicit(&cas_counter, memory_order_relaxed);
        while (!atomic_compare_exchange_weak_explicit(&cas_counter, &old, old + 1,
                                                      memory_order_acq_rel, memory_order_relaxed)) {}
        while (atomic_exchange_explicit(&lock_word, 1, memory_order_acquire)) {}
        ++protected_counter;
        protected_sum += (hart + 1) * (i + 1);
        atomic_store_explicit(&lock_word, 0, memory_order_release);
    }
}
void aster_secondary_main(void) {
    for (unsigned job = 1; job <= 3; ++job) {
        while (atomic_load_explicit(&start_epoch, memory_order_acquire) != job) {}
        worker(1);
        atomic_store_explicit(&done[1], job, memory_order_release);
    }
}
int main(void) {
    directed();
    const unsigned harts = *(volatile uint32_t *)0x20002008u;
    check(harts == 1 || harts == 2);
    if (harts == 2) *(volatile uint32_t *)0x20002004u = 1;
    for (unsigned job = 1; job <= 3; ++job) {
        atomic_store_explicit(&counter, 0, memory_order_relaxed);
        atomic_store_explicit(&cas_counter, 0, memory_order_relaxed);
        lrsc_counter = protected_counter = protected_sum = 0;
        atomic_store_explicit(&start_epoch, job, memory_order_release);
        worker(0);
        atomic_store_explicit(&done[0], job, memory_order_release);
        if (harts == 2) while (atomic_load_explicit(&done[1], memory_order_acquire) != job) {}
        const unsigned expected = 128 * harts;
        const unsigned sum = (128 * 129 / 2) * (harts == 2 ? 3 : 1);
        check(atomic_load(&counter) == expected && lrsc_counter == expected &&
              atomic_load(&cas_counter) == expected && protected_counter == expected && protected_sum == sum);
        probe_results[0] = job;
        probe_results[1] = atomic_load(&counter);
        probe_results[2] = lrsc_counter;
        probe_results[3] = atomic_load(&cas_counter);
        probe_results[4] = protected_counter;
        probe_results[5] = protected_sum;
        probe_results[6] = atomic_is_lock_free(&counter);
        probe_results[7] = harts;
        aster_puts("RV32A JOB PASS\n");
    }
    return 0;
}
