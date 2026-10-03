// The CPU shell's exit for v1 firmware on the Aster core (milestone 18.6: the
// firmware regression): the ported runtime passes main's status to aster_exit
// (software/runtime/aster_trap.S, weak there), and an unexpected trap -1. A
// store to tohost ends the run in the shell and in Spike: 1 passes, (status << 1) | 1
// reports a failure.
#include <stdint.h>

volatile uint32_t tohost[2] __attribute__((section(".tohost"), aligned(64)));
volatile uint32_t fromhost[2] __attribute__((section(".tohost"), aligned(64)));

void aster_exit(int status) {
    __asm__ volatile ("fence" ::: "memory");
    tohost[0] = status == 0 ? 1u : ((uint32_t)status << 1) | 1u;
    for (;;) __asm__ volatile ("" ::: "memory");
}
