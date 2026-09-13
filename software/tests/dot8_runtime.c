// Real RAM-backed RV32IMA+Xasterdot8 acceptance, not a performance benchmark.
// Private progress records let the independent simulator validate every output
// as it is architecturally stored, including values overwritten by later jobs.
#include "aster.h"
#include "aster_dma.h"
#include "aster_dot8.h"
#include "dot8_kernels.h"
#include <stdatomic.h>

enum { BUFFER=512, YWORDS=47, YOFFSET=16, POLLS=2000000, META_WORDS=160 };
static uint8_t input_a[2][BUFFER] __attribute__((aligned(64)));
static uint8_t input_b[2][BUFFER] __attribute__((aligned(64)));
static uint32_t output[2][YWORDS] __attribute__((aligned(64)));
static uint8_t dma_source[BUFFER] __attribute__((aligned(64)));
static uint8_t dma_destination[BUFFER] __attribute__((aligned(64)));
static _Atomic uint32_t directed_done[2], published, consumed;
static uint32_t reservation_word[2];
volatile uint32_t aster_dot8_runtime_results0[META_WORDS] __attribute__((section(".private0")));
volatile uint32_t aster_dot8_runtime_results1[META_WORDS] __attribute__((section(".private1")));
static volatile uint32_t *results(unsigned h) {
    return h ? aster_dot8_runtime_results1 : aster_dot8_runtime_results0;
}
static void fence(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }
static void check(int ok) {
    if (!ok) { __asm__ volatile ("ebreak"); for (;;) {} }
}
static uint8_t pattern(unsigned i,uint32_t seed,unsigned bank) {
    switch (seed&7) {
        case 0: return 0;
        case 1: return 128;
        case 2: return bank?127:128;
        case 3: return (i&1)?127:128;
        default: return (uint8_t)((seed>>((i&3)*8))^(i*73u)^(i>>3)^(bank*0x5bu));
    }
}
static uint32_t guard(unsigned i,uint32_t seed) { return 0x6d5a0000u^(i*0x01010101u)^seed; }
static unsigned outputs(unsigned kind) { return kind==0?1:kind==1?8:15; }
static uint32_t reference(const uint8_t *a,const uint8_t *b,unsigned kind,unsigned n,unsigned out) {
    uint32_t sum=0;
    for (unsigned k=0;k<n;++k) {
        unsigned ai=kind==2?(out/5)*n+k:kind==1?out+k:k;
        unsigned bi=kind==2?k*5+out%5:k;
        // Independent scalar interpretation, deliberately not the driver helper.
        int x=a[ai],y=b[bi]; if (x>=128) x-=256; if (y>=128) y-=256;
        sum+=(uint32_t)(x*y);
    }
    return sum;
}
static void prepare(unsigned slot,uint32_t seed) {
    for (unsigned i=0;i<BUFFER;++i) { input_a[slot][i]=pattern(i,seed,0);input_b[slot][i]=pattern(i,seed,1); }
    for (unsigned i=0;i<YWORDS;++i) output[slot][i]=guard(i,seed);
    fence();
}
static void metadata(unsigned h,unsigned slot,unsigned job,unsigned kind,unsigned n,unsigned sa,unsigned sb,uint32_t seed) {
    volatile uint32_t *m=results(h);
    m[0]=0;m[1]=job;m[2]=kind;m[3]=n;m[4]=sa;m[5]=sb;m[6]=seed;
    m[7]=(uint32_t)(uintptr_t)input_a[slot];m[8]=(uint32_t)(uintptr_t)input_b[slot];
    m[9]=(uint32_t)(uintptr_t)output[slot];m[10]=BUFFER;m[11]=YWORDS;
    fence();
}
static void invoke(unsigned h,unsigned slot,unsigned kind,unsigned n,unsigned sa,unsigned sb,unsigned custom) {
    results(h)[0]=custom?2:1;fence();
    const uint8_t *a=input_a[slot]+64+sa,*b=input_b[slot]+64+sb;
    uint32_t *y=output[slot]+YOFFSET;
    if (kind==0) {
        if (custom) aster_dot8_custom_dot(a,b,y,n);else aster_dot8_scalar_dot(a,b,y,n);
    } else if (kind==1) {
        if (custom) aster_dot8_custom_fir(a,b,y,n);else aster_dot8_scalar_fir(a,b,y,n);
    } else {
        if (custom) aster_dot8_custom_gemm(a,b,y,n);else aster_dot8_scalar_gemm(a,b,y,n);
    }
    fence();
}
static void verify(unsigned slot,unsigned kind,unsigned n,unsigned sa,unsigned sb,uint32_t seed) {
    for (unsigned i=0;i<BUFFER;++i)
        check(input_a[slot][i]==pattern(i,seed,0) && input_b[slot][i]==pattern(i,seed,1));
    for (unsigned i=0;i<YWORDS;++i) {
        uint32_t expected=i>=YOFFSET && i<YOFFSET+outputs(kind)
            ?reference(input_a[slot]+64+sa,input_b[slot]+64+sb,kind,n,i-YOFFSET):guard(i,seed);
        check(output[slot][i]==expected);
    }
}
static void reservations(unsigned h) {
    volatile uint32_t *m=results(h);m[0]=4;fence();
    for (unsigned i=0;i<16;++i) {
        uint32_t value=0x12345678u^i,a=0x80807fffu+i,b=0xff01807fu;
        reservation_word[h]=value;fence();
        uint32_t old,dot,failed;
        __asm__ volatile (
            "lr.w %0,(%3)\n\t"
            ".insn r 0x0b,0,0,%1,%4,%5\n\t"
            "sc.w %2,%6,(%3)"
            : "=&r"(old),"=&r"(dot),"=&r"(failed)
            : "r"(&reservation_word[h]),"r"(a),"r"(b),"r"(value+1) : "memory");
        check(old==value && !failed && dot==aster_dot8_reference(a,b) && reservation_word[h]==value+1);
    }
    __asm__ volatile (".insn r 0x0b,0,0,x0,%0,%1" :: "r"(0x80808080u),"r"(0x80808080u));
    m[19]=16;m[18]+=17;m[0]=100;fence();
}
static void directed(unsigned h) {
    static const unsigned sizes[]={0,1,2,3,4,5,7,8,15,16,31,32,63,64,127,128,255,256};
    volatile uint32_t *m=results(h);unsigned job=0,dots=0;
    for (unsigned kind=0;kind<3;++kind) for (unsigned sa=0;sa<4;++sa) for (unsigned sb=0;sb<4;++sb)
    for (unsigned index=0;index<(kind==0?18u:14u);++index) {
        unsigned n=sizes[index];++job;uint32_t seed=0xa57e8000u^(h<<24)^(job*0x9e3779b9u);
        metadata(h,h,job,kind,n,sa,sb,seed);prepare(h,seed);
        invoke(h,h,kind,n,sa,sb,0);verify(h,kind,n,sa,sb,seed);
        m[0]=0;fence();for (unsigned i=0;i<YWORDS;++i) output[h][i]=guard(i,seed);
        invoke(h,h,kind,n,sa,sb,1);verify(h,kind,n,sa,sb,seed);
        dots+=(n/4)*outputs(kind);m[0]=3;m[16]=job;m[18]=dots;fence();
    }
    check(job==736);reservations(h);
    atomic_store_explicit(&directed_done[h],1,memory_order_release);
}
void aster_secondary_main(void) {
    directed(1);
    for (unsigned job=1;job<=3;++job) {
        while (atomic_load_explicit(&published,memory_order_acquire)!=job) {}
        uint32_t seed=0x81d7e005u+job*8;
        metadata(1,0,736+job,2,32,1,2,seed);
        invoke(1,0,2,32,1,2,1);verify(0,2,32,1,2,seed);
        results(1)[0]=3;results(1)[18]+=120;results(1)[20]=job;fence();
        atomic_store_explicit(&consumed,job,memory_order_release);
    }
    results(1)[0]=100;fence();
}
int main(void) {
    volatile uint32_t *m=results(0);unsigned harts=*(volatile uint32_t *)0x20002008u;
    check(*(volatile uint32_t *)0x20003284u==6 && *(volatile uint32_t *)0x20003288u==1);
    check(*(volatile uint32_t *)0x2000328cu==0x0b && *(volatile uint32_t *)0x20003290u==0xfe00707f);
    check(*(volatile uint32_t *)0x20003298u==harts && *(volatile uint32_t *)0x30000184u==5);
    *(volatile uint32_t *)0x20003080u=1;fence();
    if (harts==2) *(volatile uint32_t *)0x20002004u=1;
    directed(0);
    if (harts==2) while (!atomic_load_explicit(&directed_done[1],memory_order_acquire)) {}
    for (unsigned job=1;job<=3;++job) {
        uint32_t seed=0x81d7e005u+job*8;prepare(0,seed);
        for (unsigned i=0;i<BUFFER;++i) {dma_source[i]=pattern(i,seed,0);input_a[0][i]^=0x5a;}
        check(aster_dma_copy(input_a[0],dma_source,BUFFER,POLLS)==ASTER_DMA_OK);
        if (harts==2) {
            atomic_store_explicit(&published,job,memory_order_release);
            while (atomic_load_explicit(&consumed,memory_order_acquire)!=job) {}
        } else {
            metadata(0,0,736+job,2,32,1,2,seed);
            invoke(0,0,2,32,1,2,1);verify(0,2,32,1,2,seed);
            m[0]=3;m[18]+=120;
        }
        verify(0,2,32,1,2,seed);m[20]=job;
    }
    m[0]=4;fence();
    for (unsigned i=0;i<BUFFER;++i) dma_destination[i]=(uint8_t)(0xa5u^i);
    check(aster_dma_submit(dma_destination,dma_source,BUFFER)==ASTER_DMA_PENDING);
    for (unsigned i=0;i<64;++i) {
        uint32_t a=0x80807fffu+i*0x13579u,b=0xff01807fu;
        check(aster_dot8_packed(a,b)==aster_dot8_reference(a,b));
    }
    check(aster_dma_wait(POLLS)==ASTER_DMA_OK);
    for (unsigned i=0;i<BUFFER;++i) check(dma_destination[i]==dma_source[i]);
    m[18]+=64;check(aster_dma_acknowledge()==ASTER_DMA_OK);m[0]=100;fence();
    *(volatile uint32_t *)0x20003080u=2;fence();
    for (unsigned bank=0;bank<4;++bank) {
        uint32_t base=bank<2?0x20003000u+bank*256:bank==2?0x30000100u:0x20003200u;
        for (unsigned i=0;i<(bank==3?16u:28u);++i) m[32+28*bank+i]=((volatile uint32_t *)(uintptr_t)base)[i];
    }
    m[21]=harts;m[22]=0x80001u;fence();
    aster_puts("DOT8 RUNTIME PASS\n");return 0;
}
