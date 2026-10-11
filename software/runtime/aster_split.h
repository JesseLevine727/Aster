// 20.5's split policy (docs/tuning.md §4.3): one worker only at the sizes where one beats two at R on every layout,
// warm and cold, in every window, at that size and every smaller one of step 3's finer sweep (scripts/matrix.py's
// split_fine); from the next size up, the original's two workers. Below each kind's threshold there was a band
// where neither won everywhere; there the original's two workers stay (the owner's decision, 11 October 2026: only
// a win is adopted, tuning.md §3). The runtime's default for tuned variants. Sizes are a program's build constants,
// so the thresholds are macros its preprocessor can test.
#ifndef ASTER_SPLIT_H
#define ASTER_SPLIT_H

#include <stdint.h>

#define ASTER_SPLIT_NEVER 0xFFFFFFFFu                   // (the harts serialise: one worker at every size)

// the dot product's K (software/matrix/dsp.c): one worker wins to K = 32 (the band, 40 to 96, keeps two)
#define ASTER_SPLIT_DOT_K 33u

// the coherence kinds' items (software/matrix/coherent.c's kinds; the sweep's sizes 2 to 129)
#define ASTER_SPLIT_ITEMS_0 33u                         // atomic_add (one worker to 32 items)
#define ASTER_SPLIT_ITEMS_1 17u                         // lrsc_counter (to 16)
#define ASTER_SPLIT_ITEMS_2 9u                          // cas_counter (to 8)
#define ASTER_SPLIT_ITEMS_3 ASTER_SPLIT_NEVER           // lock_sum (one worker at every size)
#define ASTER_SPLIT_ITEMS_4 33u                         // false_shared (to 32)
#define ASTER_SPLIT_ITEMS_5 33u                         // padded (to 32)
#define ASTER_SPLIT_ITEMS_8 5u                          // shared_mix (to 4)
#define ASTER_SPLIT_ITEMS__(kind) ASTER_SPLIT_ITEMS_##kind   // (kinds 6, 7 and 9 are two-hart cases: none)
#define ASTER_SPLIT_ITEMS(kind) ASTER_SPLIT_ITEMS__(kind)

static inline uint32_t aster_split_workers(uint32_t size, uint32_t from) { return size >= from ? 2u : 1u; }

#endif
