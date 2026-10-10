// Phase 20.5 (docs/tuning.md §4.1): the copy helper (software/runtime/aster_copy.c) in the two-hart SoC. Each
// check that fails sets a distinct status:
//  1  a copy's destination is not the source's bytes, its guards (and the source) changed: every length 0-64
//     and the lengths around the thresholds, at all 16 offset pairs, by the policy's engine
//  2  the engine chosen is not the policy's (computed here, independently, from the thresholds and the offsets)
//  3  a DMA copy reported an error
//  4  hart 1 used the DMA (only hart 0 may), or its copies are wrong
//  5  aster_copy_start/finish: a started DMA copy is not done at finish, or a CPU copy not done at start
#include <stdint.h>

#include "aster.h"
#include "aster_copy.h"
#include "aster_smp.h"

#define GUARD 64u
#define MAX_BYTES 320u
static uint8_t source[GUARD + 4u + MAX_BYTES + GUARD] __attribute__((aligned(64)));
static uint8_t destination[GUARD + 4u + MAX_BYTES + GUARD] __attribute__((aligned(64)));
static volatile int status;

static void check(int ok, int number) { if (!ok && !status) status = number; }

static uint8_t source_byte(uint32_t i) { return (uint8_t)((i * 37u + 11u) ^ (i >> 3)); }
static uint8_t background(uint32_t i) { return (uint8_t)(0xA5u ^ i); }

// one copy, checked whole: the engine the policy names, every byte of the copy, the guards and the source
static uint32_t copy_checked(uint32_t bytes, uint32_t src_off, uint32_t dst_off, int hart, int number) {
    for (uint32_t i = 0; i < sizeof source; ++i) { source[i] = source_byte(i); destination[i] = background(i); }
    __asm__ volatile ("fence rw,rw" ::: "memory");
    uint8_t *dst = destination + GUARD + dst_off;
    const uint8_t *src = source + GUARD + src_off;
    enum aster_copy_engine used;
    const enum aster_dma_result r = aster_copy(dst, src, bytes, &used);
    __asm__ volatile ("fence rw,rw" ::: "memory");
    const uint32_t threshold = ((src_off | dst_off) & 3u) == 0 ? aster_copy_policy.aligned
                             : ((src_off ^ dst_off) & 3u) == 0 ? aster_copy_policy.same_offset
                             : aster_copy_policy.different_offsets;
    const enum aster_copy_engine want = hart == 0 && bytes >= threshold ? ASTER_COPY_DMA : ASTER_COPY_CPU;
    check(used == want, hart ? 4 : 2);
    check(r == ASTER_DMA_OK, hart ? 4 : 3);
    for (uint32_t i = 0; i < sizeof destination; ++i) {
        const int in = i >= GUARD + dst_off && i < GUARD + dst_off + bytes;
        check(destination[i] == (in ? source_byte(i - dst_off + src_off) : background(i)), number);
        check(source[i] == source_byte(i), number);
    }
    return used == ASTER_COPY_DMA;
}

static const uint32_t extra[] = {95u, 96u, 126u, 127u, 128u, 160u, 255u, 256u, 300u, 320u};

static void hart1_copies(void *arg) {
    volatile uint32_t *dma = arg;
    for (uint32_t k = 0; k < sizeof extra / sizeof extra[0]; ++k) *dma += copy_checked(extra[k], 0, 0, 1, 4);
}

int main(void) {
    uint32_t copies = 0, by_dma = 0;
    for (uint32_t s = 0; s < 4u; ++s)
        for (uint32_t d = 0; d < 4u; ++d) {
            for (uint32_t bytes = 0; bytes <= 64u; ++bytes, ++copies) by_dma += copy_checked(bytes, s, d, 0, 1);
            for (uint32_t k = 0; k < sizeof extra / sizeof extra[0]; ++k, ++copies)
                by_dma += copy_checked(extra[k], s, d, 0, 1);
        }
    // started, then finished: the DMA's copy is done only at finish; the CPU's at once
    for (uint32_t i = 0; i < sizeof source; ++i) { source[i] = source_byte(i); destination[i] = background(i); }
    __asm__ volatile ("fence rw,rw" ::: "memory");
    enum aster_copy_engine used;
    enum aster_dma_result r = aster_copy_start(destination + GUARD, source + GUARD, 256u, &used);
    check(used == ASTER_COPY_DMA && (r == ASTER_DMA_PENDING || r == ASTER_DMA_OK), 5);
    check(aster_copy_finish() == ASTER_DMA_OK, 5);
    for (uint32_t i = 0; i < 256u; ++i) check(destination[GUARD + i] == source_byte(GUARD + i), 5);
    r = aster_copy_start(destination + GUARD + 1u, source + GUARD, 8u, &used);
    check(used == ASTER_COPY_CPU && r == ASTER_DMA_OK, 5);
    for (uint32_t i = 0; i < 8u; ++i) check(destination[GUARD + 1u + i] == source_byte(GUARD + i), 5);
    check(aster_copy_finish() == ASTER_DMA_OK, 5);
    // hart 1: every copy by the CPU
    static volatile uint32_t hart1_dma;
    aster_smp_start();
    aster_smp_dispatch(hart1_copies, (void *)&hart1_dma);
    aster_smp_join();
    check(hart1_dma == 0u, 4);
    aster_puts("SOC COPY copies="); aster_put_u32(copies); aster_puts(" dma="); aster_put_u32(by_dma);
    aster_puts(" hart1_copies="); aster_put_u32(sizeof extra / sizeof extra[0]);
    aster_puts(status ? " FAIL check=" : " PASS");
    if (status) aster_put_u32((uint32_t)status);
    aster_puts("\n");
    return status;
}
