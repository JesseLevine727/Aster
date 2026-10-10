// 20.5's copy policy (aster_copy.h, docs/tuning.md §4.1).
#include "aster_copy.h"

#include <stdint.h>

struct aster_copy_policy aster_copy_policy = {127u, 63u, 15u};   // 20.4's crossovers at R (tuned in step 3)

static enum aster_dma_result pending = ASTER_DMA_OK;              // (a started DMA copy's, until finished)

typedef uint32_t alias_word __attribute__((__may_alias__));

// v1's fair CPU copy, verbatim (software/benchmarks/dma.c)
__attribute__((noipa)) void aster_fair_copy(void *destination_, const void *source_, uint32_t size) {
    uint8_t *dst = destination_;
    const uint8_t *src = source_;
    // Matching offsets become aligned after at most three bytes. Different
    // offsets use the explicit byte path; no unaligned RV32 loads are assumed.
    while (size && (((uintptr_t)src | (uintptr_t)dst) & 3)) { *dst++ = *src++; --size; }
    while (size >= 16) {
        alias_word *d = (alias_word *)(void *)dst;
        const alias_word *s = (const alias_word *)(const void *)src;
        d[0] = s[0]; d[1] = s[1]; d[2] = s[2]; d[3] = s[3];
        dst += 16; src += 16; size -= 16;
    }
    while (size >= 4) {
        *(alias_word *)(void *)dst = *(const alias_word *)(const void *)src;
        dst += 4; src += 4; size -= 4;
    }
    while (size) { *dst++ = *src++; --size; }
}

static uint32_t hart_id(void) { return *(volatile uint32_t *)0x20002000u; }   // (the SoC's, as v1's driver reads it)

enum aster_copy_engine aster_copy_choice(void *destination, const void *source, uint32_t bytes) {
    if (hart_id() != 0u) return ASTER_COPY_CPU;
    const uintptr_t d = (uintptr_t)destination, s = (uintptr_t)source;
    const uint32_t threshold = ((d | s) & 3u) == 0 ? aster_copy_policy.aligned
                             : ((d ^ s) & 3u) == 0 ? aster_copy_policy.same_offset
                             : aster_copy_policy.different_offsets;
    return bytes >= threshold ? ASTER_COPY_DMA : ASTER_COPY_CPU;
}

enum aster_dma_result aster_copy_start(void *destination, const void *source, uint32_t bytes,
                                       enum aster_copy_engine *used) {
    if (pending == ASTER_DMA_PENDING) return ASTER_DMA_ALREADY_BUSY;     // (one copy at a time)
    const enum aster_copy_engine engine = aster_copy_choice(destination, source, bytes);
    if (used) *used = engine;
    if (engine == ASTER_COPY_CPU) {
        aster_fair_copy(destination, source, bytes);
        pending = ASTER_DMA_OK;
        return ASTER_DMA_OK;
    }
    pending = aster_dma_submit(destination, source, bytes);
    return pending;
}

enum aster_dma_result aster_copy_finish(void) {
    enum aster_dma_result r = pending;
    if (r == ASTER_DMA_PENDING) r = aster_dma_wait(8000000u);
    // (a timeout does not hand the buffers back, v1's driver says: the copy stays pending, to be finished or
    // aborted through the driver)
    pending = r == ASTER_DMA_TIMEOUT ? ASTER_DMA_PENDING : ASTER_DMA_OK;
    return r;
}

enum aster_dma_result aster_copy(void *destination, const void *source, uint32_t bytes, enum aster_copy_engine *used) {
    const enum aster_dma_result r = aster_copy_start(destination, source, bytes, used);
    return r == ASTER_DMA_PENDING ? aster_copy_finish() : r;
}
