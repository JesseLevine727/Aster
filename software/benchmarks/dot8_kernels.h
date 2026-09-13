#ifndef ASTER_DOT8_KERNELS_H
#define ASTER_DOT8_KERNELS_H
#include <stdint.h>

// A/B are signed INT8 bit patterns, Y holds modulo-2^32 results. Caller owns
// exact nonoverlapping objects: dot A[K],B[K],Y[1]; FIR A[K+7],B[K],Y[8];
// GEMM A[3*K],B[K*5],Y[15] row major. K=0 still writes every output zero.
// No special alignment, custom-only packing/pretranspose or hidden overread.
void aster_dot8_scalar_dot(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
void aster_dot8_custom_dot(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
void aster_dot8_scalar_fir(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
void aster_dot8_custom_fir(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
void aster_dot8_scalar_gemm(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
void aster_dot8_custom_gemm(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t k);
#endif
