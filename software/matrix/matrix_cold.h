// 20.4's matrix: a cold or a warm run as one data word (MATRIX_COLD sets it), so that the two builds' code and
// data lie at the same addresses and differ only in that word: whether the warm-up pass runs. (A compile-time
// branch let the compiler inline the single pass in a cold build, which moved every later function and the data
// after them: MNIST's cold windows came out up to 0.24% faster than its warm ones.)
#ifndef MATRIX_COLD_H
#define MATRIX_COLD_H
#include <stdint.h>
#ifdef MATRIX_COLD
#define MATRIX_COLD_WORD 1u
#else
#define MATRIX_COLD_WORD 0u
#endif
static volatile const uint32_t matrix_cold = MATRIX_COLD_WORD;
#define MATRIX_CACHE_STATE (matrix_cold ? "cold" : "warm")
#endif
