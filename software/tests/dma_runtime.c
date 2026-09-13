// Real RV32IMA DMA driver regression. No ARM/helper copies, no timing claim.
#include "aster.h"
#include "aster_dma.h"
#include <stdatomic.h>

enum { BUFFER = 1152, POLLS = 200000 };
static uint8_t source[BUFFER] __attribute__((aligned(64)));
static uint8_t destination[BUFFER] __attribute__((aligned(64)));
static _Atomic uint32_t published, consumed;
static _Atomic uint32_t independent_work;
volatile uint32_t dma_results[128] __attribute__((section(".private0")));
static uint32_t checks, jobs;

static void check(int ok) {
    ++checks;
    if (!ok) {
        aster_puts("DMA FAIL check="); aster_put_u32(checks); aster_putc('\n');
        __asm__ volatile ("ebreak"); for (;;) {}
    }
}
static uint8_t pattern(unsigned offset, unsigned seed) {
    return (uint8_t)((offset * 73u) ^ (offset >> 3) ^ (seed * 41u));
}
static void prepare(unsigned seed) {
    for (unsigned i = 0; i < BUFFER; ++i) { source[i] = pattern(i, seed); destination[i] = (uint8_t)(0xa5 ^ i); }
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
}
static void verify(unsigned src, unsigned dst, unsigned length, unsigned seed) {
    for (unsigned i = 0; i < BUFFER; ++i) {
        // Still compare every source/destination/guard byte; keep the success
        // count in one store so test bookkeeping does not dominate simulation.
        if (source[i] != pattern(i,seed)) { checks += 2*i; check(0); }
        if (destination[i] != (i >= dst && i < dst+length ? pattern(src+i-dst,seed) : (uint8_t)(0xa5 ^ i))) {
            checks += 2*i+1; check(0);
        }
    }
    checks += 2*BUFFER;
}
static void directed(void) {
    const unsigned sizes[] = {0,1,2,3,4,7,8,15,16,31,32,63,64,127,128,255,256,511,512,1024};
    for (unsigned sa = 0; sa < 4; ++sa) for (unsigned da = 0; da < 4; ++da)
    for (unsigned n = 0; n < sizeof(sizes)/sizeof(sizes[0]); ++n) {
        unsigned seed = 1+sa*80+da*20+n;
        prepare(seed);
        check(aster_dma_copy(destination+64+da, source+64+sa, sizes[n], POLLS) == ASTER_DMA_OK);
        check(aster_dma_bytes_done() == sizes[n] && aster_dma_status() == ASTER_DMA_DONE);
        check(sizes[n] ? aster_dma_job_cycles() != 0 : aster_dma_job_cycles() == 0);
        verify(64+sa,64+da,sizes[n],seed); ++jobs;
    }
    check(aster_dma_copy((void *)0xffffffffu,(const void *)0,0,POLLS) == ASTER_DMA_OK);
    check(aster_dma_bytes_done() == 0);
    check(aster_dma_copy(destination,(const void *)0xfffffffcu,8,POLLS) == ASTER_DMA_BAD_SOURCE);
    check(aster_dma_copy((void *)0xfffffffcu,source,8,POLLS) == ASTER_DMA_BAD_DESTINATION);
    check(aster_dma_copy(destination,(const void *)0x10008000u,4,POLLS) == ASTER_DMA_BAD_SOURCE);
    check(aster_dma_copy((void *)0x1000c000u,source,4,POLLS) == ASTER_DMA_BAD_DESTINATION);
    check(aster_dma_copy(destination,(const void *)0x10007fffu,2,POLLS) == ASTER_DMA_BAD_SOURCE);
    check(aster_dma_copy(source+1,source,32,POLLS) == ASTER_DMA_OVERLAP);
    check(aster_dma_acknowledge() == ASTER_DMA_OK && aster_dma_poll() == ASTER_DMA_IDLE);
    // Abort is a real hardware prefix, not a canceled CPU loop. Zero polling
    // budget cannot imply quiescence; a new descriptor must not alter this job.
    prepare(777);
    check(aster_dma_submit(destination+64,source+65,1024) == ASTER_DMA_PENDING);
    check(aster_dma_wait(0) == ASTER_DMA_TIMEOUT);
    check(aster_dma_submit(destination,source,4) == ASTER_DMA_ALREADY_BUSY);
    check(aster_dma_acknowledge() == ASTER_DMA_ALREADY_BUSY);
    check(aster_dma_abort_and_wait(POLLS) == ASTER_DMA_WAS_ABORTED);
    unsigned prefix = aster_dma_bytes_done();
    check(prefix < 1024 && aster_dma_status() == (ASTER_DMA_DONE | ASTER_DMA_ABORTED));
    verify(65,64,prefix,777);
    check(aster_dma_copy(destination+64,source+65,1024,POLLS) == ASTER_DMA_OK);
    verify(65,64,1024,777);
    aster_puts("DMA DIRECTED PASS\n");
}

static uint32_t lr(volatile uint32_t *address) {
    uint32_t value; __asm__ volatile ("lr.w %0,(%1)" : "=r"(value) : "r"(address) : "memory"); return value;
}
static uint32_t sc(volatile uint32_t *address, uint32_t value) {
    uint32_t failed;
    __asm__ volatile ("sc.w %0,%2,(%1)" : "=r"(failed) : "r"(address), "r"(value) : "memory"); return failed;
}
static void reservations(void) {
    volatile uint32_t *word = (volatile uint32_t *)(void *)(destination+64);
    // These are explicit implementation-directed LR/SC trials, not constrained
    // progress loops: our word-granular reservation survives unrelated traffic.
    prepare(991);
    uint32_t old = lr(word);
    check(aster_dma_copy(destination+68,source+64,4,POLLS) == ASTER_DMA_OK);
    check(sc(word,old) == 0); // same line, different word: dirty drain is maintenance
    old = lr(word);
    check(aster_dma_copy(source+64,destination+64,4,POLLS) == ASTER_DMA_OK);
    check(sc(word,old) == 0); // device read must not consume a reservation
    for (unsigned byte = 0; byte < 4; ++byte) {
        // Copy the same value, so only a correctly observed DMA store breaks SC.
        source[64] = destination[64+byte];
        old = lr(word);
        check(aster_dma_copy(destination+64+byte,source+64,1,POLLS) == ASTER_DMA_OK);
        check(sc(word,old ^ 0xffffffffu) != 0 && *word == old);
    }
    aster_puts("DMA RESERVATIONS PASS\n");
}

void aster_secondary_main(void) {
    for (unsigned job = 1; job <= 3; ++job) {
        while (atomic_load_explicit(&published,memory_order_acquire) != job) {
            // Even raw MMIO must enforce ownership; bypassing the C driver's
            // owner check must not mutate the primary's descriptor or abort it.
            ((volatile uint32_t *)ASTER_DMA_BASE)[0] = 0xffffffffu;
            ((volatile uint32_t *)ASTER_DMA_BASE)[1] = 0x1000c000u;
            ((volatile uint32_t *)ASTER_DMA_BASE)[2] = 0xffffffffu;
            ((volatile uint32_t *)ASTER_DMA_BASE)[3] = 1;
            ((volatile uint32_t *)ASTER_DMA_BASE)[3] = 2;
            atomic_fetch_add_explicit(&independent_work,1,memory_order_relaxed);
        }
        // Driver enforces primary-only control; the secondary consumes bytes
        // only after the primary publishes DMA completion with release/acquire.
        if (aster_dma_submit(destination,source,4) != ASTER_DMA_NOT_OWNER) {
            aster_puts("DMA SECONDARY FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
        for (unsigned i = 64; i < 1088; ++i) if (destination[i] != pattern(i+1,2000+job)) {
            aster_puts("DMA SECONDARY FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
        atomic_store_explicit(&consumed,job,memory_order_release);
    }
}
int main(void) {
    *(volatile uint32_t *)0x20003080u = 1;
    directed(); reservations();
    unsigned harts = *(volatile uint32_t *)0x20002008u;
    if (harts == 2) *(volatile uint32_t *)0x20002004u = 1;
    if (harts == 2) for (unsigned cycle = 0; cycle < 8; ++cycle) {
        prepare(3000+cycle);
        check(aster_dma_submit(destination+64,source+65,1024) == ASTER_DMA_PENDING);
        *(volatile uint32_t *)0x20002004u = 0;
        while (*(volatile uint32_t *)0x2000200cu & 2) {}
        check(aster_dma_wait(POLLS) == ASTER_DMA_OK && aster_dma_bytes_done() == 1024);
        verify(65,64,1024,3000+cycle);
        *(volatile uint32_t *)0x20002004u = 1;
    }
    for (unsigned job = 1; job <= 3; ++job) {
        prepare(2000+job);
        check(aster_dma_copy(destination+64,source+65,1024,POLLS) == ASTER_DMA_OK);
        atomic_store_explicit(&published,job,memory_order_release);
        if (harts == 2) while (atomic_load_explicit(&consumed,memory_order_acquire) != job) {}
        verify(65,64,1024,2000+job);
        dma_results[0] = job; dma_results[1] = jobs; dma_results[2] = checks;
        dma_results[3] = (uint32_t)(uintptr_t)source;
        dma_results[4] = (uint32_t)(uintptr_t)destination;
        dma_results[5] = atomic_load_explicit(&independent_work,memory_order_relaxed);
        dma_results[6] = harts; dma_results[7] = aster_dma_bytes_done();
        aster_puts("DMA JOB PASS\n");
    }
    *(volatile uint32_t *)0x20003080u = 2;
    __asm__ volatile ("fence iorw,iorw" ::: "memory");
    dma_results[8] = *(volatile uint32_t *)0x30000184u;
    dma_results[9] = *(volatile uint32_t *)0x30000188u;
    dma_results[10] = *(volatile uint32_t *)0x3000018cu;
    for (unsigned i = 0; i < 28; ++i) {
        dma_results[16+i] = ((volatile uint32_t *)0x30000100u)[i];
        dma_results[44+i] = ((volatile uint32_t *)0x20003000u)[i];
        dma_results[72+i] = ((volatile uint32_t *)0x20003100u)[i];
    }
    aster_puts("DMA COUNTERS PASS\n");
    return 0;
}
