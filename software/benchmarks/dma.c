// AsterBench v5: paired same-buffer CPU memcpy versus actual coherent DMA.
// See docs/phase7-bench.md. UART/checking/preparation are outside the window.
#include "aster.h"
#include "aster_dma.h"

#ifndef DMA_BYTES
#define DMA_BYTES 64
#endif
#ifndef DMA_ALIGNMENT
#define DMA_ALIGNMENT 0
#endif
#ifndef DMA_JOBS
#define DMA_JOBS 4
#endif
#ifndef DMA_SEED
#define DMA_SEED 0x13570000u
#endif
_Static_assert(DMA_BYTES >= 0 && DMA_BYTES <= 8192, "DMA study supports 0..8192 bytes");
_Static_assert(DMA_ALIGNMENT >= 0 && DMA_ALIGNMENT <= 2, "alignment must be aligned/same-offset/different-offset");
_Static_assert(DMA_JOBS >= 1 && DMA_JOBS <= 8, "jobs must be 1..8");
enum {
    BUFFER_BYTES = (DMA_BYTES+131+63)&~63,
    SOURCE_OFFSET = 64+(DMA_ALIGNMENT != 0),
    DESTINATION_OFFSET = 64+(DMA_ALIGNMENT == 1 ? 1 : DMA_ALIGNMENT == 2 ? 2 : 0),
    RECORD_WORDS = 108
};
#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define PERF 0x20003000u
#define WINDOW (PERF+0x80u)
uint8_t aster_dma_bench_source[BUFFER_BYTES] __attribute__((section(".dma_source"),aligned(64)));
uint8_t aster_dma_bench_destination[BUFFER_BYTES] __attribute__((section(".dma_destination"),aligned(64)));
volatile uint32_t aster_dma_bench_results[DMA_JOBS][2][RECORD_WORDS]
    __attribute__((section(".private0")));

static const char *const cpu_events[] = {"cycles","retired","memory","i_access","i_miss","d_access","d_miss",
    "backing","atomic","sc_success","sc_failure","intervention","invalidation","writeback"};
static const char *const dma_events[] = {"busy","wait","reads","writes","bytes","backing_reads","backing_writes",
    "forwards","dirty_words","invalidations","success","aborts","errors","rejected"};
static const char *const alignments[] = {"aligned","same_offset","different_offset"};
typedef uint32_t alias_word __attribute__((__may_alias__));

__attribute__((noipa,section(".text.benchmark")))
void aster_dma_cpu_memcpy(void *destination, const void *source, uint32_t size) {
    uint8_t *dst = destination;
    const uint8_t *src = source;
    // Matching offsets become aligned after at most three bytes. Different
    // offsets use the explicit byte path; no unaligned RV32 loads are assumed.
    while (size && (((uintptr_t)src | (uintptr_t)dst) & 3)) { *dst++ = *src++; --size; }
    while (size >= 16) {
        alias_word *d = (alias_word *)(void *)dst;
        const alias_word *s = (const alias_word *)(const void *)src;
        d[0] = s[0]; d[1] = s[1]; d[2] = s[2]; d[3] = s[3];
        dst += 16; src += 16; size -= 16;
    }
    while (size >= 4) {
        *(alias_word *)(void *)dst = *(const alias_word *)(const void *)src;
        dst += 4; src += 4; size -= 4;
    }
    while (size) { *dst++ = *src++; --size; }
}
static void fence_io(void) { __asm__ volatile ("fence iorw,iorw" ::: "memory"); }
static uint8_t source_byte(unsigned i, uint32_t seed) {
    return (uint8_t)((seed >> ((i&3)*8)) ^ (i*73u) ^ (i>>3));
}
static uint8_t destination_byte(unsigned i, uint32_t seed) { return (uint8_t)(0xa5u ^ i ^ (seed>>16)); }
static void prepare(uint32_t seed) {
    for (unsigned i = 0; i < BUFFER_BYTES; ++i) {
        aster_dma_bench_source[i] = source_byte(i,seed);
        aster_dma_bench_destination[i] = destination_byte(i,seed);
    }
    fence_io();
}
static void decimal(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static void hex32(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static void emit(unsigned job, unsigned pass, volatile uint32_t *r) {
    aster_puts("ASTERBENCH,version=5,name=dma_memcpy,window=setup_copy_complete,policy=prepared_reinitialize,status=");
    aster_puts(r[10] ? "FAIL" : "PASS");
    aster_puts(",method="); aster_puts(r[1] ? "dma" : "cpu");
    aster_puts(",order="); aster_puts(job&1 ? "cpu_dma" : "dma_cpu");
    aster_puts(",alignment="); aster_puts(alignments[DMA_ALIGNMENT]);
    decimal("size",DMA_BYTES); decimal("jobs",DMA_JOBS); decimal("job",job); decimal("pass",pass+1);
    hex32("base_seed",DMA_SEED); hex32("seed",r[3]); decimal("harts",r[15]); decimal("workers",1);
    decimal("source_offset",SOURCE_OFFSET); decimal("destination_offset",DESTINATION_OFFSET);
    decimal("buffer_bytes",BUFFER_BYTES); hex32("source_addr",r[7]); hex32("destination_addr",r[8]);
    decimal("errors",r[10]); decimal("source_errors",r[21]); decimal("destination_errors",r[22]);
    hex32("driver_result",r[23]); decimal("clock_hz",r[17]); decimal("l1",r[16]&1);
    decimal("sync_memory",(r[16]>>1)&1); decimal("line_words",r[18]); decimal("line_count",r[19]);
    decimal("memory_wait",r[20]); decimal("cpu_abi",REG(PERF+0x84));
    decimal("dma_abi",REG(ASTER_DMA_BASE+0x1c)); decimal("dma_counter_abi",REG(ASTER_DMA_BASE+0x184));
    decimal("raw_dma_status",r[11]); decimal("raw_dma_bytes_done",r[12]);
    aster_puts(",raw_dma_job_cycles=0x"); aster_put_hex32(r[14]); aster_put_hex32(r[13]);
    for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i) {
        aster_puts(h ? ",h1_" : ",h0_"); aster_puts(cpu_events[i]); aster_puts("=0x");
        aster_put_hex32(r[25+28*h+2*i]); aster_put_hex32(r[24+28*h+2*i]);
    }
    for (unsigned i = 0; i < 14; ++i) {
        aster_puts(",dma_"); aster_puts(dma_events[i]); aster_puts("=0x");
        aster_put_hex32(r[81+2*i]); aster_put_hex32(r[80+2*i]);
    }
    aster_puts(",output=");
    static const char digits[] = "0123456789abcdef";
    for (unsigned i = 0; i < BUFFER_BYTES; ++i) {
        uint8_t byte = aster_dma_bench_destination[i];
        aster_putc(digits[byte>>4]); aster_putc(digits[byte&15]);
    }
    aster_putc('\n');
}
void aster_secondary_main(void) { for (;;) __asm__ volatile ("" ::: "memory"); }
int main(void) {
    if (REG(PERF+0x84) != 4 || REG(PERF+0x184) != 4 || REG(ASTER_DMA_BASE+0x184) != 5 ||
        !(REG(ASTER_DMA_BASE+0x18c)&4) || (REG(0x2000200c)&2)) {
        aster_puts("DMA BENCH BAD ABI/TOPOLOGY\n"); __asm__ volatile ("ebreak"); for (;;) {}
    }
    for (unsigned job = 1; job <= DMA_JOBS; ++job) for (unsigned pass = 0; pass < 2; ++pass) {
        unsigned method = ((job-1)&1) ^ pass;
        uint32_t seed = DMA_SEED ^ (job*0x9e3779b9u);
        if (aster_dma_acknowledge() != ASTER_DMA_OK) {
            aster_puts("DMA BENCH ACK FAIL\n"); __asm__ volatile ("ebreak"); for (;;) {}
        }
        prepare(seed);
        enum aster_dma_result driver_result = ASTER_DMA_OK;
        fence_io(); REG(WINDOW) = 1; fence_io();
        if (method) driver_result = aster_dma_copy(aster_dma_bench_destination+DESTINATION_OFFSET,
            aster_dma_bench_source+SOURCE_OFFSET,DMA_BYTES,2000000);
        else aster_dma_cpu_memcpy(aster_dma_bench_destination+DESTINATION_OFFSET,
            aster_dma_bench_source+SOURCE_OFFSET,DMA_BYTES);
        fence_io(); REG(WINDOW) = 2; fence_io();
        volatile uint32_t *r = aster_dma_bench_results[job-1][pass];
        for (unsigned bank = 0; bank < 3; ++bank) {
            uint32_t address = bank < 2 ? PERF+bank*256 : ASTER_DMA_BASE+256;
            for (unsigned i = 0; i < 28; ++i) r[24+28*bank+i] = REG(address+i*4);
        }
        uint32_t source_errors = 0, destination_errors = 0;
        for (unsigned i = 0; i < BUFFER_BYTES; ++i) {
            source_errors += aster_dma_bench_source[i] != source_byte(i,seed);
            uint8_t expected = i >= DESTINATION_OFFSET && i < DESTINATION_OFFSET+DMA_BYTES
                ? source_byte(SOURCE_OFFSET+i-DESTINATION_OFFSET,seed) : destination_byte(i,seed);
            destination_errors += aster_dma_bench_destination[i] != expected;
        }
        uint64_t engine_cycles = aster_dma_job_cycles();
        r[0] = job; r[1] = method; r[2] = pass+1; r[3] = seed; r[4] = DMA_BYTES;
        r[5] = SOURCE_OFFSET; r[6] = DESTINATION_OFFSET;
        r[7] = (uint32_t)(uintptr_t)(aster_dma_bench_source+SOURCE_OFFSET);
        r[8] = (uint32_t)(uintptr_t)(aster_dma_bench_destination+DESTINATION_OFFSET);
        r[9] = BUFFER_BYTES; r[10] = source_errors+destination_errors+(driver_result != ASTER_DMA_OK);
        r[11] = aster_dma_status(); r[12] = aster_dma_bytes_done(); r[13] = engine_cycles; r[14] = engine_cycles>>32;
        r[15] = REG(0x20002008); r[16] = REG(ASTER_DMA_BASE+0x18c); r[17] = REG(ASTER_DMA_BASE+0x188);
        r[18] = REG(ASTER_DMA_BASE+0x190); r[19] = REG(ASTER_DMA_BASE+0x194); r[20] = REG(ASTER_DMA_BASE+0x198);
        r[21] = source_errors; r[22] = destination_errors; r[23] = (uint32_t)driver_result;
        emit(job,pass,r);
        if (r[10]) { __asm__ volatile ("ebreak"); for (;;) {} }
    }
    return 0;
}
