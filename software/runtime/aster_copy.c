// 20.5's copy policy (aster_copy.h, docs/tuning.md §4.1).
#include "aster_copy.h"

#include <stdint.h>

// 20.5 step 3's thresholds (docs/tuning.md §4.1): the finer sweep at R (8 KiB caches) on every layout, warm and cold,
// the destination cached or not, the DMA where it wins on all of them (scripts/matrix_crossover.py; 20.4's crossovers
// were 127, 63 and 15)
struct aster_copy_policy aster_copy_policy = {113u, 89u, 21u, 13u};

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

// 20.5 step 3b's CPU copy at different offsets in a word, tuned for Aster (the owner's decision, 11 October 2026:
// docs/tuning.md §4.1), where v1's fair copy goes a byte at a time: the destination word-aligned first, a byte at a
// time, then aligned words loaded and each output word merged from two by shifts, one aligned store a word; the
// tail a byte at a time. A load never leaves the aligned word that holds a byte the copy needs. (At the same offset
// the helper keeps v1's fair copy: step 3b's sweep found no loop faster on Aster, its stores binding.)
__attribute__((noipa)) void aster_shift_copy(void *destination_, const void *source_, uint32_t size) {
    uint8_t *dst = destination_;
    const uint8_t *src = source_;
    while (size && ((uintptr_t)dst & 3u)) { *dst++ = *src++; --size; }
    const uint32_t offset = (uintptr_t)src & 3u;
    if (offset != 0 && size >= 4) {
        const uint32_t right = 8u * offset, left = 32u - right;
        alias_word *d = (alias_word *)(void *)dst;
        const alias_word *s = (const alias_word *)(const void *)(src - offset);
        uint32_t low = *s++;
        while (size >= 4) {
            const uint32_t high = *s++;
            *d++ = (low >> right) | (high << left);
            low = high;
            size -= 4;
        }
        dst = (uint8_t *)d;
        src = (const uint8_t *)s - 4u + offset;
    }
    while (size) { *dst++ = *src++; --size; }       // (and the whole copy, should the offsets match after all)
}

static uint32_t same_offset(const void *destination, const void *source) {
    return (((uintptr_t)destination ^ (uintptr_t)source) & 3u) == 0;
}

void aster_cpu_copy(void *destination, const void *source, uint32_t bytes) {
    if (same_offset(destination, source) || bytes < aster_copy_policy.shift) aster_fair_copy(destination, source, bytes);
    else aster_shift_copy(destination, source, bytes);
}

// the hart's number: its mhartid, which is the SoC's 0x2000_2000 (v1's driver's) without a device read on every copy
// (20.5 step 3: the device read cost a copy about 95 cycles with the call)
static uint32_t hart_id(void) { uint32_t id; __asm__ volatile ("csrr %0, mhartid" : "=r"(id)); return id; }

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
        aster_cpu_copy(destination, source, bytes);                // (by the offsets: v1's, or the shift copy)
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
