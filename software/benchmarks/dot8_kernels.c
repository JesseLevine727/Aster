#include "dot8_kernels.h"
#include "aster_dot8.h"

// Identical no-IPA boundaries and build flags, ordinary nonvolatile C inputs.
// Deliberately include all packing/gather/tails/output stores in these kernels.
#define SCALAR __attribute__((noipa,section(".text.benchmark.scalar")))
#define CUSTOM __attribute__((noipa,section(".text.benchmark.custom")))
static inline uint32_t product(uint8_t a,uint8_t b) {
    return (uint32_t)(aster_dot8_signed_byte(a)*aster_dot8_signed_byte(b));
}
SCALAR void aster_dot8_scalar_dot(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    uint32_t sum=0;
    for (uint32_t k=0;k<n;++k) sum+=product(a[k],b[k]);
    y[0]=sum;
}
CUSTOM void aster_dot8_custom_dot(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    uint32_t sum=0,k=0;
    for (;n-k>=4;k+=4) sum+=aster_dot8_packed(aster_dot8_pack4(a+k),aster_dot8_pack4(b+k));
    for (;k<n;++k) sum+=product(a[k],b[k]);
    y[0]=sum;
}
SCALAR void aster_dot8_scalar_fir(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    for (uint32_t row=0;row<8;++row) {
        uint32_t sum=0;
        for (uint32_t k=0;k<n;++k) sum+=product(a[row+k],b[k]);
        y[row]=sum;
    }
}
CUSTOM void aster_dot8_custom_fir(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    for (uint32_t row=0;row<8;++row) {
        uint32_t sum=0,k=0;
        for (;n-k>=4;k+=4) sum+=aster_dot8_packed(aster_dot8_pack4(a+row+k),aster_dot8_pack4(b+k));
        for (;k<n;++k) sum+=product(a[row+k],b[k]);
        y[row]=sum;
    }
}
SCALAR void aster_dot8_scalar_gemm(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    for (uint32_t row=0;row<3;++row) for (uint32_t col=0;col<5;++col) {
        uint32_t sum=0;
        for (uint32_t k=0;k<n;++k) sum+=product(a[row*n+k],b[k*5+col]);
        y[row*5+col]=sum;
    }
}
CUSTOM void aster_dot8_custom_gemm(const uint8_t *a,const uint8_t *b,uint32_t *y,uint32_t n) {
    for (uint32_t row=0;row<3;++row) for (uint32_t col=0;col<5;++col) {
        uint32_t sum=0,k=0;
        for (;n-k>=4;k+=4) {
            const uint8_t *p=b+k*5+col;
            uint32_t packed=(uint32_t)p[0]|((uint32_t)p[5]<<8)|((uint32_t)p[10]<<16)|((uint32_t)p[15]<<24);
            sum+=aster_dot8_packed(aster_dot8_pack4(a+row*n+k),packed);
        }
        for (;k<n;++k) sum+=product(a[row*n+k],b[k*5+col]);
        y[row*5+col]=sum;
    }
}
