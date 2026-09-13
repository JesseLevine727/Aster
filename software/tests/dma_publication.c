// Actual-core code publication: a DMA copy must become executable RAM code.
// Hart 1 produces dirty source instructions and consumes only after handoff.
#include "aster.h"
#include "aster_dma.h"
#include <stdatomic.h>

#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define ROUNDS 8u
volatile uint32_t publication_results[128] __attribute__((section(".private0")));
static uint32_t staging[16] __attribute__((aligned(64)));
static uint32_t code[16] __attribute__((aligned(64)));
static _Atomic uint32_t requested, produced, completed, consumed, peer_value;
static uint32_t checks;

static uint32_t value(unsigned epoch) { return 0x100u+epoch*7u; }
static void check(int condition) {
    ++checks;
    if (!condition) { aster_puts("DMA CODE FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {} }
}
static void produce(unsigned epoch) {
    for (unsigned i = 0; i < 16; ++i) staging[i] = 0xa57e0000u ^ i;
    staging[0] = (value(epoch)<<20) | 0x00000513u; // addi a0,zero,value
    staging[1] = 0x00008067u; // ret
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
}
__attribute__((noipa)) static uint32_t execute_code(void) {
    // RV32 bare-metal ABI test, not portable hosted-C function construction.
    return ((uint32_t (*)(void))(uintptr_t)code)();
}
void aster_secondary_main(void) {
    for (unsigned epoch = 1; epoch <= ROUNDS; ++epoch) {
        while (atomic_load_explicit(&requested,memory_order_acquire) != epoch) {}
        produce(epoch);
        atomic_store_explicit(&produced,epoch,memory_order_release);
        while (atomic_load_explicit(&completed,memory_order_acquire) != epoch) {}
        atomic_store_explicit(&peer_value,execute_code(),memory_order_relaxed);
        atomic_store_explicit(&consumed,epoch,memory_order_release);
    }
    for (;;) __asm__ volatile ("" ::: "memory");
}
int main(void) {
    const unsigned harts = REG(0x20002008u);
    REG(0x20003080u) = 1;
    if (harts == 2) REG(0x20002004u) = 1;
    uint32_t actual = 0;
    for (unsigned epoch = 1; epoch <= ROUNDS; ++epoch) {
        for (unsigned i = 0; i < 16; ++i) code[i] = 0xc0010000u ^ i;
        code[0] = 0x01100513u; code[1] = 0x00008067u;
        __asm__ volatile ("fence iorw,iorw" ::: "memory");
        check(execute_code() == 0x11); // Actually execute the old destination.
        if (harts == 2) {
            atomic_store_explicit(&requested,epoch,memory_order_release);
            while (atomic_load_explicit(&produced,memory_order_acquire) != epoch) {}
        } else produce(epoch);
        check(aster_dma_copy(code,staging,8,100000) == ASTER_DMA_OK);
        actual = execute_code(); check(actual == value(epoch));
        atomic_store_explicit(&completed,epoch,memory_order_release);
        if (harts == 2) {
            while (atomic_load_explicit(&consumed,memory_order_acquire) != epoch) {}
            check(atomic_load_explicit(&peer_value,memory_order_relaxed) == value(epoch));
        }
        for (unsigned i = 0; i < 16; ++i) {
            check(staging[i] == (i == 0 ? (value(epoch)<<20)|0x513u : i == 1 ? 0x8067u : 0xa57e0000u^i));
            check(code[i] == (i == 0 ? (value(epoch)<<20)|0x513u : i == 1 ? 0x8067u : 0xc0010000u^i));
        }
    }
    REG(0x20003080u) = 2;
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
    publication_results[0] = ROUNDS; publication_results[1] = 0; publication_results[2] = checks;
    publication_results[3] = actual; publication_results[4] = atomic_load_explicit(&peer_value,memory_order_relaxed);
    publication_results[5] = (uint32_t)(uintptr_t)code; publication_results[6] = harts; publication_results[7] = aster_dma_bytes_done();
    publication_results[8] = REG(0x30000184u); publication_results[9] = REG(0x30000188u); publication_results[10] = REG(0x3000018cu);
    publication_results[11] = (uint32_t)(uintptr_t)staging;
    for (unsigned i = 0; i < 28; ++i) {
        publication_results[16+i] = ((volatile uint32_t *)0x30000100u)[i];
        publication_results[44+i] = ((volatile uint32_t *)0x20003000u)[i];
        publication_results[72+i] = ((volatile uint32_t *)0x20003100u)[i];
    }
    aster_puts("DMA CODE PASS\n");
    return 0;
}
