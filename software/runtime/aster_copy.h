// 20.5's copy policy (docs/tuning.md §4.1): a copy by the DMA (v1's driver, software/drivers/aster_dma.c,
// unchanged) or by the CPU's fair copy (v1's, software/benchmarks/dma.c, verbatim), chosen by size and alignment.
// The thresholds (the bytes from which the DMA copies) start at 20.4's crossovers at R (docs/phase20.md, step 3:
// from 127 bytes aligned, 63 at the same offset, 15 at different offsets) and are tuned in 20.5's step 3; a
// program may set its own. Only hart 0 programs the DMA (v1's driver checks it), so hart 1 always copies itself.
#ifndef ASTER_COPY_H
#define ASTER_COPY_H

#include <stdint.h>

#include "aster_dma.h"

struct aster_copy_policy {
    uint32_t aligned;                 // source and destination both word-aligned
    uint32_t same_offset;             // the same offset in a word, not zero
    uint32_t different_offsets;       // different offsets
};
extern struct aster_copy_policy aster_copy_policy;

enum aster_copy_engine { ASTER_COPY_CPU = 0, ASTER_COPY_DMA = 1 };

// the engine the policy picks for this copy, on this hart
enum aster_copy_engine aster_copy_choice(void *destination, const void *source, uint32_t bytes);
// v1's fair CPU copy
void aster_fair_copy(void *destination, const void *source, uint32_t bytes);
// a copy, done when it returns: ASTER_DMA_OK, or the DMA driver's error. *used (if given) says which engine
enum aster_dma_result aster_copy(void *destination, const void *source, uint32_t bytes, enum aster_copy_engine *used);
// a copy begun: the CPU's done at once (ASTER_DMA_OK), the DMA's running (ASTER_DMA_PENDING) until
// aster_copy_finish; *used (if given) says which engine. One copy at a time.
enum aster_dma_result aster_copy_start(void *destination, const void *source, uint32_t bytes,
                                       enum aster_copy_engine *used);
enum aster_dma_result aster_copy_finish(void);

#endif
