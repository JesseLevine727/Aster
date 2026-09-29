#ifndef ASTER_WORKLOAD_COH_H
#define ASTER_WORKLOAD_COH_H
#include "aster.h"

// AsterBench v10 generic record and v11 coherent record support.
// v11 keeps the v10 primary-hart CPU counter meanings while adding per-hart
// DOT8 events and cumulative requester-owned engine totals.

#define COH_CONTROL      0x20003080u
#define COH_CPU_ABI      0x20003084u
#define COH_CLOCK_HZ     0x20003088u
#define COH_FLAGS        0x2000308cu
#define COH_LINE_WORDS   0x20003090u
#define COH_LINE_COUNT   0x20003094u
#define COH_MEMORY_WAIT  0x20003098u
#define COH_HART_COUNT   0x20002008u
#define COH_DOT8_BASE    0x20003200u
#define COH_CPU_ABI_VALUE 4u

// AsterBench v11 separates engine-owned totals. These fields are accumulated
// once per completed job; they never use the shared cache's generic device-store
// event as a proxy for DMA or NPU traffic.
struct aster_workload_engine_totals {
    uint32_t dma_jobs, dma_completed_jobs, dma_aborted_jobs, dma_error_jobs;
    uint64_t dma_bytes, dma_job_cycles;
    uint32_t npu_jobs, npu_completed_jobs, npu_aborted_jobs, npu_error_jobs;
    uint64_t npu_bytes_read, npu_bytes_written, npu_tiles;
    uint64_t npu_job_cycles, npu_compute_cycles;
};

static inline uint32_t coh_reg(uint32_t address) {
    return *(volatile uint32_t *)(uintptr_t)address;
}

static inline uint64_t coh_read64(uint32_t address) {
    volatile uint32_t *low = (volatile uint32_t *)(uintptr_t)address;
    volatile uint32_t *high = low + 1;
    uint32_t first, value, last;
    do { first = *high; value = *low; last = *high; } while (first != last);
    return ((uint64_t)last << 32) | value;
}

static inline uint64_t coh_counter(uint32_t event) {
    return coh_read64(0x20003000u + event * 8u);
}

static inline uint64_t coh_counter_hart(uint32_t hart, uint32_t event) {
    return coh_read64(0x20003000u + hart * 256u + event * 8u);
}

static inline uint64_t coh_dot8_counter(uint32_t hart, uint32_t event) {
    return coh_read64(COH_DOT8_BASE + (hart * 4u + event) * 8u);
}

static inline void aster_workload_add_dma_success(struct aster_workload_engine_totals *totals,
                                                  uint32_t bytes, uint64_t job_cycles) {
    ++totals->dma_jobs;
    ++totals->dma_completed_jobs;
    totals->dma_bytes += bytes;
    totals->dma_job_cycles += job_cycles;
}

static inline void aster_workload_add_npu_success(struct aster_workload_engine_totals *totals,
                                                  uint32_t bytes_read,
                                                  uint32_t bytes_written,
                                                  uint64_t job_cycles,
                                                  uint64_t compute_cycles,
                                                  uint32_t tiles) {
    ++totals->npu_jobs;
    ++totals->npu_completed_jobs;
    totals->npu_bytes_read += bytes_read;
    totals->npu_bytes_written += bytes_written;
    totals->npu_job_cycles += job_cycles;
    totals->npu_compute_cycles += compute_cycles;
    totals->npu_tiles += tiles;
}

static inline void coh_field(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_putc('='); aster_put_u32(value);
}
static inline void coh_hex32(const char *key, uint32_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x"); aster_put_hex32(value);
}
static inline void coh_hex64(const char *key, uint64_t value) {
    aster_putc(','); aster_puts(key); aster_puts("=0x");
    aster_put_hex32((uint32_t)(value >> 32)); aster_put_hex32((uint32_t)value);
}
static inline void aster_workload_emit_coh(const char *name, const char *category,
                                           uint32_t size, uint32_t iterations, uint32_t param,
                                           uint32_t seed, uint32_t checksum, uint32_t pass) {
    aster_puts("ASTERBENCH,version=10,name="); aster_puts(name);
    aster_puts(",category="); aster_puts(category);
    aster_puts(",status="); aster_puts(pass ? "PASS" : "FAIL");
    coh_field("size", size);
    coh_field("iterations", iterations);
    coh_field("param", param);
    coh_hex32("seed", seed);
    coh_hex32("checksum", checksum);
    coh_field("clock_hz", coh_reg(COH_CLOCK_HZ));
    coh_field("l1", coh_reg(COH_FLAGS) & 1u);
    coh_field("sync_memory", (coh_reg(COH_FLAGS) >> 1) & 1u);
    coh_field("line_words", coh_reg(COH_LINE_WORDS));
    coh_field("line_count", coh_reg(COH_LINE_COUNT));
    coh_field("memory_wait", coh_reg(COH_MEMORY_WAIT));
    coh_hex64("cycles", coh_counter(0));
    coh_hex64("retired", coh_counter(1));
    coh_hex64("memory_transactions", coh_counter(2));
    coh_hex64("backing_transactions", coh_counter(7));
    coh_hex64("cache_accesses", coh_counter(3) + coh_counter(5));
    coh_hex64("cache_misses", coh_counter(4) + coh_counter(6));
    coh_hex64("dma_bytes", coh_read64(0x30000120u));
    coh_hex64("accelerator_cycles", coh_read64(0x40000048u));
    aster_putc('\n');
}

static inline void aster_workload_emit_coh_v11(
    const char *name, const char *category, uint32_t size, uint32_t iterations,
    uint32_t param, uint32_t workers, uint32_t seed, uint32_t checksum,
    uint32_t pass, const struct aster_workload_engine_totals *engines) {
    aster_puts("ASTERBENCH,version=11,name="); aster_puts(name);
    aster_puts(",category="); aster_puts(category);
    aster_puts(",status="); aster_puts(pass ? "PASS" : "FAIL");
    coh_field("size", size);
    coh_field("iterations", iterations);
    coh_field("param", param);
    coh_hex32("seed", seed);
    coh_hex32("checksum", checksum);
    coh_field("clock_hz", coh_reg(COH_CLOCK_HZ));
    coh_field("harts", coh_reg(COH_HART_COUNT));
    coh_field("workers", workers);
    coh_field("l1", coh_reg(COH_FLAGS) & 1u);
    coh_field("sync_memory", (coh_reg(COH_FLAGS) >> 1) & 1u);
    coh_field("line_words", coh_reg(COH_LINE_WORDS));
    coh_field("line_count", coh_reg(COH_LINE_COUNT));
    coh_field("memory_wait", coh_reg(COH_MEMORY_WAIT));
    coh_hex64("cycles", coh_counter(0));
    coh_hex64("retired", coh_counter(1));
    coh_hex64("memory_transactions", coh_counter(2));
    coh_hex64("backing_transactions", coh_counter(7));
    coh_hex64("cache_accesses", coh_counter(3) + coh_counter(5));
    coh_hex64("cache_misses", coh_counter(4) + coh_counter(6));
    coh_field("dma_jobs", engines->dma_jobs);
    coh_field("dma_completed_jobs", engines->dma_completed_jobs);
    coh_field("dma_aborted_jobs", engines->dma_aborted_jobs);
    coh_field("dma_error_jobs", engines->dma_error_jobs);
    coh_hex64("dma_bytes", engines->dma_bytes);
    coh_hex64("dma_job_cycles", engines->dma_job_cycles);
    for (uint32_t hart = 0; hart < 2; ++hart) {
        const char *prefix = hart == 0 ? "h0_dot8_" : "h1_dot8_";
        for (uint32_t event = 0; event < 4; ++event) {
            const char *suffix = event == 0 ? "accept" : event == 1 ? "wait" :
                                 event == 2 ? "complete" : "retire";
            aster_putc(','); aster_puts(prefix); aster_puts(suffix); aster_puts("=0x");
            const uint64_t count = coh_dot8_counter(hart, event);
            aster_put_hex32((uint32_t)(count >> 32));
            aster_put_hex32((uint32_t)count);
        }
    }
    coh_field("npu_jobs", engines->npu_jobs);
    coh_field("npu_completed_jobs", engines->npu_completed_jobs);
    coh_field("npu_aborted_jobs", engines->npu_aborted_jobs);
    coh_field("npu_error_jobs", engines->npu_error_jobs);
    coh_hex64("npu_bytes_read", engines->npu_bytes_read);
    coh_hex64("npu_bytes_written", engines->npu_bytes_written);
    coh_hex64("npu_tiles", engines->npu_tiles);
    coh_hex64("npu_job_cycles", engines->npu_job_cycles);
    coh_hex64("npu_compute_cycles", engines->npu_compute_cycles);
    aster_putc('\n');
}
#endif
