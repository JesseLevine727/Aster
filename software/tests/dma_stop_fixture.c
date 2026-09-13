// Short real-core fixture for global STOP escalation during selective DMA pause.
// The host interrupts this program at independently observed lifecycle edges.
#include "aster.h"
#include "aster_dma.h"
#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
static uint8_t source[384] __attribute__((aligned(64)));
static uint8_t destination[384] __attribute__((aligned(64)));
void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }
int main(void) {
    REG(0x20003080u) = 1;
    REG(0x20002004u) = 1;
    while (!(REG(0x2000200cu)&2)) {}
    for (unsigned i = 0; i < 384; ++i) { source[i] = (uint8_t)(i*73u+19u); destination[i] = (uint8_t)(i^0xa5u); }
    if (aster_dma_submit(destination+65,source+64,257) != ASTER_DMA_PENDING) {
        aster_puts("DMA STOP FIXTURE FAIL\n"); __asm__ volatile ("ebreak");
    }
    REG(0x20002004u) = 0;
    for (;;) __asm__ volatile ("" ::: "memory");
}
