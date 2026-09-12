#ifndef ASTER_MULTICORE_H
#define ASTER_MULTICORE_H
#include "aster.h"

#define ASTER_HART_ID ((volatile uint32_t *)0x20002000u)
#define ASTER_SECONDARY_RUN ((volatile uint32_t *)0x20002004u)
#define ASTER_HART_COUNT ((volatile uint32_t *)0x20002008u)
#define ASTER_HART_STATUS ((volatile uint32_t *)0x2000200cu)
#define ASTER_TO_HART1 ((volatile uint32_t *)0x20002010u)
#define ASTER_TO_HART0 ((volatile uint32_t *)0x20002014u)
#define ASTER_PRIVATE0 __attribute__((section(".private0"), aligned(16)))
#define ASTER_PRIVATE1 __attribute__((section(".private1"), aligned(16)))

// Only for explicitly uncached shared objects, with single-writer/handshake
// ownership. This is not a substitute for cache coherence or atomic RMWs.
static inline void aster_fence(void) {
    __asm__ volatile ("fence rw,rw" ::: "memory");
}
static inline void aster_publish(volatile uint32_t *mailbox, uint32_t value) {
    aster_fence();
    *mailbox = value;
    aster_fence();
}
static inline uint32_t aster_observe(volatile uint32_t *mailbox) {
    uint32_t value = *mailbox;
    aster_fence();
    return value;
}
static inline void aster_secondary_release(void) {
    aster_publish(ASTER_SECONDARY_RUN, 1);
}
static inline void aster_secondary_reset(void) {
    aster_publish(ASTER_SECONDARY_RUN, 0);
}
#endif
