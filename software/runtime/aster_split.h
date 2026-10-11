// 20.5's split policy (docs/tuning.md §4.3): two workers only from the sizes at which two beat one at R on every
// layout, warm and cold, in every window (step 3's finer sweep, scripts/matrix.py's split_fine): the runtime's
// default for tuned variants. Below them, one hart does the work. Sizes are a program's build constants, so the
// thresholds are macros its preprocessor can test.
#ifndef ASTER_SPLIT_H
#define ASTER_SPLIT_H

#include <stdint.h>

#define ASTER_SPLIT_NEVER 0xFFFFFFFFu                   // (the harts serialise: one worker at every size)

// the dot product's K (software/matrix/dsp.c): the cold run binds (warm, two workers win from about 40)
#define ASTER_SPLIT_DOT_K 128u

// the coherence kinds' items (software/matrix/coherent.c's kinds; the sweep's sizes 2 to 129)
#define ASTER_SPLIT_ITEMS_0 129u                        // atomic_add
#define ASTER_SPLIT_ITEMS_1 64u                         // lrsc_counter
#define ASTER_SPLIT_ITEMS_2 64u                         // cas_counter
#define ASTER_SPLIT_ITEMS_3 ASTER_SPLIT_NEVER           // lock_sum
#define ASTER_SPLIT_ITEMS_4 129u                        // false_shared
#define ASTER_SPLIT_ITEMS_5 129u                        // padded
#define ASTER_SPLIT_ITEMS_8 16u                         // shared_mix
#define ASTER_SPLIT_ITEMS__(kind) ASTER_SPLIT_ITEMS_##kind   // (kinds 6, 7 and 9 are two-hart cases: none)
#define ASTER_SPLIT_ITEMS(kind) ASTER_SPLIT_ITEMS__(kind)

static inline uint32_t aster_split_workers(uint32_t size, uint32_t from) { return size >= from ? 2u : 1u; }

#endif
