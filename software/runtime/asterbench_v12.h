// AsterBench v12 (docs/asterbench-v12.md): one measurement window on the Phase 20 SoC and
// its record. A workload:
//   v12_begin()                      clears the software totals and the NPU's, then START
//                                    (v12_prepare() alone, for a caller that writes START itself)
//   ... its work: each hart stamps v12_work_start() / v12_work_end() around its useful work;
//       each DMA job started is noted (v12_note_dma_job), each NPU job's end (v12_note_npu_job)
//   v12_end(&record)                 FREEZE, then every counter into the record
//   fill in the identity (name, family, method, window, cache state, sizes, seed, checksum,
//   workers, status), then v12_emit(&record).
// The configuration a hart cannot read (the data cache's mode, the NPU's port width and strips)
// comes from the build: V12_DCACHE, V12_NPU_PORT_BYTES, V12_NPU_STRIPS, set by the matrix's
// runner and checked by it against the testbench's readback (the owner's decision, matrix.md §9).
#ifndef ASTERBENCH_V12_H
#define ASTERBENCH_V12_H

#include <stdint.h>

#include "aster_counters.h"

#ifndef V12_DCACHE
#define V12_DCACHE 1
#endif
#ifndef V12_NPU_PORT_BYTES
#define V12_NPU_PORT_BYTES 8
#endif
#ifndef V12_NPU_STRIPS
#define V12_NPU_STRIPS 2
#endif

struct v12_hart {
    uint64_t counter[ASTER_C_COUNT];   // ABI 4's fourteen, in its order
    uint64_t dot8[4];                   // accept, wait, complete, retire
    uint32_t work_start, work_end;      // cycles from the window's start (0, 0: no work)
};

struct v12_record {
    // identity (the workload's)
    const char *name, *family, *method, *window, *cache_state;
    int pass;
    uint32_t size, iterations, param, seed, checksum, workers;
    // configuration
    uint32_t clock_hz, harts, dcache, line_words, line_count, memory_wait, npu_dim, npu_port_bytes, npu_strips;
    struct v12_hart hart[2];
    // the DMA: software's job count, then ABI 5's
    uint32_t dma_jobs;
    uint64_t dma_completed, dma_aborted, dma_errors, dma_rejected, dma_bytes, dma_busy, dma_wait, dma_reads,
             dma_writes, dma_backing_reads, dma_backing_writes, dma_invalidations;
    // the NPU: its totals, and software's outcomes and tiles
    uint64_t npu_jobs, npu_completed, npu_aborted, npu_errors, npu_job_cycles, npu_active, npu_macs,
             npu_bytes_read, npu_bytes_written, npu_tiles;
    uint64_t fabric[ASTER_FABRIC_COUNT];
    int engine_busy_at_freeze;          // a DMA or NPU job crossed the window's end: the record says FAIL
};

void v12_prepare(void);
void v12_begin(void);
void v12_end(struct v12_record *record);
void v12_emit(const struct v12_record *record);

// each hart's work interval in the open window
void v12_work_start(uint32_t hart);
void v12_work_end(uint32_t hart);
// software's engine accounting in the open window
void v12_note_dma_job(void);
void v12_note_npu_job(uint32_t status);   // the job's STATUS at its end (aster_npu2_wait's result)

#endif
