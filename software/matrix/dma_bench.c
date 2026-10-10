// 20.4's matrix: the DMA (docs/matrix.md §4.3), one point in each simulation, through v1's driver
// (software/drivers/aster_dma.c, unchanged). DMA_CASE:
//   1 a copy, v1's window (software/benchmarks/dma.c): DMA_METHOD 0 v1's fair CPU copy, 1 the DMA
//     (aster_dma_copy); DMA_BYTES from source offset DMA_SRC_OFF to destination offset DMA_DST_OFF, with
//     DMA_DST_CACHED 1 the destination's lines read into hart 0's data cache just before the window
//   2 the overlap (new): a DMA copy of DMA_BYTES and an unrelated checksum over DMA_WORK words, DMA_METHOD
//     0 the copy then the checksum on hart 0, 1 the checksum on hart 0 while the DMA copies,
//     2 the copy then the checksum on hart 1, 3 the checksum on hart 1 while the DMA copies
//     (phase17-plus.md §4: polling alone is not freed time; hart 0 only polls while the DMA works for it)
// One window, e2e: a copy's set-up is the copy (matrix.md §10.10). The buffers have guard bytes on both sides;
// after the window every source and destination byte is checked, inside the copy and outside it, as v1 did.
// The DMA's own counters are the record's (v12): its busy cycles are the time hart 0 polls. A cold run
// (matrix_cold.h) is the first pass after reset; a warm run is the same code untimed first (and the buffers
// prepared again). Work intervals: a CPU copy's is hart 0's whole window; where hart 0 only submits and polls,
// 0 and 0; the overlap's checksum is stamped on the hart that does it.
#include <stdint.h>

#include "matrix_layout.h"
#include "aster.h"
#include "aster_dma.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef DMA_CASE
#define DMA_CASE 1
#endif
#ifndef DMA_METHOD
#define DMA_METHOD 1
#endif
#ifndef DMA_BYTES
#define DMA_BYTES 1024u
#endif
#ifndef DMA_SRC_OFF
#define DMA_SRC_OFF 0u
#endif
#ifndef DMA_DST_OFF
#define DMA_DST_OFF 0u
#endif
#ifndef DMA_DST_CACHED
#define DMA_DST_CACHED 0u
#endif
#ifndef DMA_SEED
#define DMA_SEED 0x13570000u
#endif
#define GUARD 64u
#define BUFFER_BYTES ((GUARD + 8u + DMA_BYTES + GUARD + 63u) & ~63u)
#ifndef DMA_WORK
#define DMA_WORK 2048u                                   // the overlap's checksum: 8 KiB (or 1 KiB: 256)
#endif
#define WORK_WORDS DMA_WORK
#define USES_DMA (DMA_CASE == 2 || DMA_METHOD == 1)
#define HART1_WORK (DMA_CASE == 2 && DMA_METHOD >= 2)

MATRIX_ROOM(source, sizeof(uint8_t[BUFFER_BYTES]), 64);
#define source MATRIX_AT(uint8_t, source, BUFFER_BYTES)
MATRIX_ROOM(destination, sizeof(uint8_t[BUFFER_BYTES]), 64);
#define destination MATRIX_AT(uint8_t, destination, BUFFER_BYTES)
#if DMA_CASE == 2
MATRIX_ROOM(work, sizeof(uint32_t[WORK_WORDS]), 64);
#define work MATRIX_AT(uint32_t, work, WORK_WORDS)
static volatile uint32_t work_sum;
#endif
static volatile uint32_t driver_failed;

static uint8_t source_byte(uint32_t i) { return (uint8_t)(((DMA_SEED ^ (i * 0x9e3779b9u)) >> 11) & 0xffu); }
static uint8_t destination_byte(uint32_t i) { return (uint8_t)(0xa5u ^ i ^ (DMA_SEED >> 16)); }

#if DMA_CASE == 1 && DMA_METHOD == 0
typedef uint32_t alias_word __attribute__((__may_alias__));

// v1's fair CPU copy, verbatim (software/benchmarks/dma.c)
__attribute__((noipa)) static void cpu_memcpy(void *destination_, const void *source_, uint32_t size) {
    uint8_t *dst = destination_;
    const uint8_t *src = source_;
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
#endif

// the buffers as before every pass: the source's pattern, the destination's background (v1's), the
// destination's lines cached when the case asks
static __attribute__((noinline)) void prepare(void) {
    for (uint32_t i = 0; i < BUFFER_BYTES; ++i) { source[i] = source_byte(i); destination[i] = destination_byte(i); }
#if DMA_CASE == 2
    for (uint32_t i = 0; i < WORK_WORDS; ++i) work[i] = DMA_SEED ^ (i * 0x1021u);
#endif
    __asm__ volatile ("fence rw,rw" ::: "memory");
#if DMA_DST_CACHED
    volatile const uint32_t *d = (volatile const uint32_t *)(void *)destination;
    for (uint32_t i = 0; i < BUFFER_BYTES / 4u; i += 4u) (void)d[i];   // one read a line: hart 0's cache holds it
#endif
    if (aster_dma_acknowledge() != ASTER_DMA_OK) driver_failed = 1;    // (v1's: before each job)
}

#if DMA_CASE == 2
static __attribute__((noinline)) uint32_t checksum_work(void) {
    uint32_t sum = 0;
    for (uint32_t i = 0; i < WORK_WORDS; ++i) sum = (sum * 33u) ^ work[i];
    return sum;
}

__attribute__((unused)) static void hart1_work(void *arg) {                        // (methods 2 and 3)
    (void)arg;
    matrix_stamp_start(1);
    work_sum = checksum_work();
    matrix_stamp_end(1);
}
#endif

static uint8_t *const dst_at = destination + GUARD + DMA_DST_OFF;
static const uint8_t *const src_at = source + GUARD + DMA_SRC_OFF;

// the computation, in the window or not
static __attribute__((noinline)) void run(void) {
#if DMA_CASE == 1
#if DMA_METHOD == 1
    if (aster_dma_copy(dst_at, src_at, DMA_BYTES, 8000000u) != ASTER_DMA_OK) driver_failed = 1;
    v12_note_dma_job();
#else
    cpu_memcpy(dst_at, src_at, DMA_BYTES);
#endif
#else
    matrix_last = 1;                                   // (one stretch)
#if DMA_METHOD == 0
    if (aster_dma_copy(dst_at, src_at, DMA_BYTES, 8000000u) != ASTER_DMA_OK) driver_failed = 1;
    v12_note_dma_job();
    v12_work_start(0);
    work_sum = checksum_work();
    v12_work_end(0);
#elif DMA_METHOD == 1
    enum aster_dma_result r = aster_dma_submit(dst_at, src_at, DMA_BYTES);
    v12_note_dma_job();
    v12_work_start(0);
    work_sum = checksum_work();
    v12_work_end(0);
    if (r == ASTER_DMA_PENDING) r = aster_dma_wait(8000000u);
    if (r != ASTER_DMA_OK) driver_failed = 1;
#elif DMA_METHOD == 2
    if (aster_dma_copy(dst_at, src_at, DMA_BYTES, 8000000u) != ASTER_DMA_OK) driver_failed = 1;
    v12_note_dma_job();
    aster_smp_dispatch(hart1_work, 0);
    aster_smp_join();
#else
    enum aster_dma_result r = aster_dma_submit(dst_at, src_at, DMA_BYTES);
    v12_note_dma_job();
    aster_smp_dispatch(hart1_work, 0);
    if (r == ASTER_DMA_PENDING) r = aster_dma_wait(8000000u);
    if (r != ASTER_DMA_OK) driver_failed = 1;
    aster_smp_join();
#endif
#endif
}

// the window, the same code for the warm-up and the timed pass (its START and FREEZE warm too)
static __attribute__((noinline)) void window(void) {
    matrix_open(1);
    run();
    matrix_close();
}

int main(void) {
    static struct v12_record record;
#if HART1_WORK
    aster_smp_start();
#endif
    if (!matrix_cold) {                                // the window's warm-up
        prepare();
        window();
    }
    driver_failed = 0;
    prepare();
    matrix_stamps_clear();
    v12_prepare();
    window();
    v12_end(&record);
#if DMA_CASE == 1 && DMA_METHOD == 0
    matrix_hart0_whole(&record);                       // (the CPU copy is hart 0's, all of the window)
#elif DMA_CASE == 1 || HART1_WORK
    matrix_hart0_none(&record);                        // (hart 0 only submits, dispatches and polls)
#endif
    uint32_t errors = 0, checksum = 0;                 // v1's check: every byte, the copy's and the guards'
    for (uint32_t i = 0; i < BUFFER_BYTES; ++i) {
        errors += source[i] != source_byte(i);
        const uint32_t in = i >= GUARD + DMA_DST_OFF && i < GUARD + DMA_DST_OFF + DMA_BYTES;
        const uint8_t want = in ? source_byte(i - DMA_DST_OFF + DMA_SRC_OFF) : destination_byte(i);
        errors += destination[i] != want;
        if (in) checksum = (checksum * 33u) ^ destination[i];
    }
#if DMA_CASE == 2
    checksum = (checksum * 33u) ^ work_sum;
#endif
    static const char *const names[2][4] = {{"copy", "copy", "", ""},
                                            {"overlap_serial_h0", "overlap_h0", "overlap_serial_h1", "overlap_h1"}};
    record.name = names[DMA_CASE - 1][DMA_METHOD]; record.family = "dma";
    record.method = USES_DMA ? "dma" : "cpu_copy"; record.window = "e2e"; record.cache_state = MATRIX_CACHE_STATE;
    record.size = DMA_BYTES; record.iterations = 1;
    record.param = DMA_CASE == 2 ? WORK_WORDS : (DMA_DST_CACHED << 8) | (DMA_SRC_OFF << 4) | DMA_DST_OFF;
    record.seed = DMA_SEED; record.checksum = checksum; record.workers = HART1_WORK ? 2u : 1u;
    record.pass = errors == 0 && !driver_failed;
    v12_emit(&record);
    return !record.pass;
}
