#ifndef XE_KERNELS_H
#define XE_KERNELS_H

#include <stdint.h>
#include "aster_npu.h"

// Phase 10 cross-engine kernels.
//
// Every kernel is expressed with the Phase 9 byte-stride descriptor so the
// scalar, DOT8 and NPU paths consume the same logical inputs and output:
//
//   A element (i,k) = A_BASE + i*A_STRIDE + k
//   B element (k,j) = B_BASE + k*B_STRIDE + j
//   C element (i,j) = C_BASE + i*C_STRIDE + 4*j
//
// Inputs are signed two's-complement INT8; outputs are 32-bit two's-complement
// accumulated modulo 2^32. The three kernels are:
//
//   dot : M=1, N=1, K
//   fir : M=TAPS, N=1, K, A_STRIDE=1 (sliding window a[r+k])
//   gemm: M, N, K
//
// `xe_scalar_gemm` is the shared scalar reference. `xe_dot8_gemm` is the
// Xasterdot8 packed implementation of the same descriptor and requires an image
// built with ENABLE_DOT8=1. Neither touches memory beyond the descriptor.

void xe_scalar_gemm(const struct aster_npu_gemm *job);
void xe_dot8_gemm(const struct aster_npu_gemm *job);

// Number of signed products the logical job performs, for diagnostics.
uint64_t xe_mac_count(const struct aster_npu_gemm *job);

#endif
