// 20.4's matrix: the memory hierarchy (docs/matrix.md §4.2), one point of a case in each simulation. MEM_CASE:
//   1 memcpy           v1's fair CPU copy (software/benchmarks/dma.c: words, with a byte prefix and tail; different
//                      offsets take the byte path), MEM_BYTES from source offset MEM_SRC_OFF to destination offset
//                      MEM_DST_OFF (v1's three: (0,0), (1,1), (1,2)), MEM_REPS times (memcpy_bench.c's), between
//                      32-byte guards; every byte checked after the window
//   2 read_sequential  v1's ring (software/benchmarks/memory_walk.c): one dependent read a hop, MEM_BYTES of links
//                      in order, MEM_REPS laps
//   3 read_strided     every MEM_STRIDE-th word over 16 KiB, offset by offset, so each word is read once a lap
//   4 walk_random      v1's ring shuffled (its seeded Fisher-Yates, the permutation beside the ring), MEM_REPS laps
//   5 working_set      the first MEM_BYTES of a 48 KiB buffer summed over and over: 49,152 reads at every size
//   6 stream           each hart sums the lines of one bank in its own 16 KiB half (4 KiB of it, a line in four),
//                      MEM_REPS laps: MEM_PATTERN 0 hart 0 alone (bank 0), 1 both harts in bank 0, 2 hart 0 in bank
//                      0 and hart 1 in bank 1 (soc.md §4.2: a 16-byte line's bank is address bits [5:4]). Whether
//                      two streams in one bank collide depends on their phase, so hart 1 starts MEM_STAGGER nops
//                      late: the matrix sweeps it over a line's period and reports the spread.
// A one-hart case has nothing outside its computation, so its two windows are one: each record is its e2e
// window (matrix.md §10.10). The streaming cases record e2e (the runtime's dispatch and join) and, warm, the
// kernel window (hart 1 armed, matrix_window.h). A cold run (matrix_cold.h) is the first pass after reset (the
// buffers are written before it, which the write-through, no-allocate data cache does not fill); a warm run is
// the same code untimed, then the window. memcpy's destination is poisoned before each timed pass. Each
// record's checksum is scripts/matrix.py's oracle's model of the computation; the rings are checked after the
// window, as v1 did.
#include <stdint.h>

#include "matrix_layout.h"
#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef MEM_CASE
#define MEM_CASE 1
#endif
#ifndef MEM_BYTES
#define MEM_BYTES 1024u
#endif
#ifndef MEM_SRC_OFF
#define MEM_SRC_OFF 0u
#endif
#ifndef MEM_DST_OFF
#define MEM_DST_OFF 0u
#endif
#ifndef MEM_STRIDE
#define MEM_STRIDE 1u
#endif
#ifndef MEM_PATTERN
#define MEM_PATTERN 1u
#endif
#ifndef MEM_SEED
#define MEM_SEED 0x13570000u
#endif
#ifndef MEM_STAGGER
#define MEM_STAGGER 0
#endif
#define STRINGIFY_(x) #x
#define STRINGIFY(x) STRINGIFY_(x)
#define MEM_REPS 4u
#define WORDS (MEM_BYTES / 4u)
#define WS_READS 49152u                                  // the working-set sweep's reads at every size
#define STRIDE_WORDS 4096u                               // 16 KiB
#define HALF_BYTES 16384u                                // the streaming case's half a hart
#define TWO_HARTS (MEM_CASE == 6 && MEM_PATTERN != 0)

enum { E2E = 1, KERNEL };

static uint32_t checksum;
static volatile uint32_t pass_ok = 1;

#if MEM_CASE == 1
// ---- memcpy: v1's fair CPU copy, verbatim ----
typedef uint32_t alias_word __attribute__((__may_alias__));

__attribute__((noipa)) static void cpu_memcpy(void *destination, const void *source, uint32_t size) {
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

#define GUARD 32u
#define COPY_BUFFER (GUARD + 4u + MEM_BYTES + GUARD)
MATRIX_ROOM(source, sizeof(uint8_t[COPY_BUFFER]), 64);
#define source MATRIX_AT(uint8_t, source, COPY_BUFFER)
MATRIX_ROOM(destination, sizeof(uint8_t[COPY_BUFFER]), 64);
#define destination MATRIX_AT(uint8_t, destination, COPY_BUFFER)

static uint8_t pattern(uint32_t i) { return (uint8_t)(((MEM_SEED ^ (i * 0x9e3779b9u)) >> 11) & 0xffu); }

static void setup(void) {
    for (uint32_t i = 0; i < COPY_BUFFER; ++i) source[i] = pattern(i);
}

static __attribute__((noinline)) void run(void) {
    for (uint32_t r = 0; r < MEM_REPS; ++r)
        cpu_memcpy(destination + GUARD + MEM_DST_OFF, source + GUARD + MEM_SRC_OFF, MEM_BYTES);
}

static void before_pass(void) { matrix_poison(destination, sizeof destination); }

static void after_pass(void) {                          // every byte: the copy, the guards, the source (v1's check)
    checksum = 0;
    for (uint32_t i = 0; i < COPY_BUFFER; ++i) {
        if (source[i] != pattern(i)) pass_ok = 0;
        const uint32_t in = i >= GUARD + MEM_DST_OFF && i < GUARD + MEM_DST_OFF + MEM_BYTES;
        if (destination[i] != (in ? pattern(i - MEM_DST_OFF + MEM_SRC_OFF) : 0xA5u)) pass_ok = 0;
        if (in) checksum = (checksum * 33u) ^ destination[i];
    }
}
#elif MEM_CASE == 2 || MEM_CASE == 4
// ---- v1's rings: one dependent read a hop ----
MATRIX_ROOM(links, sizeof(volatile uint32_t[WORDS]), 64);
#define links MATRIX_AT(volatile uint32_t, links, WORDS)
static uint32_t visited[(WORDS + 31u) / 32u];
#if MEM_CASE == 4
static uint32_t permutation[WORDS];
#endif
static volatile uint32_t observed_sum, observed_last;

static void setup(void) {
#if MEM_CASE == 4
    uint32_t state = MEM_SEED;
    for (uint32_t i = 0; i < WORDS; ++i) permutation[i] = i;
    for (uint32_t i = WORDS - 1u; i > 0; --i) {             // v1's full Fisher-Yates (its LCG)
        state = state * 1664525u + 1013904223u;
        const uint32_t j = state % (i + 1u);
        const uint32_t tmp = permutation[i]; permutation[i] = permutation[j]; permutation[j] = tmp;
    }
    for (uint32_t i = 0; i < WORDS; ++i) links[permutation[i]] = permutation[(i + 1u) % WORDS];
#else
    for (uint32_t i = 0; i < WORDS; ++i) links[i] = (i + 1u) % WORDS;
#endif
}

static __attribute__((noinline)) void run(void) {
    uint32_t position = 0, sum = 0;
    for (uint32_t i = 0; i < WORDS * MEM_REPS; ++i) {
        position = links[position];
        sum += position;
    }
    observed_last = position;
    observed_sum = sum;
}

static void before_pass(void) { observed_sum = 0; observed_last = 0xFFFFFFFFu; }

static void after_pass(void) {                          // v1's check: one ring through every node
    checksum = observed_sum;                            // (and the ring itself, folded: the oracle rebuilds it)
    for (uint32_t i = 0; i < WORDS; ++i) checksum = (checksum * 33u) ^ links[i];
    if (observed_last != 0) pass_ok = 0;
    for (uint32_t i = 0; i < (WORDS + 31u) / 32u; ++i) visited[i] = 0;
    uint32_t position = 0;
    for (uint32_t i = 0; i < WORDS; ++i) {
        if (position >= WORDS || (visited[position / 32u] & (1u << (position % 32u)))) { pass_ok = 0; break; }
        visited[position / 32u] |= 1u << (position % 32u);
        position = links[position];
    }
    if (position != 0) pass_ok = 0;
}
#elif MEM_CASE == 3 || MEM_CASE == 5
// ---- strided reads, and the working-set sweep ----
#if MEM_CASE == 3
#define BUFFER_WORDS STRIDE_WORDS
#else
#define BUFFER_WORDS (48u * 1024u / 4u)
#endif
MATRIX_ROOM(buffer, sizeof(uint32_t[BUFFER_WORDS]), 64);
#define buffer MATRIX_AT(uint32_t, buffer, BUFFER_WORDS)
static volatile uint32_t observed_sum;

static void setup(void) {
    for (uint32_t i = 0; i < BUFFER_WORDS; ++i) buffer[i] = MEM_SEED ^ (i * 0x1021u);
}

static __attribute__((noinline)) void run(void) {
    const volatile uint32_t *b = buffer;
    uint32_t sum = 0;
#if MEM_CASE == 3
    for (uint32_t r = 0; r < MEM_REPS; ++r)
        for (uint32_t o = 0; o < MEM_STRIDE; ++o)
            for (uint32_t i = o; i < STRIDE_WORDS; i += MEM_STRIDE) sum += b[i];
#else
    for (uint32_t p = 0; p < WS_READS / WORDS; ++p)
        for (uint32_t i = 0; i < WORDS; ++i) sum += b[i];
#endif
    observed_sum = sum;
}

static void before_pass(void) { observed_sum = 0; }
static void after_pass(void) { checksum = observed_sum; }
#else
// ---- two harts streaming, each the lines of one bank in its own half ----
MATRIX_ROOM(buffer, sizeof(uint32_t[2u * HALF_BYTES / 4u]), 64);
#define buffer MATRIX_AT(uint32_t, buffer, 2u * HALF_BYTES / 4u)
static volatile uint32_t part[2];

static void setup(void) {
    for (uint32_t i = 0; i < 2u * HALF_BYTES / 4u; ++i) buffer[i] = MEM_SEED ^ (i * 0x1021u);
}

// the lines of bank `bank` in hart `hart`'s half: four words each (a line is 16 bytes, a bank's every 64th)
static __attribute__((noinline)) uint32_t stream(uint32_t hart, uint32_t bank) {
    const volatile uint32_t *b = buffer + hart * (HALF_BYTES / 4u) + 4u * bank;
    uint32_t sum = 0;
    for (uint32_t r = 0; r < MEM_REPS; ++r)
        for (uint32_t line = 0; line < HALF_BYTES / 64u; ++line) {
            const volatile uint32_t *w = b + 16u * line;
            sum += w[0] + w[1] + w[2] + w[3];
        }
    return sum;
}

#if TWO_HARTS
static void hart1_stream(void *arg) {
    (void)arg;
    __asm__ volatile (".rept " STRINGIFY(MEM_STAGGER) "\n\tnop\n\t.endr" ::: "memory");   // (the stagger)
    matrix_stamp_start(1);
    part[1] = stream(1, MEM_PATTERN == 2 ? 1u : 0u);
    matrix_stamp_end(1);
}

static void hart1_poison(void *arg) { (void)arg; part[1] = 0xA5A5A5A5u; }   // (its own word: matrix_window.h)
#endif

static __attribute__((noinline)) void run_mode(int mode) {
#if TWO_HARTS
    matrix_last = 1;                                    // (one stretch a window)
    if (mode == KERNEL) {
        matrix_arm(hart1_stream, 0);
        matrix_open(1);
        matrix_release();
        part[0] = stream(0, 0);
        matrix_stamp_end(0);
        matrix_await();
        matrix_close();
        aster_smp_join();
    } else {
        matrix_open(1);
        aster_smp_dispatch(hart1_stream, 0);
        part[0] = stream(0, 0);
        matrix_stamp_end(0);
        aster_smp_join();
        matrix_close();
    }
#else
    (void)mode;
    part[0] = stream(0, 0);
    part[1] = 0;
#endif
}

static void before_pass(void) {
#if TWO_HARTS
    aster_smp_dispatch(hart1_poison, 0);
    part[0] = 0xA5A5A5A5u;
    aster_smp_join();
#else
    part[0] = 0xA5A5A5A5u;
    part[1] = 0;
#endif
}
static void after_pass(void) { checksum = (part[0] * 33u) ^ part[1]; }
#endif

// one pass in the mode's window (a warm-up's window is opened and closed too, the same code)
static void pass(int mode) {
#if MEM_CASE == 6
    if (TWO_HARTS) { run_mode(mode); return; }
    matrix_open(1);
    run_mode(mode);
    matrix_close();
#else
    (void)mode;                                         // (one window, e2e)
    matrix_open(1);
    run();
    matrix_close();
#endif
}

static int timed_pass(struct v12_record *record, int mode) {
    before_pass();
    matrix_stamps_clear();
    v12_prepare();
    pass(mode);
    v12_end(record);
#if TWO_HARTS
    (void)0;                                            // (hart 0's end stamped: it only waits after its share)
#else
    matrix_hart0_whole(record);
#endif
    after_pass();
    static const char *const names[] = {"", "memcpy", "read_sequential", "read_strided", "walk_random",
                                        "working_set", "stream"};
    static const char *const streams[] = {"stream_one_hart", "stream_same_bank", "stream_diff_banks"};
    record->name = MEM_CASE == 6 ? streams[MEM_PATTERN] : names[MEM_CASE];
    record->family = "memory";
    record->method = MEM_CASE == 1 ? "cpu_copy" : (TWO_HARTS ? "multicore" : "scalar");
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = MEM_CASE == 3 ? STRIDE_WORDS * 4u : (MEM_CASE == 6 ? HALF_BYTES / 4u : MEM_BYTES);
    record->iterations = MEM_CASE == 5 ? WS_READS / WORDS : MEM_REPS;
    record->param = MEM_CASE == 1 ? (MEM_SRC_OFF << 4) | MEM_DST_OFF
                  : (MEM_CASE == 3 ? MEM_STRIDE : (MEM_CASE == 6 ? MEM_STAGGER : 0u));
    record->seed = MEM_SEED; record->checksum = checksum; record->workers = TWO_HARTS ? 2u : 1u;
    record->pass = pass_ok;
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    setup();
#if TWO_HARTS
    aster_smp_start();
#endif
    if (!matrix_cold) pass(E2E);                       // the e2e window's warm-up (the same code)
    int failed = timed_pass(&record, E2E);
#if TWO_HARTS
    if (!matrix_cold) {
        pass(KERNEL);                                  // the kernel window's warm-up
        failed |= timed_pass(&record, KERNEL);
    }
#endif
    return failed;
}
