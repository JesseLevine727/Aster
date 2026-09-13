#include "aster_multicore.h"
static volatile uint32_t mode, bad;
static volatile uint32_t private0[16] ASTER_PRIVATE0;
static volatile uint32_t private1[16] ASTER_PRIVATE1;
#define GUARD ((volatile uint32_t *)0x10007000u)

static void fail(uint32_t reason) {
    aster_puts("MULTICORE FAULTS FAIL mode="); aster_put_hex32(mode);
    aster_puts(" reason="); aster_put_hex32(reason); aster_putc('\n');
    for (;;) {}
}
void aster_secondary_main(void) {
    if (*ASTER_HART_ID != 1 || private1[0] != 0) { bad = 1; for (;;) {} }
    private1[0] = 0x76543210;
    aster_publish(ASTER_TO_HART0, 0x600d0000u | mode);
    switch (mode) {
        case 0: __asm__ volatile ("ecall"); break;
        case 1: __asm__ volatile ("ebreak"); break;
        case 2: __asm__ volatile (".word 0"); break;
        case 3: __asm__ volatile ("li t0,0x10007001; lw t1,0(t0)" ::: "t0", "t1", "memory"); break;
        case 4: __asm__ volatile ("li t0,0x10007001; lh t1,0(t0)" ::: "t0", "t1", "memory"); break;
        case 5: __asm__ volatile ("li t0,0x10007001; sw zero,0(t0)" ::: "t0", "memory"); break;
        case 6: __asm__ volatile ("li t0,0x10007001; sh zero,0(t0)" ::: "t0", "memory"); break;
        case 7: ((void (*)(void))(uintptr_t)2)(); break;
        case 8: ((void (*)(void))(uintptr_t)0x20000000)(); break;
        case 9: ((void (*)(void))(uintptr_t)0x10008000)(); break;
        case 10: ((void (*)(void))(uintptr_t)0x10010000)(); break;
        // AMOADD.W is deliberately unsupported: Phase 5 is RV32IM, not RV32IMA.
        case 11: __asm__ volatile ("li a0,0x10007000; li a1,1; .word 0x00b5252f" ::: "a0", "a1", "memory"); break;
    }
    bad = 1; // returning from the offending operation must not be accepted
    for (;;) {}
}
int main(void) {
    if (*ASTER_HART_ID || *ASTER_HART_COUNT != 2) fail(1);
    // If the worker could execute hart 0's private RAM, this code would return
    // normally. The required illegal fetch therefore tests actual protection.
    private0[0] = 0x02a00513; // addi a0,zero,42
    private0[1] = 0x00008067; // ret
    for (unsigned test = 0; test != 12; ++test) {
        GUARD[0] = 0x11223344; GUARD[1] = 0x55667788;
        bad = 0; mode = test;
        aster_secondary_release();
        while (aster_observe(ASTER_TO_HART0) != (0x600d0000u | test)) if (bad) fail(2);
        while (!(*ASTER_HART_STATUS & 0x200)) if (bad) fail(3);
        // Substantial live primary work while hart 1 remains in sticky trap.
        for (unsigned i = 0; i != 128; ++i) private0[15] = private0[15] + i + 0x123;
        if ((*ASTER_HART_STATUS & 0x303) != 0x203 || bad || GUARD[0] != 0x11223344 || GUARD[1] != 0x55667788)
            fail(4);
        if (private0[0] != 0x02a00513 || private0[1] != 0x00008067) fail(5);
        aster_secondary_reset();
        if (*ASTER_HART_STATUS != 1 || *ASTER_TO_HART0 != 0) fail(6);
    }
    aster_puts("MULTICORE FAULTS PASS\n");
    return 0;
}
