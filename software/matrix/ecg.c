// 20.4's matrix: streaming ECG (docs/matrix.md §4.8; a v1-retained workload): the frozen MIT-BIH segment (record
// 100, 1,024 samples, software/benchmarks/workload_ecg_data.h) in chunks of ECG_CHUNK samples, an ECG_COEF-tap
// FIR, features, and a 3-class linear classifier of ECG_FEATURES features: 4 (v1's) or 8 (v1's four over each half
// of the filtered chunk). ECG_METHOD:
//   0 v1's heterogeneous pipeline (software/benchmarks/workload_ecg.c), ported: hart 0 stages each chunk, the DMA
//     moves it, hart 1 runs the DOT8 FIR (v1's epoch handshake; hart 1 released just before START when cold, as v1
//     did, before its warm-up when warm), hart 0 extracts the features and the NPU classifies them
//   1 scalar on one hart: the CPU stages and copies, a scalar FIR and classifier
//   2 DOT8 on one hart: the CPU stages and copies, v1's xe_dot8_gemm for the FIR and the classifier
//   3 a two-hart pipeline: chunk i's FIR on hart 1 (the runtime's dispatch) while hart 0 classifies chunk i - 1;
//     the DMA and the NPU as in 0, the buffers doubled
// Two windows (matrix.md §4):
//   e2e     v1's: the stream, chunk by chunk, staging and movement included. Each chunk's latency, from its staging
//           to its class, is read from hart 0's cycle CSR (an instruction at each end) and printed apart, on a
//           MATRIX_ECG line after the record, for the deadline (the chunk's period at 360 Hz).
//   kernel  the stream already in memory: the FIR, features and classifier of every chunk, one interval.
// A cold run (matrix_cold.h) is the e2e window as the first pass after reset; a warm run is, for each window, an
// untimed pass of the same code, then the window. Each timed pass's outputs (every chunk's class and scores, and
// the checksum's inputs) are recomputed from scratch; the per-chunk results are poisoned before it. The checksum
// folds each chunk's first and last filtered value, its features, scores and class (v1's), which scripts/matrix.py
// recomputes with an independent model (workload_reference.ecg_checksum's for v1's model).
#include <stdint.h>
#include <stdatomic.h>

#include "matrix_layout.h"
#include "aster.h"
#include "aster_dma.h"
#include "aster_npu2.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"
#include "workload_ecg_data.h"
#include "xe_kernels.h"

#ifndef ECG_METHOD
#define ECG_METHOD 0
#endif
#ifndef ECG_CHUNK
#define ECG_CHUNK 64u
#endif
#ifndef ECG_COEF
#define ECG_COEF 16u
#endif
#ifndef ECG_FEATURES
#define ECG_FEATURES 4u
#endif
#define ECG_SEED 0x13570000u
#define ECG_CHUNKS (ECG_DATA_COUNT / ECG_CHUNK)
#define ECG_FOUT (ECG_CHUNK - ECG_COEF + 1u)
#define ECG_CLASSES 3u
#define PIPELINE (ECG_METHOD == 0 || ECG_METHOD == 3)
_Static_assert(ECG_CHUNK > ECG_COEF && ECG_DATA_COUNT % ECG_CHUNK == 0, "the chunks and the filter");
_Static_assert(ECG_FEATURES == 4u || ECG_FEATURES == 8u, "v1's model or twice its features");

enum { UNTIMED, E2E, KERNEL };

MATRIX_ROOM(raw, sizeof(int8_t[ECG_CHUNK]), 8);                 // the staging buffer
#define raw MATRIX_AT(int8_t, raw, ECG_CHUNK)
MATRIX_ROOM(moved, sizeof(int8_t[2][ECG_CHUNK]), 8);            // the DMA's (or the CPU copy's) target
#define moved MATRIX_AT(int8_t, moved, 2][ECG_CHUNK)
MATRIX_ROOM(stream, sizeof(int8_t[ECG_DATA_COUNT]), 8);         // the kernel window's stream in place
#define stream MATRIX_AT(int8_t, stream, ECG_DATA_COUNT)
MATRIX_ROOM(coef, sizeof(int8_t[ECG_COEF]), 4);
#define coef MATRIX_AT(int8_t, coef, ECG_COEF)
MATRIX_ROOM(filtered, sizeof(int32_t[2][ECG_FOUT]), 4);
#define filtered MATRIX_AT(int32_t, filtered, 2][ECG_FOUT)
MATRIX_ROOM(feat_q, sizeof(int8_t[ECG_FEATURES]), 4);
#define feat_q MATRIX_AT(int8_t, feat_q, ECG_FEATURES)
MATRIX_ROOM(weights, sizeof(int8_t[ECG_CLASSES * ECG_FEATURES]), 4);
#define weights MATRIX_AT(int8_t, weights, ECG_CLASSES * ECG_FEATURES)
MATRIX_ROOM(scores, sizeof(int32_t[ECG_CLASSES]), 4);
#define scores MATRIX_AT(int32_t, scores, ECG_CLASSES)
static uint32_t chunk_start[ECG_CHUNKS], chunk_latency[ECG_CHUNKS];
static int32_t chunk_class[ECG_CHUNKS];
static volatile uint32_t engine_failed;

static inline uint32_t now(void) { uint32_t t; __asm__ volatile ("rdcycle %0" : "=r"(t)); return t; }

// 20.5 (tuning.md §5): ECG_STAGES 1 builds a separate entry that stamps each chunk's stages and engine jobs in the
// window's time base (the stamps cost cycles in v1's window, so the gate's figures are the unstamped entries'):
// hart 0's staging and move (bring) and its features and classifier (classify), hart 1's FIR, and each DMA and
// NPU job from its submission for its own JOB_CYCLES (not the polling span). Printed after the record, one
// MATRIX_STAGE line a chunk.
#ifndef ECG_STAGES
#define ECG_STAGES 0
#endif
#if ECG_STAGES
enum { ST_BRING, ST_FIR, ST_CLASSIFY, ST_DMA, ST_NPU, ST_KINDS };
static volatile uint32_t stage_at[ECG_CHUNKS][ST_KINDS][2];
static volatile uint32_t fir_chunk;                    // (the chunk hart 1 filters)
#define STAGE_BEGIN(c, k) (stage_at[c][k][0] = aster_window_now())
#define STAGE_END(c, k) (stage_at[c][k][1] = aster_window_now())
#define STAGE_JOB(c, k, start, cycles) (stage_at[c][k][0] = (start), stage_at[c][k][1] = (start) + (uint32_t)(cycles))
#else
#define STAGE_BEGIN(c, k) ((void)0)
#define STAGE_END(c, k) ((void)0)
#define STAGE_JOB(c, k, start, cycles) ((void)0)
#endif

static int32_t trunc_div(int32_t value, int32_t divisor) {
    const int32_t quotient = (value < 0 ? -value : value) / divisor;
    return value < 0 ? -quotient : quotient;
}

static int8_t clamp8(int32_t value) {
    if (value < -128) return -128;
    if (value > 127) return 127;
    return (int8_t)value;
}

// (the computation's functions out of line, noinline, so that their code is the same in every caller and build)
// the FIR, C(r) = sum_k x(r + k) coef(k): on the method's engine
static __attribute__((noinline)) void fir(const int8_t *x, int32_t *out) {
#if ECG_METHOD == 1
    for (uint32_t r = 0; r < ECG_FOUT; ++r) {
        int32_t sum = 0;
        for (uint32_t k = 0; k < ECG_COEF; ++k) sum += (int32_t)x[r + k] * (int32_t)coef[k];
        out[r] = sum;
    }
#else
    const struct aster_npu_gemm job = {x, coef, out, 1u, 1u, 4u, ECG_FOUT, 1u, ECG_COEF};
    xe_dot8_gemm(&job);
#endif
}

// v1's four features over filtered[begin, end), into feat_q[at .. at + 3]
static __attribute__((noinline)) void features_of(const int32_t *f, uint32_t begin, uint32_t end, uint32_t at) {
    int32_t peak = 0, sum_scaled = 0, abs_sum = 0, previous_sign = 0;
    uint32_t zero_crossings = 0;
    for (uint32_t r = begin; r < end; ++r) {
        const int32_t value = f[r];
        const int32_t scaled = value >> 8;
        sum_scaled += scaled;
        const int32_t magnitude = scaled < 0 ? -scaled : scaled;
        abs_sum += magnitude;
        if (magnitude > peak) peak = magnitude;
        const int32_t sign = value > 0 ? 1 : (value < 0 ? -1 : 0);
        if (previous_sign != 0 && sign != 0 && sign != previous_sign) ++zero_crossings;
        if (sign != 0) previous_sign = sign;
    }
    const int32_t n = (int32_t)(end - begin);
    feat_q[at + 0] = clamp8(peak >> 4);
    feat_q[at + 1] = clamp8(trunc_div(abs_sum, n) >> 4);
    feat_q[at + 2] = clamp8((int32_t)zero_crossings);
    feat_q[at + 3] = clamp8(trunc_div(sum_scaled, n) >> 4);
}

// a chunk's features, classifier and class, its results folded into the checksum
static __attribute__((noinline)) uint32_t classify(uint32_t chunk, const int32_t *f, uint32_t checksum) {
    STAGE_BEGIN(chunk, ST_CLASSIFY);
#if ECG_FEATURES == 4
    features_of(f, 0, ECG_FOUT, 0);
#else
    features_of(f, 0, ECG_FOUT / 2u, 0);
    features_of(f, ECG_FOUT / 2u, ECG_FOUT, 4);
#endif
#if PIPELINE
    const struct aster_npu2_job job = {weights, feat_q, scores, ECG_FEATURES, 1u, 4u, ECG_CLASSES, 1u, ECG_FEATURES,
                                       0, 0, 0, 0, 0};
#if ECG_STAGES
    const uint32_t submitted = aster_window_now();
#endif
    aster_npu2_start(&job);
    const uint32_t status = aster_npu2_wait();
    STAGE_JOB(chunk, ST_NPU, submitted, aster_counter_read64(0x40000080u));     // (its JOB_CYCLES)
    v12_note_npu_job(status);
    aster_npu2_ack();
    if ((status & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED)) != ASTER_NPU2_DONE) engine_failed = 1;
#elif ECG_METHOD == 2
    const struct aster_npu_gemm job = {weights, feat_q, scores, ECG_FEATURES, 1u, 4u, ECG_CLASSES, 1u, ECG_FEATURES};
    xe_dot8_gemm(&job);
#else
    for (uint32_t c = 0; c < ECG_CLASSES; ++c) {
        int32_t sum = 0;
        for (uint32_t i = 0; i < ECG_FEATURES; ++i) sum += (int32_t)weights[c * ECG_FEATURES + i] * (int32_t)feat_q[i];
        scores[c] = sum;
    }
#endif
    int best = 0;
    for (uint32_t c = 1; c < ECG_CLASSES; ++c) if (scores[c] > scores[best]) best = (int)c;
    chunk_class[chunk] = best;
    checksum = (checksum * 33u) ^ (uint32_t)f[0];
    checksum = (checksum * 33u) ^ (uint32_t)f[ECG_FOUT - 1u];
    for (uint32_t i = 0; i < ECG_FEATURES; ++i) checksum = (checksum * 33u) ^ (uint32_t)(uint8_t)feat_q[i];
    for (uint32_t c = 0; c < ECG_CLASSES; ++c) checksum = (checksum * 33u) ^ (uint32_t)scores[c];
    STAGE_END(chunk, ST_CLASSIFY);
    return (checksum * 33u) ^ (uint32_t)best;
}

// a chunk staged and moved into moved[slot] (e2e), its samples' address returned; the kernel window's are in place
static __attribute__((noinline)) const int8_t *bring(uint32_t chunk, uint32_t slot, int mode) {
    if (mode == KERNEL) return &stream[chunk * ECG_CHUNK];
    STAGE_BEGIN(chunk, ST_BRING);
    for (uint32_t i = 0; i < ECG_CHUNK; ++i) raw[i] = ecg_samples[chunk * ECG_CHUNK + i];      // the staging
    __asm__ volatile ("fence rw,rw" ::: "memory");
#if PIPELINE
#if ECG_STAGES
    const uint32_t submitted = aster_window_now();
#endif
    if (aster_dma_copy(moved[slot], raw, ECG_CHUNK, 8000000u) != ASTER_DMA_OK) engine_failed = 1;
    STAGE_JOB(chunk, ST_DMA, submitted, aster_dma_job_cycles());
    v12_note_dma_job();
#else
    for (uint32_t i = 0; i < ECG_CHUNK; ++i) moved[slot][i] = raw[i];                          // the CPU's copy
#endif
    __asm__ volatile ("fence rw,rw" ::: "memory");
    STAGE_END(chunk, ST_BRING);
    return moved[slot];
}

#if ECG_METHOD == 0
// v1's: hart 1 filters the chunk in hand for each new epoch
static _Atomic uint32_t ecg_epoch, ecg_done;
static const int8_t *volatile fir_in;
static uint32_t next_epoch;

void aster_secondary_main(void) {
    uint32_t last = 0;
    for (;;) {
        const uint32_t e = atomic_load_explicit(&ecg_epoch, memory_order_acquire);
        if (e != 0u && e != last) {
            last = e;
            matrix_stamp_start(1);
            STAGE_BEGIN(fir_chunk, ST_FIR);
            fir(fir_in, filtered[0]);
            STAGE_END(fir_chunk, ST_FIR);
            matrix_stamp_end(1);
            atomic_store_explicit(&ecg_done, e, memory_order_release);
        }
    }
}

static uint32_t run(int mode) {
    uint32_t checksum = 0;
    for (uint32_t chunk = 0; chunk < ECG_CHUNKS; ++chunk) {
        chunk_start[chunk] = now();
        fir_in = bring(chunk, 0, mode);
#if ECG_STAGES
        fir_chunk = chunk;
#endif
        matrix_last = chunk == ECG_CHUNKS - 1u;
        const uint32_t e = ++next_epoch;
        atomic_store_explicit(&ecg_done, 0u, memory_order_relaxed);
        atomic_store_explicit(&ecg_epoch, e, memory_order_release);
        __asm__ volatile ("fence rw,rw" ::: "memory");
        while (atomic_load_explicit(&ecg_done, memory_order_acquire) != e) { }
        __asm__ volatile ("fence rw,rw" ::: "memory");
        checksum = classify(chunk, filtered[0], checksum);
        chunk_latency[chunk] = now() - chunk_start[chunk];
    }
    return checksum;
}
#elif ECG_METHOD == 3
// the two-hart pipeline: chunk i filtered on hart 1 into filtered[i % 2] while hart 0 classifies chunk i - 1
static const int8_t *volatile fir_in;
static int32_t *volatile fir_out;

static void fir_job(void *arg) {
    (void)arg;
    matrix_stamp_start(1);
    STAGE_BEGIN(fir_chunk, ST_FIR);
    fir(fir_in, fir_out);
    STAGE_END(fir_chunk, ST_FIR);
    matrix_stamp_end(1);
}

static uint32_t run(int mode) {
    uint32_t checksum = 0;
    for (uint32_t chunk = 0; chunk <= ECG_CHUNKS; ++chunk) {
        if (chunk < ECG_CHUNKS) {
            chunk_start[chunk] = now();
            fir_in = bring(chunk, chunk & 1u, mode);
            fir_out = filtered[chunk & 1u];
#if ECG_STAGES
            fir_chunk = chunk;
#endif
            matrix_last = chunk == ECG_CHUNKS - 1u;
            aster_smp_dispatch(fir_job, 0);
        }
        if (chunk > 0) {
            checksum = classify(chunk - 1u, filtered[(chunk - 1u) & 1u], checksum);
            chunk_latency[chunk - 1u] = now() - chunk_start[chunk - 1u];
        }
        if (chunk < ECG_CHUNKS) aster_smp_join();
    }
    return checksum;
}
#else
static uint32_t run(int mode) {
    uint32_t checksum = 0;
    for (uint32_t chunk = 0; chunk < ECG_CHUNKS; ++chunk) {
        chunk_start[chunk] = now();
        fir(bring(chunk, 0, mode), filtered[0]);
        checksum = classify(chunk, filtered[0], checksum);
        chunk_latency[chunk] = now() - chunk_start[chunk];
    }
    return checksum;
}
#endif

static uint32_t pass(int mode) {
    if (mode != UNTIMED) matrix_open(1);
    const uint32_t checksum = run(mode);
    if (mode != UNTIMED) matrix_close();
    return checksum;
}

static int timed_pass(struct v12_record *record, int mode) {
    matrix_poison(chunk_class, sizeof chunk_class);
    matrix_poison(chunk_latency, sizeof chunk_latency);
    matrix_stamps_clear();
#if ECG_STAGES
    for (uint32_t c = 0; c < ECG_CHUNKS; ++c)
        for (uint32_t k = 0; k < ST_KINDS; ++k) stage_at[c][k][0] = stage_at[c][k][1] = 0;
#endif
    v12_prepare();
#if ECG_METHOD == 0
    if (matrix_cold) *(volatile uint32_t *)0x20002004u = 1u;   // v1's: hart 1 released just before START
#endif
    const uint32_t checksum = pass(mode);
    v12_end(record);
    matrix_hart0_whole(record);                        // (each chunk ends in hart 0's classifier and checksum)
    static const char *const names[2][4] = {{"ecg_pipeline_v1", "ecg_scalar", "ecg_dot8", "ecg_pipeline_overlap"},
                                            {"ecg_pipeline_v1_f8", "ecg_scalar_f8", "ecg_dot8_f8",
                                             "ecg_pipeline_overlap_f8"}};
    static const char *const methods[] = {"pipeline", "scalar", "dot8", "pipeline"};
    record->name = names[ECG_FEATURES == 8u][ECG_METHOD]; record->family = "ecg"; record->method = methods[ECG_METHOD];
#if ECG_STAGES
    static char stamped[48];                           // (the stamped entry's case: the name and "__stages")
    if (!stamped[0]) {
        int n = 0;
        for (const char *c = record->name; *c; ++c) stamped[n++] = *c;
        for (const char *c = "__stages"; *c; ++c) stamped[n++] = *c;
    }
    record->name = stamped;
#endif
    record->window = mode == KERNEL ? "kernel" : "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = ECG_CHUNK; record->iterations = ECG_CHUNKS; record->param = ECG_COEF; record->seed = ECG_SEED;
    record->checksum = checksum; record->workers = PIPELINE ? 2u : 1u; record->pass = !engine_failed;
    v12_emit(record);
#if ECG_STAGES
    static const char *const kinds[ST_KINDS] = {",bring=", ",fir=", ",classify=", ",dma=", ",npu="};
    for (uint32_t c = 0; c < ECG_CHUNKS; ++c) {
        aster_puts("MATRIX_STAGE,window="); aster_puts(record->window); aster_puts(",chunk="); aster_put_u32(c);
        for (uint32_t k = 0; k < ST_KINDS; ++k) {
            aster_puts(kinds[k]); aster_put_u32(stage_at[c][k][0]); aster_putc('-'); aster_put_u32(stage_at[c][k][1]);
        }
        aster_puts("\n");
    }
#endif
    if (mode == E2E) {                                 // each chunk's latency, apart from the record
        uint32_t lo = 0xFFFFFFFFu, hi = 0;
        uint64_t sum = 0;
        for (uint32_t c = 0; c < ECG_CHUNKS; ++c) {
            const uint32_t l = chunk_latency[c];
            if (l < lo) lo = l;
            if (l > hi) hi = l;
            sum += l;
        }
        aster_puts("MATRIX_ECG,name="); aster_puts(record->name); aster_puts(",chunks="); aster_put_u32(ECG_CHUNKS);
        aster_puts(",latency_min="); aster_put_u32(lo); aster_puts(",latency_max="); aster_put_u32(hi);
        aster_puts(",latency_mean="); aster_put_u32((uint32_t)(sum / ECG_CHUNKS)); aster_puts("\n");
    }
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    for (uint32_t i = 0; i < ECG_COEF; ++i) coef[i] = (int8_t)((ECG_SEED ^ (i * 0x9e3779b9u)) & 0xffu);
    for (uint32_t c = 0; c < ECG_CLASSES; ++c)
        for (uint32_t f = 0; f < ECG_FEATURES; ++f)
            weights[c * ECG_FEATURES + f] = (int8_t)((ECG_SEED ^ (c * 0x85ebca6bu) ^ (f * 0x1021u)) & 0xffu);
    for (uint32_t i = 0; i < ECG_DATA_COUNT; ++i) stream[i] = ecg_samples[i];
#if ECG_METHOD == 3
    aster_smp_start();
#endif
    if (!matrix_cold) {
#if ECG_METHOD == 0
        *(volatile uint32_t *)0x20002004u = 1u;        // (warm: hart 1 is running before the timed pass)
#endif
        (void)pass(E2E);                               // the e2e window's warm-up (the same code)
    }
    engine_failed = 0;
    int failed = timed_pass(&record, E2E);
    if (!matrix_cold) {
        (void)pass(KERNEL);                            // the kernel window's warm-up
        failed |= timed_pass(&record, KERNEL);
    }
    return failed;
}
