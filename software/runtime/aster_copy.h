// 20.5's copy policy (docs/tuning.md §4.1): a copy by the DMA (v1's driver, software/drivers/aster_dma.c,
// unchanged) or by the CPU, chosen by size and alignment. The CPU's copy (step 3b): at the same offset in a word, v1's
// fair copy (software/benchmarks/dma.c, verbatim); at different offsets, the shift copy, tuned for Aster.
// The thresholds (the bytes from which the DMA copies) are 20.5 step 3's (aster_copy.c: 113 bytes aligned, 89 at the
// same offset, 21 at different offsets; 20.4's crossovers were 127, 63 and 15); a program may set its own. Only hart 0 programs the DMA (v1's driver checks it), so hart 1 always copies itself.
#ifndef ASTER_COPY_H
#define ASTER_COPY_H

#include <stdint.h>

#include "aster_dma.h"

struct aster_copy_policy {            // the bytes from which the DMA copies, by alignment:
    uint32_t aligned;                 // source and destination both word-aligned
    uint32_t same_offset;             // the same offset in a word, not zero
    uint32_t different_offsets;       // different offsets
    uint32_t shift;                   // (the CPU's: the bytes from which the shift copy copies at different offsets)
};
extern struct aster_copy_policy aster_copy_policy;

enum aster_copy_engine { ASTER_COPY_CPU = 0, ASTER_COPY_DMA = 1 };

// the engine the policy picks for this copy, on this hart
enum aster_copy_engine aster_copy_choice(void *destination, const void *source, uint32_t bytes);
// v1's fair CPU copy (verbatim)
void aster_fair_copy(void *destination, const void *source, uint32_t bytes);
// step 3b's CPU copy at different offsets in a word, tuned for Aster (any offsets are copied correctly)
void aster_shift_copy(void *destination, const void *source, uint32_t bytes);
// the helper's CPU copy: v1's fair copy at the same offset, and below the policy's shift; else the shift copy
void aster_cpu_copy(void *destination, const void *source, uint32_t bytes);
// a copy, done when it returns: ASTER_DMA_OK, or the DMA driver's error. *used (if given) says which engine
enum aster_dma_result aster_copy(void *destination, const void *source, uint32_t bytes, enum aster_copy_engine *used);
// a copy begun: the CPU's done at once (ASTER_DMA_OK), the DMA's running (ASTER_DMA_PENDING) until
// aster_copy_finish; *used (if given) says which engine. One copy at a time.
enum aster_dma_result aster_copy_start(void *destination, const void *source, uint32_t bytes,
                                       enum aster_copy_engine *used);
enum aster_dma_result aster_copy_finish(void);

#endif
