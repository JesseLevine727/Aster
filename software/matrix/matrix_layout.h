// 20.5's layout knob (docs/tuning.md §3): the layouts that judge a tuning change pad *between* the image's parts,
// so that each part's bank phase (address bits [5:4]) and cache index move relative to the others, as 20.4's
// runtime growth moved them. Three pads:
//   MATRIX_PAD_CODE  between the program's code and the runtime's (matrix_pad.S, linked before the runtime);
//   MATRIX_PAD_DATA  each of the program's buffers placed this many bytes past its alignment;
//   MATRIX_PAD_HART  hart 1's own buffers placed this many bytes further, in the two-hart programs that have them.
// The layouts: L0 every pad 0 (the layout of record); L1, L2, L3 every pad 16, 32, 48; L4 the code and data pads
// 1 KiB. A buffer's pad survives its alignment: the buffer is declared with room for the pad and named through
// MATRIX_AT, a dereferenced pointer to an array of its own type and length, so indexing, sizeof and decay are
// the array's:
//     MATRIX_ROOM(name, bytes, align);                      (its room: the bytes, plus the pads, aligned)
//     #define name MATRIX_AT(type, name, count)             (or MATRIX_AT_HART for hart 1's own buffer)
// With every pad 0 the buffer's address is the room's: the layout of record.
#ifndef MATRIX_LAYOUT_H
#define MATRIX_LAYOUT_H

#include <stdint.h>

#ifndef MATRIX_PAD_DATA
#define MATRIX_PAD_DATA 0u
#endif
#ifndef MATRIX_PAD_HART
#define MATRIX_PAD_HART 0u
#endif

#define MATRIX_ROOM(name, bytes, align) \
    static uint8_t name##_room[(bytes) + MATRIX_PAD_DATA + MATRIX_PAD_HART] __attribute__((aligned(align)))
#define MATRIX_AT(type, name, count) (*(type (*)[count])(void *)(name##_room + MATRIX_PAD_DATA))
#define MATRIX_AT_HART(type, name, count) \
    (*(type (*)[count])(void *)(name##_room + MATRIX_PAD_DATA + MATRIX_PAD_HART))
#define MATRIX_OBJECT(type, name) (*(type *)(void *)(name##_room + MATRIX_PAD_DATA))

#endif
