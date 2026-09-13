// AsterBench v6: same-image scalar/custom signed INT8 dot, FIR and GEMM.
// Complete fixed experiment and inclusions/exclusions are in docs/phase8.md.
#include "aster.h"
#include "dot8_kernels.h"
#ifndef DOT8_KIND
#define DOT8_KIND 0
#endif
#ifndef DOT8_K
#define DOT8_K 64
#endif
#ifndef DOT8_ALIGNMENT
#define DOT8_ALIGNMENT 0
#endif
#ifndef DOT8_JOBS
#define DOT8_JOBS 4
#endif
#ifndef DOT8_SEED
#define DOT8_SEED 0x13570000u
#endif
_Static_assert(DOT8_KIND>=0 && DOT8_KIND<=2,"dot/FIR/GEMM only");
_Static_assert(DOT8_K>=0 && DOT8_K<=(DOT8_KIND==0?4096:64),"fixed study K bounds");
_Static_assert(DOT8_ALIGNMENT==0 || DOT8_ALIGNMENT==1,"aligned/unaligned only");
_Static_assert(DOT8_JOBS>=1 && DOT8_JOBS<=8,"1..8 paired jobs");
enum {
    A_USED=DOT8_KIND==2?3*DOT8_K:DOT8_KIND==1?DOT8_K+7:DOT8_K,
    B_USED=DOT8_KIND==2?5*DOT8_K:DOT8_K,
    OUTPUTS=DOT8_KIND==2?15:DOT8_KIND==1?8:1,
    A_BYTES=(A_USED+131+63)&~63,B_BYTES=(B_USED+131+63)&~63,
    A_OFFSET=64+DOT8_ALIGNMENT,B_OFFSET=64+2*DOT8_ALIGNMENT,
    Y_WORDS=48,Y_OFFSET=16,RECORD_WORDS=132
};
#define REG(addr) (*(volatile uint32_t *)(uintptr_t)(addr))
uint8_t aster_dot8_bench_a[A_BYTES] __attribute__((section(".dot8_a"),aligned(64)));
uint8_t aster_dot8_bench_b[B_BYTES] __attribute__((section(".dot8_b"),aligned(64)));
uint32_t aster_dot8_bench_y[Y_WORDS] __attribute__((section(".dot8_y"),aligned(64)));
volatile uint32_t aster_dot8_bench_results[DOT8_JOBS][2][RECORD_WORDS] __attribute__((section(".private0")));
static const char *const names[]={"dot","fir","gemm"};
static const char *const cpu_events[]={"cycles","retired","memory","i_access","i_miss","d_access","d_miss",
    "backing","atomic","sc_success","sc_failure","intervention","invalidation","writeback"};
static const char *const dma_events[]={"busy","wait","reads","writes","bytes","backing_reads","backing_writes",
    "forwards","dirty_words","invalidations","success","aborts","errors","rejected"};
static const char *const dot8_events[]={"accept","wait","complete","retired"};
static void fence(void) {__asm__ volatile ("fence iorw,iorw" ::: "memory");}
static uint8_t input_byte(unsigned i,uint32_t seed,unsigned bank) {
    return (uint8_t)((seed>>((i&3)*8))^(i*73u)^(i>>3)^(bank*0x5bu));
}
static uint32_t guard(unsigned i,uint32_t seed) {return 0x6d5a0000u^(i*0x01010101u)^seed;}
static void prepare(uint32_t seed) {
    for(unsigned i=0;i<A_BYTES;++i)aster_dot8_bench_a[i]=input_byte(i,seed,0);
    for(unsigned i=0;i<B_BYTES;++i)aster_dot8_bench_b[i]=input_byte(i,seed,1);
    for(unsigned i=0;i<Y_WORDS;++i)aster_dot8_bench_y[i]=guard(i,seed);
    fence();
}
static uint32_t reference(unsigned out,uint32_t seed) {
    uint32_t sum=0,n=DOT8_K;
    for(unsigned k=0;k<n;++k) {
        unsigned ai=DOT8_KIND==2?(out/5)*DOT8_K+k:DOT8_KIND==1?out+k:k;
        unsigned bi=DOT8_KIND==2?k*5+out%5:k;
        int x=input_byte(A_OFFSET+ai,seed,0),y=input_byte(B_OFFSET+bi,seed,1);
        if(x>=128)x-=256;
        if(y>=128)y-=256;
        sum+=(uint32_t)(x*y);
    }
    return sum;
}
static void number(const char* key,uint32_t value) {aster_putc(',');aster_puts(key);aster_putc('=');aster_put_u32(value);}
static void hex32(const char* key,uint32_t value) {aster_putc(',');aster_puts(key);aster_puts("=0x");aster_put_hex32(value);}
static void emit(volatile uint32_t* r) {
    aster_puts("ASTERBENCH,version=6,name=");aster_puts(names[DOT8_KIND]);
    aster_puts(",window=dispatch_load_pack_compute_store,policy=prepared_reinitialize,status=");
    aster_puts(r[21]?"FAIL":"PASS");aster_puts(",method=");aster_puts(r[1]?"custom":"scalar");
    aster_puts(",order=");aster_puts(r[0]&1?"scalar_custom":"custom_scalar");
    aster_puts(",alignment=");aster_puts(DOT8_ALIGNMENT?"unaligned":"aligned");
    number("k",DOT8_K);number("rows",DOT8_KIND==2?3:DOT8_KIND==1?8:1);number("cols",DOT8_KIND==2?5:1);
    number("outputs",OUTPUTS);number("jobs",DOT8_JOBS);number("job",r[0]);number("pass",r[2]);
    hex32("base_seed",DOT8_SEED);hex32("seed",r[3]);number("harts",r[15]);number("workers",1);
    number("a_used",A_USED);number("b_used",B_USED);number("a_offset",A_OFFSET);number("b_offset",B_OFFSET);
    number("a_bytes",A_BYTES);number("b_bytes",B_BYTES);number("y_bytes",Y_WORDS*4);number("y_offset",Y_OFFSET*4);
    hex32("a_addr",r[8]);hex32("b_addr",r[9]);hex32("y_addr",r[10]);
    number("errors",r[21]);number("a_errors",r[22]);number("b_errors",r[23]);number("y_errors",r[24]);
    number("dot8_abi",r[25]);number("dot8_counter_abi",r[26]);number("cpu_abi",r[27]);
    number("dma_abi",r[28]);number("dma_counter_abi",r[29]);number("groups",r[30]);
    number("clock_hz",r[17]);number("l1",r[16]&1);number("sync_memory",(r[16]>>1)&1);
    number("line_words",r[18]);number("line_count",r[19]);number("memory_wait",r[20]);
    for(unsigned h=0;h<2;++h)for(unsigned n=0;n<14;++n) {
        aster_puts(h?",h1_":",h0_");aster_puts(cpu_events[n]);aster_puts("=0x");
        aster_put_hex32(r[33+28*h+2*n]);aster_put_hex32(r[32+28*h+2*n]);
    }
    for(unsigned n=0;n<14;++n) {
        aster_puts(",dma_");aster_puts(dma_events[n]);aster_puts("=0x");
        aster_put_hex32(r[89+2*n]);aster_put_hex32(r[88+2*n]);
    }
    for(unsigned h=0;h<2;++h)for(unsigned n=0;n<4;++n) {
        aster_puts(h?",h1_dot8_":",h0_dot8_");aster_puts(dot8_events[n]);aster_puts("=0x");
        aster_put_hex32(r[117+8*h+2*n]);aster_put_hex32(r[116+8*h+2*n]);
    }
    aster_puts(",output=");static const char digits[]="0123456789abcdef";
    const uint8_t *bytes=(const uint8_t *)(const void *)aster_dot8_bench_y;
    for(unsigned i=0;i<Y_WORDS*4;++i) {aster_putc(digits[bytes[i]>>4]);aster_putc(digits[bytes[i]&15]);}
    aster_putc('\n');
}
void aster_secondary_main(void) {for(;;)__asm__ volatile ("" ::: "memory");}
int main(void) {
    if(REG(0x20003084)!=4 || REG(0x20003184)!=4 || REG(0x30000184)!=5 || REG(0x20003284)!=6 ||
        REG(0x20003288)!=1 || REG(0x2000328c)!=0x0b || REG(0x20003290)!=0xfe00707f ||
        REG(0x20003294)!=4 || REG(0x2000329c)!=1 || (REG(0x2000200c)&2) || REG(0x30000010)) {
        aster_puts("DOT8 BENCH BAD ABI/TOPOLOGY\n");__asm__ volatile ("ebreak");for(;;){}
    }
    for(unsigned job=1;job<=DOT8_JOBS;++job)for(unsigned pass=0;pass<2;++pass) {
        unsigned method=((job-1)&1)^pass;uint32_t seed=DOT8_SEED^(job*0x9e3779b9u);
        prepare(seed);fence();REG(0x20003080)=1;fence();
        if(DOT8_KIND==0) {
            if(method)aster_dot8_custom_dot(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
            else aster_dot8_scalar_dot(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
        } else if(DOT8_KIND==1) {
            if(method)aster_dot8_custom_fir(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
            else aster_dot8_scalar_fir(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
        } else {
            if(method)aster_dot8_custom_gemm(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
            else aster_dot8_scalar_gemm(aster_dot8_bench_a+A_OFFSET,aster_dot8_bench_b+B_OFFSET,aster_dot8_bench_y+Y_OFFSET,DOT8_K);
        }
        fence();REG(0x20003080)=2;fence();
        volatile uint32_t *r=aster_dot8_bench_results[job-1][pass];
        for(unsigned bank=0;bank<4;++bank) {
            uint32_t base=bank<2?0x20003000u+bank*256:bank==2?0x30000100u:0x20003200u;
            for(unsigned n=0;n<(bank==3?16u:28u);++n)r[32+28*bank+n]=REG(base+n*4);
        }
        unsigned ae=0,be=0,ye=0;
        for(unsigned i=0;i<A_BYTES;++i)ae+=aster_dot8_bench_a[i]!=input_byte(i,seed,0);
        for(unsigned i=0;i<B_BYTES;++i)be+=aster_dot8_bench_b[i]!=input_byte(i,seed,1);
        for(unsigned i=0;i<Y_WORDS;++i) {
            uint32_t expected=i>=Y_OFFSET && i<Y_OFFSET+OUTPUTS?reference(i-Y_OFFSET,seed):guard(i,seed);
            ye+=aster_dot8_bench_y[i]!=expected;
        }
        r[0]=job;r[1]=method;r[2]=pass+1;r[3]=seed;r[4]=DOT8_KIND;r[5]=DOT8_K;r[6]=A_OFFSET;r[7]=B_OFFSET;
        r[8]=(uint32_t)(uintptr_t)(aster_dot8_bench_a+A_OFFSET);r[9]=(uint32_t)(uintptr_t)(aster_dot8_bench_b+B_OFFSET);
        r[10]=(uint32_t)(uintptr_t)(aster_dot8_bench_y+Y_OFFSET);r[11]=A_BYTES;r[12]=B_BYTES;r[13]=Y_WORDS*4;
        r[14]=OUTPUTS;r[15]=REG(0x20002008);r[16]=REG(0x3000018c);r[17]=REG(0x200032a0);
        r[18]=REG(0x30000190);r[19]=REG(0x30000194);r[20]=REG(0x30000198);
        r[21]=ae+be+ye;r[22]=ae;r[23]=be;r[24]=ye;r[25]=REG(0x20003288);r[26]=REG(0x20003284);
        r[27]=REG(0x20003084);r[28]=REG(0x3000001c);r[29]=REG(0x30000184);r[30]=(DOT8_K/4)*OUTPUTS;r[31]=0;
        emit(r);if(r[21]) {__asm__ volatile ("ebreak");for(;;){}}
    }
    return 0;
}
