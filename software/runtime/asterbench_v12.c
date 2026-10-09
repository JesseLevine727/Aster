// AsterBench v12's window and record (asterbench_v12.h, docs/asterbench-v12.md).
#include "asterbench_v12.h"

#include "aster.h"

// The software totals and each hart's stamps for the open window (shared: hart 1 stamps its own)
static volatile uint32_t work_start[2], work_end[2];
static volatile uint32_t dma_jobs, npu_completed, npu_aborted, npu_errors, npu_tiles;
// The engines' last jobs' own counters, read after FREEZE (nothing in the window): the NPU's JOB_CYCLES, JOB_ACTIVE,
// JOB_MACS, JOB_BYTES_READ and JOB_BYTES_WRITTEN, then the DMA's JOB_CYCLES and BYTES_DONE. v12_emit prints them on
// a MATRIX_JOBS line after the record; scripts/matrix.py reconciles a one-job window's totals with them.
static uint64_t last_job[7];

enum { NPU2_CONTROL = 0x40000000u, NPU2_STATUS = 0x40000004u };
enum { NPU2_BUSY = 1u, NPU2_DONE = 2u, NPU2_ERROR = 4u, NPU2_ABORTED = 8u, NPU2_CLEAR_TOTALS = 8u };

void v12_prepare(void) {
    for (int h = 0; h < 2; ++h) { work_start[h] = 0; work_end[h] = 0; }
    dma_jobs = 0; npu_completed = 0; npu_aborted = 0; npu_errors = 0; npu_tiles = 0;
    while (ASTER_REG32(NPU2_STATUS) & NPU2_BUSY) {}       // (no job may cross the window's start)
    ASTER_REG32(NPU2_CONTROL) = NPU2_CLEAR_TOTALS;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}

void v12_begin(void) {
    v12_prepare();
    ASTER_REG32(ASTER_ABI4_COMMAND) = ASTER_ABI4_START;
}

void v12_work_start(uint32_t hart) { work_start[hart & 1u] = aster_window_now(); }
void v12_work_end(uint32_t hart) { work_end[hart & 1u] = aster_window_now(); }
void v12_note_dma_job(void) { ++dma_jobs; }

void v12_note_npu_job(uint32_t status) {
    if (status & NPU2_ERROR) ++npu_errors;
    else if (status & NPU2_ABORTED) ++npu_aborted;
    else ++npu_completed;
    npu_tiles += ASTER_REG32(ASTER_NPU2_JOB_TILES_REG);
}

enum { DMA_STATUS = 0x30000010u, DMA_BUSY = 1u };

void v12_end(struct v12_record *r) {
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    ASTER_REG32(ASTER_ABI4_COMMAND) = ASTER_ABI4_FREEZE;        // (again, harmlessly, if the caller froze)
    // no job may cross the window's end either: a record whose engines still ran says FAIL
    r->engine_busy_at_freeze = (ASTER_REG32(NPU2_STATUS) & NPU2_BUSY) || (ASTER_REG32(DMA_STATUS) & DMA_BUSY);
    r->clock_hz = ASTER_REG32(ASTER_ABI4_CLOCK_HZ);
    r->harts = ASTER_REG32(ASTER_HART_COUNT);
    r->line_words = ASTER_REG32(ASTER_ABI4_LINE_WORDS);
    r->line_count = ASTER_REG32(ASTER_ABI4_LINE_COUNT);
    r->memory_wait = ASTER_REG32(ASTER_ABI4_MEMORY_WAIT);
    r->npu_dim = ASTER_REG32(ASTER_NPU2_GEOMETRY_REG) & 0xFFu;
    r->dcache = V12_DCACHE;
    r->npu_port_bytes = V12_NPU_PORT_BYTES;
    r->npu_strips = V12_NPU_STRIPS;
    // (stamps are relative to the window's start: START cleared the window's cycles)
    for (uint32_t h = 0; h < 2; ++h) {
        for (uint32_t i = 0; i < ASTER_C_COUNT; ++i)
            r->hart[h].counter[i] = aster_counter_read64(ASTER_ABI4_BASE(h) + 8u * i);
        for (uint32_t e = 0; e < 4; ++e) r->hart[h].dot8[e] = aster_counter_read64(ASTER_DOT8(h, e));
        r->hart[h].work_start = work_start[h];
        r->hart[h].work_end = work_end[h];
    }
    r->dma_jobs = dma_jobs;
    r->dma_busy = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_BUSY));
    r->dma_wait = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_WAIT));
    r->dma_reads = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_READS));
    r->dma_writes = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_WRITES));
    r->dma_bytes = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_BYTES));
    r->dma_backing_reads = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_BACKING_READS));
    r->dma_backing_writes = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_BACKING_WRITES));
    r->dma_invalidations = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_INVALIDATIONS));
    r->dma_completed = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_COMPLETED));
    r->dma_aborted = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_ABORTED));
    r->dma_errors = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_ERRORS));
    r->dma_rejected = aster_counter_read64(ASTER_DMA_CTR(ASTER_DMA_C_REJECTED));
    r->npu_jobs = ASTER_REG32(ASTER_NPU2_TOTAL_JOBS);
    r->npu_job_cycles = aster_counter_read64(ASTER_NPU2_TOTAL(ASTER_NPU2_T_CYCLES));
    r->npu_active = aster_counter_read64(ASTER_NPU2_TOTAL(ASTER_NPU2_T_ACTIVE));
    r->npu_macs = aster_counter_read64(ASTER_NPU2_TOTAL(ASTER_NPU2_T_MACS));
    r->npu_bytes_read = aster_counter_read64(ASTER_NPU2_TOTAL(ASTER_NPU2_T_BYTES_READ));
    r->npu_bytes_written = aster_counter_read64(ASTER_NPU2_TOTAL(ASTER_NPU2_T_BYTES_WRITTEN));
    r->npu_completed = npu_completed;
    r->npu_aborted = npu_aborted;
    r->npu_errors = npu_errors;
    r->npu_tiles = npu_tiles;
    for (uint32_t i = 0; i < 5; ++i) last_job[i] = aster_counter_read64(0x40000080u + 8u * i);
    last_job[5] = aster_counter_read64(0x30000020u);
    last_job[6] = ASTER_REG32(0x30000014u);
    for (uint32_t k = 0; k < ASTER_FABRIC_COUNT; ++k) r->fabric[k] = aster_counter_read64(ASTER_FABRIC(k));
}

static void put_u64(uint64_t value) {
    char digits[21];
    int n = 0;
    do {
        // (32-bit division once the value fits, which most do)
        if (value <= 0xFFFFFFFFu) {
            uint32_t v = (uint32_t)value;
            do { digits[n++] = (char)('0' + v % 10u); v /= 10u; } while (v);
            break;
        }
        digits[n++] = (char)('0' + (uint32_t)(value % 10u));
        value /= 10u;
    } while (value);
    while (n) aster_putc(digits[--n]);
}

static void field(const char *key, uint64_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); put_u64(value);
}

static void text(const char *key, const char *value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_puts(value);
}

static void hex32(const char *key, uint32_t value) {
    static const char digits[] = "0123456789abcdef";
    aster_putc(','); aster_puts(key); aster_puts("=0x");
    for (int shift = 28; shift >= 0; shift -= 4) aster_putc(digits[(value >> shift) & 0xFu]);
}

void v12_emit(const struct v12_record *r) {
    static const char *const hart_names[ASTER_C_COUNT] = {
        "cycles", "retired", "memory_transactions", "icache_accesses", "icache_misses", "dcache_accesses",
        "dcache_misses", "backing_transactions", "amos", "sc_success", "sc_failure", "dirty_interventions",
        "invalidations", "writeback_words"};
    static const char *const dot8_names[4] = {"dot8_accept", "dot8_wait", "dot8_complete", "dot8_retire"};
    static const char *const req[7] = {"i0", "d0", "i1", "d1", "n", "r", "w"};
    char key[32];

    aster_puts("ASTERBENCH");
    field("version", 12);
    text("name", r->name); text("family", r->family); text("method", r->method); text("window", r->window);
    text("status", r->pass && !r->engine_busy_at_freeze ? "PASS" : "FAIL");
    field("size", r->size); field("iterations", r->iterations); field("param", r->param);
    hex32("seed", r->seed); hex32("checksum", r->checksum);
    field("clock_hz", r->clock_hz); field("harts", r->harts); field("workers", r->workers);
    field("dcache", r->dcache); text("cache_state", r->cache_state);
    field("line_words", r->line_words); field("line_count", r->line_count); field("memory_wait", r->memory_wait);
    field("npu_dim", r->npu_dim); field("npu_port_bytes", r->npu_port_bytes); field("npu_strips", r->npu_strips);
    for (int h = 0; h < 2; ++h) {
        for (int i = 0; i < ASTER_C_COUNT + 6; ++i) {
            const char *name = i < ASTER_C_COUNT ? hart_names[i] : i < ASTER_C_COUNT + 4 ? dot8_names[i - ASTER_C_COUNT]
                             : i == ASTER_C_COUNT + 4 ? "work_start" : "work_end";
            int n = 0;
            key[n++] = 'h'; key[n++] = (char)('0' + h); key[n++] = '_';
            while (*name) key[n++] = *name++;
            key[n] = 0;
            const uint64_t value = i < ASTER_C_COUNT ? r->hart[h].counter[i]
                                 : i < ASTER_C_COUNT + 4 ? r->hart[h].dot8[i - ASTER_C_COUNT]
                                 : i == ASTER_C_COUNT + 4 ? r->hart[h].work_start : r->hart[h].work_end;
            field(key, value);
        }
    }
    field("dma_jobs", r->dma_jobs); field("dma_completed_jobs", r->dma_completed);
    field("dma_aborted_jobs", r->dma_aborted); field("dma_error_jobs", r->dma_errors);
    field("dma_rejected", r->dma_rejected); field("dma_bytes", r->dma_bytes); field("dma_busy_cycles", r->dma_busy);
    field("dma_wait_cycles", r->dma_wait); field("dma_reads", r->dma_reads); field("dma_writes", r->dma_writes);
    field("dma_backing_reads", r->dma_backing_reads); field("dma_backing_writes", r->dma_backing_writes);
    field("dma_invalidations", r->dma_invalidations);
    field("npu_jobs", r->npu_jobs); field("npu_completed_jobs", r->npu_completed);
    field("npu_aborted_jobs", r->npu_aborted); field("npu_error_jobs", r->npu_errors);
    field("npu_job_cycles", r->npu_job_cycles); field("npu_active_cycles", r->npu_active);
    field("npu_macs", r->npu_macs); field("npu_bytes_read", r->npu_bytes_read);
    field("npu_bytes_written", r->npu_bytes_written); field("npu_tiles", r->npu_tiles);
    // the fabric, in its order
    static const char *const groups[] = {"f_accepted_", "f_waited_"};
    uint32_t k = 0;
    for (int g = 0; g < 2; ++g)
        for (int q = 0; q < 7; ++q, ++k) {
            int n = 0; const char *a = groups[g]; while (*a) key[n++] = *a++;
            const char *b = req[q]; while (*b) key[n++] = *b++; key[n] = 0;
            field(key, r->fabric[k]);
        }
    static const char *const bank_kinds[] = {"_reads", "_writes", "_conflicts"};
    for (int kind = 0; kind < 3; ++kind)
        for (int b = 0; b < 4; ++b, ++k) {
            int n = 0; const char *a = "f_bank"; while (*a) key[n++] = *a++;
            key[n++] = (char)('0' + b);
            const char *s = bank_kinds[kind]; while (*s) key[n++] = *s++; key[n] = 0;
            field(key, r->fabric[k]);
        }
    static const char *const snoop_kinds[] = {"f_snoops_c", "f_invalidations_c"};
    for (int kind = 0; kind < 2; ++kind)
        for (int c = 0; c < 2; ++c)
            for (int p = 0; p < 3; ++p, ++k) {
                int n = 0; const char *a = snoop_kinds[kind]; while (*a) key[n++] = *a++;
                key[n++] = (char)('0' + c); key[n++] = 'p'; key[n++] = (char)('0' + p); key[n] = 0;
                field(key, r->fabric[k]);
            }
    field("f_resv_ended_h0", r->fabric[k++]); field("f_resv_ended_h1", r->fabric[k++]); field("f_amos", r->fabric[k++]);
    for (int q = 0; q < 7; ++q, ++k) {
        int n = 0; const char *a = "f_longest_"; while (*a) key[n++] = *a++;
        const char *b = req[q]; while (*b) key[n++] = *b++; key[n] = 0;
        field(key, r->fabric[k]);
    }
    aster_putc('\n');
    // the engines' last jobs (beside the record, not in it: v12's fields are fixed)
    static const char *const job_names[7] = {"npu_job_cycles", "npu_active_cycles", "npu_macs", "npu_bytes_read",
                                             "npu_bytes_written", "dma_job_cycles", "dma_bytes_done"};
    aster_puts("MATRIX_JOBS");
    for (int i = 0; i < 7; ++i) field(job_names[i], last_job[i]);
    aster_putc('\n');
}
