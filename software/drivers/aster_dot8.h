#ifndef ASTER_DOT8_H
#define ASTER_DOT8_H
#include <stdint.h>

// Xasterdot8 ABI 1: four little-endian packed signed INT8 products, exact
// signed sum returned as a 32-bit bit pattern. Addition of results is unsigned
// modulo 2^32. Requires an explicitly dot8-enabled Aster image; no fallback.
static inline uint32_t aster_dot8_packed(uint32_t a, uint32_t b) {
    uint32_t result;
    __asm__ volatile (".insn r 0x0b, 0, 0, %0, %1, %2" : "=r"(result) : "r"(a), "r"(b));
    return result;
}
// Call only when four bytes remain in the input object. Byte loads work at any
// alignment without aliasing casts, signed shifts or access beyond a tail.
static inline uint32_t aster_dot8_pack4(const uint8_t *p) {
    if (((uintptr_t)p & 3u) == 0) {
        uint32_t packed;
        __builtin_memcpy(&packed,__builtin_assume_aligned(p,4),sizeof packed);
        return packed;
    }
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static inline int32_t aster_dot8_signed_byte(uint8_t byte) {
    // int8_t has an exact two's-complement representation. Bit-copy rather
    // than out-of-range unsigned-to-signed conversion; GCC folds this into LB
    // when inlined at a byte load, preserving a normal optimized baseline.
    int8_t signed_byte;
    __builtin_memcpy(&signed_byte,&byte,sizeof signed_byte);
    return signed_byte;
}
static inline uint32_t aster_dot8_reference(uint32_t a, uint32_t b) {
    int32_t sum=0;
    for (unsigned lane=0;lane<4;++lane)
        sum += aster_dot8_signed_byte((uint8_t)(a>>(8*lane))) *
               aster_dot8_signed_byte((uint8_t)(b>>(8*lane)));
    return (uint32_t)sum;
}
#endif
