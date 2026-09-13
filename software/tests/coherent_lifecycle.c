#include "aster.h"
#include <stdatomic.h>

static _Atomic uint32_t requested_epoch, done_epoch, generations, worker_error;
static uint32_t payload[64];
static volatile uint32_t primary_reservation, secondary_reservation;
static volatile uint32_t primary_private[16] __attribute__((section(".private0")));
static volatile uint32_t secondary_private[16] __attribute__((section(".private1")));

static void check(int condition) {
    if (!condition) {
        aster_puts("COHERENT LIFECYCLE FAIL\n");
        __asm__ volatile ("ebreak");
        for (;;) {}
    }
}
static uint32_t pattern(unsigned epoch, unsigned index) {
    return 0x57a60000u ^ (epoch * 0x10309u) ^ (index * 0x10101u);
}
void aster_secondary_main(void) {
    const unsigned epoch = atomic_load_explicit(&requested_epoch, memory_order_acquire);
    for (unsigned i = 0; i < 16; ++i)
        if (secondary_private[i] != 0) atomic_store(&worker_error, 1);
    for (unsigned i = 0; i < 16; ++i) secondary_private[i] = pattern(epoch, i);
    for (unsigned i = 0; i < 64; ++i) payload[i] = pattern(epoch, i);
    atomic_fetch_add_explicit(&generations, 1, memory_order_relaxed);
    // Primary's matching acquire makes payload publication a C11 synchronization
    // edge. Then leave a reservation alive while waiting for controlled reset.
    atomic_store_explicit(&done_epoch, epoch, memory_order_release);
    uint32_t old;
    __asm__ volatile ("lr.w %0, (%1)" : "=r"(old) : "r"(&secondary_reservation) : "memory");
    *(volatile uint32_t *)0x20002014u = epoch;
    for (;;) __asm__ volatile ("" ::: "memory");
}
int main(void) {
    const unsigned harts = *(volatile uint32_t *)0x20002008u;
    check(harts == 1 || harts == 2);
    check(*(volatile uint32_t *)0x20003084u == 4);
    for (unsigned i = 0; i < 16; ++i) primary_private[i] = 0x13570000u + i;
    volatile uint32_t stack_guard[8];
    for (unsigned i = 0; i < 8; ++i) stack_guard[i] = 0x24680000u + i;
    if (harts == 1) {
        *(volatile uint32_t *)0x20002004u = 1;
        check(*(volatile uint32_t *)0x20002004u == 0);
        check((*(volatile uint32_t *)0x2000200cu & 3) == 1);
    } else for (unsigned epoch = 1; epoch <= 8; ++epoch) {
        check(*(volatile uint32_t *)0x20002010u == 0 && *(volatile uint32_t *)0x20002014u == 0);
        atomic_store_explicit(&requested_epoch, epoch, memory_order_release);
        *(volatile uint32_t *)0x20002010u = epoch;
        *(volatile uint32_t *)0x20002004u = 1;
        while (atomic_load_explicit(&done_epoch, memory_order_acquire) != epoch) {}
        while (*(volatile uint32_t *)0x20002014u != epoch) {}
        check(!atomic_load(&worker_error));
        uint32_t old, failure;
        __asm__ volatile ("lr.w %0, (%1)" : "=r"(old) : "r"(&primary_reservation) : "memory");
        *(volatile uint32_t *)0x20002004u = 0;
        // Reading the lifecycle status may stall while both memory admissions
        // are quiesced, but the primary itself must never reset.
        while (*(volatile uint32_t *)0x2000200cu & 2) {}
        __asm__ volatile ("sc.w %0, %2, (%1)" : "=r"(failure)
                          : "r"(&primary_reservation), "r"(old + 1) : "memory");
        check(failure == 0 && primary_reservation == epoch);
        check(atomic_load(&generations) == epoch && *(volatile uint32_t *)0x20002014u == 0);
        for (unsigned i = 0; i < 64; ++i) check(payload[i] == pattern(epoch, i));
        for (unsigned i = 0; i < 16; ++i) check(primary_private[i] == 0x13570000u + i);
        for (unsigned i = 0; i < 8; ++i) check(stack_guard[i] == 0x24680000u + i);
    }
    aster_puts("COHERENT LIFECYCLE PASS\n");
    return 0;
}
