// Phase 20.3: the DMA in the two-hart SoC (docs/soc.md §6), through v1's driver
// (software/drivers/aster_dma.c), unchanged, on the device build. Each check
// that fails sets a distinct status (the failing check's number):
//  1-9    identity: ABI 1, LIMIT_LO/LIMIT_HI (main memory), the counters'
//         metadata (ABI 5, 14 counters, the flags, the memory waits as ABI 4's);
// 10-19   the DMA against CPU copies, at every size and alignment: every
//         length 0-40 at all 64 alignment pairs, and lengths up to 4 KiB at
//         every pair, each destination equal to a CPU copy of the source,
//         its guards unchanged, its lines read into hart 0's cache first (so
//         the DMA's writes must invalidate them);
// 20-29   errors through the driver: overlap, the source and the destination
//         outside main memory, a 33-bit end; LENGTH 0; and copies at the
//         limits, which pass: a source from LIMIT_LO, a source and a
//         destination ending at LIMIT_HI (hart 1's stack, before it runs);
//         a 16,000-byte copy, misaligned (into hart 1's memory, before it
//         runs);
// 30-39   ABORT through the driver: BYTES_DONE a prefix of the source, the
//         rest of the destination unchanged;
// 40-49   the completion interrupt (source 1) to hart 0;
// 50-59   the counters (ABI 5) over a window: two jobs' reads, writes,
//         bytes, busy cycles, completions and a rejected START exactly; their
//         accepted reads and writes, their stalls and the lines they
//         invalidated equal to the fabric counters' independent counts (R and
//         W accepted and waiting, snoop hits on W's port);
// 60-64   hart 1: the driver refuses it (not the owner), its writes to the
//         DMA's registers are ignored, and its cache sees the DMA's writes
//         (lines it holds, each invalidated by the DMA, read again);
// 65-69   the DMA under contention: copies (their own checks 75-77) while
//         hart 1 streams through its own buffer (every load checked), then a
//         4 KiB job while the NPU runs a GEMM (checked whole, still running
//         when the job ends) as well; the fabric counters showing the DMA
//         waited; and RESUMEs while counting during that job, whose cycles
//         no counter adds (the devices' rule), the counters' cross-checks
//         again exact;
// 70      the timing table's copies (they must succeed);
// 80      no exception taken anywhere.
// (49: the counters' forwards and write-backs, which stay 0.)
// Prints the sweep's count and the DMA against the CPU copy in cycles, by size,
// aligned and not, then ends with 1 to tohost (pass) or (first failing check
// << 1) | 1.
#include <stdint.h>

#include "aster.h"
#include "aster_dma.h"
#include "aster_npu2.h"
#include "aster_smp.h"

#define DMA(offset) (*(volatile uint32_t *)(uintptr_t)(0x30000000u + (offset)))
#define PERF_COMMAND (*(volatile uint32_t *)0x20003080u)
#define FABRIC_CTR(k) (*(volatile uint32_t *)(uintptr_t)(0x20003300u + 8u * (k)))
#define POLLS 4000000u

static int status;
static void check(int ok, int code) { if (!ok && !status) status = code; }

#define BUF 4352u                                    // 4 KiB and room for every offset and the guards
static uint8_t src[BUF] __attribute__((aligned(64)));
static uint8_t dst[BUF] __attribute__((aligned(64)));
static uint8_t ref[BUF] __attribute__((aligned(64)));

static inline uint32_t cycles(void) {
    uint32_t c;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(c));
    return c;
}
static uint32_t mix(uint32_t x) { x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; return x; }

// the CPU's copy: a word loop with a byte prefix and tail (v1's fair CPU kernel)
static void cpu_copy(uint8_t *d, const uint8_t *s, uint32_t n) {
    while (n && (((uintptr_t)d & 3u) || ((uintptr_t)s & 3u))) { *d++ = *s++; --n; }
    for (; n >= 4; n -= 4, d += 4, s += 4) *(uint32_t *)d = *(const uint32_t *)s;
    while (n--) *d++ = *s++;
}

// one copy by the DMA, against a CPU copy of the same source: the destination and its guards
static void one_copy(uint32_t s_off, uint32_t d_off, uint32_t len, uint32_t seed, int code) {
    const uint32_t guard = 16;
    for (uint32_t i = 0; i < len + 2 * guard + 8; ++i) {
        src[i] = (uint8_t)mix(seed + i);
        dst[i] = (uint8_t)~mix(seed * 7 + i);
        ref[i] = dst[i];
    }
    volatile uint32_t sink = 0;                       // hart 0's cache holds the destination's lines
    for (uint32_t i = 0; i < len + 2 * guard + 8; i += 16) sink += dst[i];
    (void)sink;
    cpu_copy(ref + guard + d_off, src + s_off, len);
    const enum aster_dma_result r = aster_dma_copy(dst + guard + d_off, src + s_off, len, POLLS);
    check(r == ASTER_DMA_OK, code);
    check(aster_dma_bytes_done() == len, code + 1);
    for (uint32_t i = 0; i < len + 2 * guard + 8; ++i) if (dst[i] != ref[i]) { check(0, code + 2); break; }
}

static uint32_t shared[256] __attribute__((aligned(64)));   // read by hart 1, rewritten by the DMA
static void hart1_sums(void *arg) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < 256; ++i) sum += shared[i] * (i + 1);
    *(volatile uint32_t *)arg = sum;
}

// hart 1's traffic: passes over 8 KiB of its private memory (twice its data cache), each word checked
// and rewritten, until hart 0 stops it between passes
static uint32_t traffic[2048] __attribute__((aligned(64), section(".private1")));
static volatile uint32_t stop_traffic;
static void hart1_traffic(void *arg) {
    volatile uint32_t *out = (volatile uint32_t *)arg;
    uint32_t pass = 0, wrong = 0;
    do {
        for (uint32_t i = 0; i < 2048; ++i) {
            if (traffic[i] != mix(i + pass)) ++wrong;
            traffic[i] = mix(i + pass + 1);
        }
        ++pass;
    } while (!stop_traffic);
    out[0] = wrong; out[1] = pass;
}

static void hart1_tries(void *arg) {
    volatile uint32_t *result = (volatile uint32_t *)arg;
    result[0] = (uint32_t)aster_dma_submit(dst, src, 64);      // the driver: not the owner
    DMA(0x00) = 0xDEAD0000u;                                    // ignored
    DMA(0x0C) = 1u;
    result[1] = 1;
}

static volatile uint32_t dma_irqs, exceptions;
void aster_exception(uint32_t mcause, uint32_t mepc, uint32_t mtval) {
    (void)mcause; (void)mtval;
    ++exceptions;                                               // none is expected: counted, then failed
    __asm__ volatile ("csrw mepc, %0" :: "r"(mepc + 4u));
}
void aster_irq_dispatch(void) {
    uint32_t hart;
    __asm__ volatile ("csrr %0, mhartid" : "=r"(hart));
    if (hart == 0 && (*ASTER_IRQ_ACTIVE0 & ASTER_IRQ_DMA)) {
        *ASTER_IRQ_PENDING = ASTER_IRQ_DMA;
        ++dma_irqs;
    }
}

int main(void) {
    // 1-9 identity
    check(DMA(0x1C) == 1u, 1);
    check(DMA(0x28) == 0x80000000u && DMA(0x2C) == 0x80018000u, 2);
    check(DMA(0x184) == 5u && DMA(0x19C) == 14u && DMA(0x18C) == 7u, 3);
    check(DMA(0x198) == *(volatile uint32_t *)0x20003098u, 4);
    check(DMA(0x10) == 0u, 5);

    // 10-19 the sweep: every length 0-40 at every alignment pair, then larger lengths at every pair
    uint32_t jobs = 0;
    for (uint32_t len = 0; len <= 40 && !status; ++len)
        for (uint32_t s = 0; s < 8; ++s)
            for (uint32_t d = 0; d < 8; ++d) { one_copy(s, d, len, len * 64 + s * 8 + d, 10); ++jobs; }
    static const uint32_t big[] = {63, 64, 100, 127, 128, 255, 256, 511, 1000, 1024, 2047, 4096};
    for (uint32_t i = 0; i < sizeof big / sizeof big[0] && !status; ++i)
        for (uint32_t s = 0; s < 8; ++s)
            for (uint32_t d = 0; d < 8; ++d) { one_copy(s, d, big[i], big[i] + s * 8 + d, 13); ++jobs; }

    // 20-29 errors (the driver's results) and LENGTH 0
    check(aster_dma_copy(src + 8, src, 64, POLLS) == ASTER_DMA_OVERLAP, 20);
    check(aster_dma_copy(dst, (const void *)0x7FFFFFF0u, 64, POLLS) == ASTER_DMA_BAD_SOURCE, 21);
    check(aster_dma_copy((void *)0x80017FF0u, src, 64, POLLS) == ASTER_DMA_BAD_DESTINATION, 22);
    check(aster_dma_copy(dst, src, 0xFFFFFFF0u, POLLS) == ASTER_DMA_BAD_SOURCE, 23);
    check(aster_dma_copy(dst, src, 0, POLLS) == ASTER_DMA_OK && aster_dma_bytes_done() == 0u, 24);
    check(aster_dma_acknowledge() == ASTER_DMA_OK, 25);
    // at the limits, passing: hart 1 has not run, so its stack's top (main memory's last bytes) is free
    volatile uint8_t *const last = (volatile uint8_t *)(0x80018000u - 64u);   // main memory's last 64 bytes
    for (uint32_t i = 0; i < 64; ++i) { last[i] = (uint8_t)mix(i + 1234); dst[i] = 0; }
    check(aster_dma_copy(dst + 5, (const void *)(last + 35), 29, POLLS) == ASTER_DMA_OK, 26);   // ending at LIMIT_HI
    for (uint32_t i = 0; i < 29; ++i) if (dst[5 + i] != last[35 + i]) { check(0, 26); break; }
    for (uint32_t i = 0; i < 64; ++i) if ((i < 5 || i >= 34) && dst[i] != 0) { check(0, 26); break; }   // its guards
    for (uint32_t i = 0; i < 64; ++i) src[i] = (uint8_t)mix(i + 777);
    check(aster_dma_copy((void *)(last + 37), src + 2, 27, POLLS) == ASTER_DMA_OK, 27);   // a destination ending there
    for (uint32_t i = 0; i < 27; ++i) if (last[37 + i] != src[2 + i]) { check(0, 27); break; }
    for (uint32_t i = 0; i < 37; ++i) if (last[i] != (uint8_t)mix(i + 1234)) { check(0, 27); break; }   // the rest kept
    check(aster_dma_copy(dst, (const void *)0x80000000u, 100, POLLS) == ASTER_DMA_OK, 28);   // from LIMIT_LO
    for (uint32_t i = 0; i < 100; ++i) if (dst[i] != ((const uint8_t *)0x80000000u)[i]) { check(0, 28); break; }
    // a large copy (2,001 source units): the program's image to hart 1's memory, its guards unchanged
    volatile uint8_t *const big_dst = (volatile uint8_t *)0x80014000u;
    const uint8_t *const big_src = (const uint8_t *)0x80000003u;
    for (uint32_t i = 0; i < 16016; ++i) big_dst[i] = 0x3C;
    check(aster_dma_copy((void *)(big_dst + 5), big_src, 16000, POLLS) == ASTER_DMA_OK
          && aster_dma_bytes_done() == 16000u, 29);
    for (uint32_t i = 0; i < 16016; ++i)
        if (big_dst[i] != (i < 5 || i >= 16005 ? 0x3C : big_src[i - 5])) { check(0, 29); break; }

    // 30-39 ABORT: a long copy, aborted after a few polls
    for (uint32_t i = 0; i < 4096; ++i) { src[i] = (uint8_t)mix(i + 99); dst[i] = 0xA5; }
    check(aster_dma_submit(dst + 3, src + 1, 4000) == ASTER_DMA_PENDING, 30);
    for (volatile int spin = 0; spin < 20; ++spin) {}
    const enum aster_dma_result ar = aster_dma_abort_and_wait(POLLS);
    check(ar == ASTER_DMA_WAS_ABORTED || ar == ASTER_DMA_OK, 31);
    const uint32_t prefix = aster_dma_bytes_done();
    check(prefix <= 4000u && (ar == ASTER_DMA_OK) == (prefix == 4000u), 32);
    for (uint32_t i = 0; i < prefix; ++i) if (dst[3 + i] != src[1 + i]) { check(0, 33); break; }
    for (uint32_t i = prefix; i < 4000; ++i) if (dst[3 + i] != 0xA5) { check(0, 34); break; }
    check(dst[0] == 0xA5 && dst[1] == 0xA5 && dst[2] == 0xA5 && dst[4003] == 0xA5, 35);
    check(ar == ASTER_DMA_WAS_ABORTED, 36);                     // (aborted well before its end)
    aster_dma_acknowledge();

    // 40-49 the completion interrupt to hart 0
    *ASTER_IRQ_PENDING = 0xFu;
    *ASTER_IRQ_ENABLE0 = ASTER_IRQ_DMA;
    __asm__ volatile ("csrs mie, %0" :: "r"(1u << 11));
    __asm__ volatile ("csrsi mstatus, 8");
    check(aster_dma_submit(dst, src, 512) == ASTER_DMA_PENDING, 40);
    for (uint32_t spin = 0; spin < 100000 && dma_irqs == 0; ++spin) {}
    check(dma_irqs == 1u, 41);
    check(aster_dma_poll() == ASTER_DMA_OK, 42);
    __asm__ volatile ("csrci mstatus, 8");
    *ASTER_IRQ_ENABLE0 = 0;
    aster_dma_acknowledge();

    // 50-59 the counters over a window: one job, 777 bytes from offset 5 to offset 2
    for (uint32_t i = 0; i < 1024; i += 16) { volatile uint8_t t = dst[i]; (void)t; }   // its lines cached
    PERF_COMMAND = 1;                                           // START
    check(aster_dma_copy(dst + 2, src + 5, 777, POLLS) == ASTER_DMA_OK, 50);
    const uint32_t job = (uint32_t)aster_dma_job_cycles();
    DMA(0x00) = 0;                                              // idle: accepted (not a rejection)
    check(aster_dma_submit(dst, src, 2048) == ASTER_DMA_PENDING, 51);
    DMA(0x0C) = 1u;                                             // START while busy: rejected
    check(aster_dma_wait(POLLS) == ASTER_DMA_COMMAND_REJECTED, 52);
    PERF_COMMAND = 2;                                           // FREEZE
    const uint32_t ns1 = (777 + 5 + 7) / 8, nd1 = (777 + 2 + 7) / 8, ns2 = 2048 / 8, nd2 = 2048 / 8;
    check(DMA(0x128) == ns1 + ns2 && DMA(0x130) == nd1 + nd2, 53);     // backing reads, writes
    check(DMA(0x110) == ns1 + ns2 && DMA(0x118) == nd1 + nd2, 54);     // completed reads, writes
    check(DMA(0x120) == 777u + 2048u, 55);                              // payload bytes
    check(DMA(0x150) == 2u && DMA(0x158) == 0u && DMA(0x160) == 0u && DMA(0x168) == 1u, 56);  // done, aborts, errors, rejects
    check(DMA(0x100) == job + (uint32_t)aster_dma_job_cycles(), 57);   // busy cycles: the jobs' cycles
    // against the fabric counters, over the same window: R and W accepted (requesters 5, 6), and the
    // snoop hits on W's port (2) in both data caches
    check(DMA(0x128) == FABRIC_CTR(5) && DMA(0x130) == FABRIC_CTR(6), 58);
    check(DMA(0x108) == FABRIC_CTR(7 + 5) + FABRIC_CTR(7 + 6), 58);    // stalls: R and W waiting
    check(DMA(0x148) >= 1u && DMA(0x148) == FABRIC_CTR(32 + 3 * 0 + 2) + FABRIC_CTR(32 + 3 * 1 + 2), 59);
    check(DMA(0x138) == 0u && DMA(0x140) == 0u, 49);                    // no forwards, no write-backs
    aster_dma_acknowledge();

    // 60-69 hart 1
    static volatile uint32_t result[2];
    DMA(0x00) = 0x80001000u;
    aster_smp_start();
    aster_smp_dispatch(hart1_tries, (void *)result);
    aster_smp_join();
    check(result[1] == 1u, 60);
    check(result[0] == (uint32_t)ASTER_DMA_NOT_OWNER, 61);
    check(DMA(0x00) == 0x80001000u && DMA(0x10) == 0u, 62);   // (the ACK above cleared every flag)
    // hart 1's cache sees the DMA's writes: it reads shared (holding its lines), the DMA rewrites it,
    // and hart 1 reads it again
    static volatile uint32_t sum1;
    for (uint32_t i = 0; i < 256; ++i) shared[i] = mix(i + 5);
    aster_smp_dispatch(hart1_sums, (void *)&sum1);
    aster_smp_join();
    uint32_t want = 0;
    for (uint32_t i = 0; i < 256; ++i) { src[i * 4 + 0] = (uint8_t)mix(i * 3); src[i * 4 + 1] = (uint8_t)(i * 7);
                                          src[i * 4 + 2] = (uint8_t)(i ^ 0x5A); src[i * 4 + 3] = (uint8_t)(i + 1); }
    PERF_COMMAND = 1;                                           // (a window: hart 1's lines invalidated)
    check(aster_dma_copy(shared, src, 1024, POLLS) == ASTER_DMA_OK, 63);
    PERF_COMMAND = 2;
    const uint32_t hart1_lines = FABRIC_CTR(32 + 3 * 1 + 2);
    // (shared's 64 lines were in hart 1's cache, but for the few its idle loop's lines may have evicted:
    // the cache is direct-mapped)
    check(hart1_lines >= 56u && hart1_lines <= 1024u / 16u, 63);
    for (uint32_t i = 0; i < 256; ++i) want += (*(const uint32_t *)(src + 4 * i)) * (i + 1);
    aster_smp_dispatch(hart1_sums, (void *)&sum1);
    aster_smp_join();
    check(sum1 == want, 64);

    // 65-69 the DMA under contention: hart 1's traffic while hart 0 copies, then an NPU GEMM (32 x 32 x 64)
    // with a 4 KiB job as well
    static volatile uint32_t traffic_out[2];
    static int8_t na[32 * 64] __attribute__((aligned(16))), nb[64 * 32] __attribute__((aligned(16)));
    static int32_t nc[32 * 32] __attribute__((aligned(16)));
    for (uint32_t i = 0; i < 2048; ++i) traffic[i] = mix(i);
    for (uint32_t i = 0; i < 2048; ++i) { na[i] = (int8_t)mix(i + 11); nb[i] = (int8_t)mix(i + 23); }
    for (uint32_t i = 0; i < 1024; ++i) nc[i] = 0x5A5A5A5A;
    stop_traffic = 0;
    PERF_COMMAND = 1;
    aster_smp_dispatch(hart1_traffic, (void *)traffic_out);
    static const uint32_t busy[][3] = {{0, 0, 4096}, {3, 6, 1000}, {5, 1, 333}, {7, 7, 2047}, {1, 4, 64}, {6, 2, 4000}};
    uint32_t window_reads = 0;                                  // the source units the window's jobs read
    for (uint32_t i = 0; i < sizeof busy / sizeof busy[0]; ++i) {
        one_copy(busy[i][0], busy[i][1], busy[i][2], 4242 + i, 75);
        window_reads += (busy[i][2] + busy[i][0] + 7) / 8;
    }
    // RESUMEs while counting, during a job: their cycles are not counted, by the DMA's counters as by
    // the fabric's (so the reads counted fall short of the job's, and both counters still agree)
    for (uint32_t i = 0; i < 4096; ++i) src[i] = (uint8_t)mix(i + 31337);
    const struct aster_npu2_job gemm = {na, nb, nc, 64, 32, 128, 32, 32, 64, 0, 0, 0, 0, 0};
    aster_npu2_start(&gemm);
    check(aster_dma_submit(dst, src, 4096) == ASTER_DMA_PENDING, 65);
    for (uint32_t i = 0; i < 12; ++i) PERF_COMMAND = 4;
    check(aster_dma_wait(POLLS) == ASTER_DMA_OK, 65);
    const uint32_t npu_overlapped = ASTER_NPU2_REG(ASTER_NPU2_STATUS) & ASTER_NPU2_BUSY;
    check(npu_overlapped != 0u, 69);                            // (the NPU ran through the whole job)
    window_reads += 4096 / 8;
    for (uint32_t i = 0; i < 4096; ++i) if (dst[i] != src[i]) { check(0, 65); break; }
    const uint32_t npu_status = aster_npu2_wait();
    PERF_COMMAND = 2;
    stop_traffic = 1;
    aster_smp_join();
    aster_npu2_ack();
    check(traffic_out[0] == 0u && traffic_out[1] >= 1u, 68);
    for (uint32_t i = 0; i < 2048; ++i) if (traffic[i] != mix(i + traffic_out[1])) { check(0, 68); break; }
    check((npu_status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) == ASTER_NPU2_DONE, 69);
    for (uint32_t m = 0; m < 32; ++m)
        for (uint32_t n = 0; n < 32; ++n) {
            int32_t e = 0;
            for (uint32_t k = 0; k < 64; ++k) e += (int32_t)na[m * 64 + k] * (int32_t)nb[k * 32 + n];
            if (nc[m * 32 + n] != e) { check(0, 69); m = 32; break; }
        }
    check(DMA(0x128) == FABRIC_CTR(5) && DMA(0x130) == FABRIC_CTR(6), 67);
    check(DMA(0x108) == FABRIC_CTR(7 + 5) + FABRIC_CTR(7 + 6), 67);
    check(DMA(0x148) == FABRIC_CTR(32 + 3 * 0 + 2) + FABRIC_CTR(32 + 3 * 1 + 2), 67);
    const uint32_t stalls = DMA(0x108), n_accepted = FABRIC_CTR(4), h1_accepted = FABRIC_CTR(3);
    check(stalls >= 1u && n_accepted >= 1u && h1_accepted >= 1u, 66);   // the DMA waited for the others
    const uint32_t resume_dropped = window_reads - DMA(0x128);
    check(DMA(0x128) < window_reads, 66);                               // (a RESUME's cycle had a read)

    // the DMA against the CPU copy in cycles (end to end through the driver; the job's own cycles), by
    // size, aligned (both offsets 0) and not (the source at 3, the destination at 6): a measurement. The
    // table's second pass is kept (the first warms the instruction cache) and printed after it, so no
    // printing evicts the driver's code between copies; an untimed 8-byte copy before each timed one
    // brings the driver's stack back into the data cache after the CPU copy before it.
    aster_puts("DMA SWEEP jobs="); aster_put_u32(jobs);
    aster_puts(" abort_prefix="); aster_put_u32(prefix);
    aster_puts(" hart1_lines="); aster_put_u32(hart1_lines);
    aster_puts(" contention: dma_stalls="); aster_put_u32(stalls);
    aster_puts(" npu_accepted="); aster_put_u32(n_accepted);
    aster_puts(" hart1_accepted="); aster_put_u32(h1_accepted);
    aster_puts(" hart1_passes="); aster_put_u32(traffic_out[1]);
    aster_puts(" resume_dropped_reads="); aster_put_u32(resume_dropped);
    aster_puts("\n");
    static const uint32_t sizes[] = {16, 64, 256, 1024, 4096};
    static uint32_t timed[5][2][3];
    for (uint32_t pass = 0; pass < 2; ++pass)
    for (uint32_t i = 0; i < sizeof sizes / sizeof sizes[0]; ++i)
        for (uint32_t misaligned = 0; misaligned < 2; ++misaligned) {
            const uint32_t so = misaligned ? 3 : 0, doff = misaligned ? 6 : 0;
            check(aster_dma_copy(dst + 4200, src, 8, POLLS) == ASTER_DMA_OK, 70);   // (untimed: the driver warm)
            uint32_t t0 = cycles();
            check(aster_dma_copy(dst + doff, src + so, sizes[i], POLLS) == ASTER_DMA_OK, 70);
            const uint32_t dma_cycles = cycles() - t0, dma_job = (uint32_t)aster_dma_job_cycles();
            t0 = cycles();
            cpu_copy(ref + doff, src + so, sizes[i]);
            const uint32_t cpu_cycles = cycles() - t0;
            timed[i][misaligned][0] = dma_cycles; timed[i][misaligned][1] = dma_job; timed[i][misaligned][2] = cpu_cycles;
        }
    for (uint32_t i = 0; i < sizeof sizes / sizeof sizes[0]; ++i)
        for (uint32_t misaligned = 0; misaligned < 2; ++misaligned) {
            aster_puts("DMA COPY bytes="); aster_put_u32(sizes[i]);
            aster_puts(misaligned ? " misaligned" : " aligned");
            aster_puts(" dma_cycles="); aster_put_u32(timed[i][misaligned][0]);
            aster_puts(" dma_job_cycles="); aster_put_u32(timed[i][misaligned][1]);
            aster_puts(" cpu_cycles="); aster_put_u32(timed[i][misaligned][2]);
            aster_puts("\n");
        }
    check(exceptions == 0u, 80);
    aster_puts("SOC DMA "); aster_puts(status ? "FAIL check=" : "PASS");
    if (status) aster_put_u32((uint32_t)status);
    aster_puts("\n");
    return status;
}
