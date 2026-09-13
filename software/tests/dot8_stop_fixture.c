// Runs forever to expose real custom/dirty-memory/selective-DMA stop edges.
// Only the simulator/guarded host requests global STOP; no self-reset or JTAG.
#include "aster.h"
#include "aster_dma.h"
#include "aster_dot8.h"
#include <stdatomic.h>
static uint8_t source[512] __attribute__((aligned(64)));
static uint8_t destination[512] __attribute__((aligned(64)));
static _Atomic uint32_t progress;
static volatile uint32_t primary_steps, secondary_result;
void aster_secondary_main(void) {
    uint32_t value=0x80808080u;
    for (;;) {
        value=aster_dot8_packed(value,0xff7f0180u);
        secondary_result=value;
        atomic_fetch_add_explicit(&progress,1,memory_order_release);
    }
}
int main(void) {
    unsigned harts=*(volatile uint32_t *)0x20002008u;
    for (unsigned job=1;;++job) {
        atomic_store_explicit(&progress,0,memory_order_relaxed);
        if(harts==2) {
            *(volatile uint32_t *)0x20002004u=1;
            while(atomic_load_explicit(&progress,memory_order_acquire)<32) {}
        }
        for(unsigned i=0;i<512;++i) {source[i]=(uint8_t)(i^job);destination[i]=(uint8_t)~i;}
        if(aster_dma_submit(destination,source,512)!=ASTER_DMA_PENDING) {__asm__ volatile ("ebreak");for(;;){}}
        if(harts==2) {
            *(volatile uint32_t *)0x20002004u=0;
            while(*(volatile uint32_t *)0x2000200cu&2) {}
        }
        uint32_t value=0x80808080u;
        for(unsigned i=0;i<64;++i) value=aster_dot8_packed(value,0xff7f0180u);
        primary_steps=value^job;
        if(aster_dma_wait(2000000)!=ASTER_DMA_OK) {__asm__ volatile ("ebreak");for(;;){}}
    }
}
