// The v2 NPU's driver (ABI 2, docs/npu.md §3): a job's descriptor written to
// the register page, START, and its end polled. Header-only. The page is the
// Phase 19 SoC's at 0x4000_0000 (rtl/soc/aster_npu_soc.sv). A job's buffers
// are the caller's; the CPU must not write A, B or C while the job runs, and
// reads C after the end (the SoC snoops the NPU's writes, so no cache
// maintenance is needed). The descriptor's stores and START are bracketed by
// fences, as v1's driver does; the SoC's ordering does not depend on them
// (npu.md §3.1).
#ifndef ASTER_NPU2_H
#define ASTER_NPU2_H

#include <stdint.h>

#define ASTER_NPU2_BASE 0x40000000u
#define ASTER_NPU2_REG(offset) (*(volatile uint32_t *)(uintptr_t)(ASTER_NPU2_BASE + (offset)))

enum {
    ASTER_NPU2_CONTROL = 0x000, ASTER_NPU2_STATUS = 0x004, ASTER_NPU2_ABI = 0x008, ASTER_NPU2_GEOMETRY = 0x00C,
    ASTER_NPU2_A_BASE = 0x010, ASTER_NPU2_B_BASE = 0x014, ASTER_NPU2_C_BASE = 0x018, ASTER_NPU2_A_STRIDE = 0x01C,
    ASTER_NPU2_B_STRIDE = 0x020, ASTER_NPU2_C_STRIDE = 0x024, ASTER_NPU2_M = 0x028, ASTER_NPU2_N = 0x02C,
    ASTER_NPU2_K = 0x030, ASTER_NPU2_MODE = 0x034, ASTER_NPU2_ERROR_CODE = 0x038, ASTER_NPU2_A_M0 = 0x03C,
    ASTER_NPU2_A_STRIDE_M1 = 0x040, ASTER_NPU2_A_K0 = 0x044, ASTER_NPU2_A_STRIDE_K1 = 0x048,
    ASTER_NPU2_JOB_CYCLES = 0x080, ASTER_NPU2_JOB_ACTIVE = 0x088, ASTER_NPU2_JOB_MACS = 0x090,
    ASTER_NPU2_JOB_READ = 0x098, ASTER_NPU2_JOB_WRITTEN = 0x0A0, ASTER_NPU2_JOB_TILES = 0x0A8,
};
enum { ASTER_NPU2_START = 1u, ASTER_NPU2_ABORT = 2u, ASTER_NPU2_ACK = 4u, ASTER_NPU2_CLEAR_TOTALS = 8u };
enum { ASTER_NPU2_BUSY = 1u, ASTER_NPU2_DONE = 2u, ASTER_NPU2_ERROR = 4u, ASTER_NPU2_ABORTED = 8u };

struct aster_npu2_job {
    const void *a;
    const void *b;
    void *c;
    uint32_t a_stride, b_stride, c_stride, m, n, k, mode;
    uint32_t a_m0, a_stride_m1, a_k0, a_stride_k1;     // A's second level (0: off)
};

static inline void aster_npu2_fence(void) { __asm__ volatile ("fence iorw, iorw" ::: "memory"); }

static inline void aster_npu2_start(const struct aster_npu2_job *job) {
    aster_npu2_fence();
    ASTER_NPU2_REG(ASTER_NPU2_A_BASE) = (uint32_t)(uintptr_t)job->a;
    ASTER_NPU2_REG(ASTER_NPU2_B_BASE) = (uint32_t)(uintptr_t)job->b;
    ASTER_NPU2_REG(ASTER_NPU2_C_BASE) = (uint32_t)(uintptr_t)job->c;
    ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE) = job->a_stride;
    ASTER_NPU2_REG(ASTER_NPU2_B_STRIDE) = job->b_stride;
    ASTER_NPU2_REG(ASTER_NPU2_C_STRIDE) = job->c_stride;
    ASTER_NPU2_REG(ASTER_NPU2_M) = job->m;
    ASTER_NPU2_REG(ASTER_NPU2_N) = job->n;
    ASTER_NPU2_REG(ASTER_NPU2_K) = job->k;
    ASTER_NPU2_REG(ASTER_NPU2_MODE) = job->mode;
    ASTER_NPU2_REG(ASTER_NPU2_A_M0) = job->a_m0;
    ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE_M1) = job->a_stride_m1;
    ASTER_NPU2_REG(ASTER_NPU2_A_K0) = job->a_k0;
    ASTER_NPU2_REG(ASTER_NPU2_A_STRIDE_K1) = job->a_stride_k1;
    ASTER_NPU2_REG(ASTER_NPU2_CONTROL) = ASTER_NPU2_START;
}

// Wait for the job's end; returns STATUS (DONE, ERROR or ABORTED set).
static inline uint32_t aster_npu2_wait(void) {
    uint32_t status;
    while (!((status = ASTER_NPU2_REG(ASTER_NPU2_STATUS)) & (ASTER_NPU2_DONE | ASTER_NPU2_ERROR | ASTER_NPU2_ABORTED))) {}
    aster_npu2_fence();
    return status;
}

static inline void aster_npu2_ack(void) { ASTER_NPU2_REG(ASTER_NPU2_CONTROL) = ASTER_NPU2_ACK; }

// A 64-bit counter: its low word first (which latches its high word), then the high word.
static inline uint64_t aster_npu2_counter(uint32_t offset) {
    const uint32_t low = ASTER_NPU2_REG(offset);
    const uint32_t high = ASTER_NPU2_REG(offset + 4u);
    return ((uint64_t)high << 32) | low;
}

#endif
