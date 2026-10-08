// The Phase 20 SoC's counter pages (docs/soc.md §8), as AsterBench v12 reads them
// (docs/asterbench-v12.md). Every page is word-only: 32-bit loads and stores. A 64-bit
// counter is read as two words; after FREEZE the counters are stable, so the reads need
// no retry, but aster_counter_read64 also retries while they count.
#ifndef ASTER_COUNTERS_H
#define ASTER_COUNTERS_H

#include <stdint.h>

#define ASTER_REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))

// Per hart: ABI 4 (docs/phase6.md), fourteen 64-bit counters at 0x00-0x6C of each page
#define ASTER_ABI4_BASE(hart)   (0x20003000u + 0x100u * (hart))
enum aster_abi4_counter {
    ASTER_C_CYCLES, ASTER_C_RETIRED, ASTER_C_MEMORY_TXNS, ASTER_C_I_ACCESSES, ASTER_C_I_MISSES,
    ASTER_C_D_ACCESSES, ASTER_C_D_MISSES, ASTER_C_BACKING, ASTER_C_AMOS, ASTER_C_SC_SUCCESS, ASTER_C_SC_FAILURE,
    ASTER_C_DIRTY_INTERVENTIONS, ASTER_C_INVALIDATIONS, ASTER_C_WRITEBACK_WORDS, ASTER_C_COUNT
};
#define ASTER_ABI4_COMMAND      0x20003080u   // hart 0 only: START, FREEZE, RESUME
enum { ASTER_ABI4_START = 1u, ASTER_ABI4_FREEZE = 2u, ASTER_ABI4_RESUME = 4u };
#define ASTER_ABI4_ABI          0x20003084u   // 4
#define ASTER_ABI4_CLOCK_HZ     0x20003088u
#define ASTER_ABI4_LINE_WORDS   0x20003090u
#define ASTER_ABI4_LINE_COUNT   0x20003094u
#define ASTER_ABI4_MEMORY_WAIT  0x20003098u   // the backing memory's wait cycles: 1 + the added waits
#define ASTER_HART_COUNT        0x20002008u

// DOT8 (v1's layout): each hart's accept, wait, complete, retire
#define ASTER_DOT8_BASE         0x20003200u
#define ASTER_DOT8(hart, event) (ASTER_DOT8_BASE + 8u * (4u * (hart) + (event)))

// The fabric's 48 counters (soc.md §8): requesters I0 D0 I1 D1 N R W
#define ASTER_FABRIC_BASE       0x20003300u
#define ASTER_FABRIC(k)         (ASTER_FABRIC_BASE + 8u * (k))
#define ASTER_FABRIC_COUNT      48u
enum { ASTER_F_ACCEPTED = 0, ASTER_F_WAITED = 7, ASTER_F_BANK_READS = 14, ASTER_F_BANK_WRITES = 18,
       ASTER_F_BANK_CONFLICTS = 22, ASTER_F_SNOOPS = 26, ASTER_F_INVALIDATIONS = 32, ASTER_F_RESV_ENDED = 38,
       ASTER_F_AMOS = 40, ASTER_F_LONGEST = 41 };
enum { ASTER_REQ_I0, ASTER_REQ_D0, ASTER_REQ_I1, ASTER_REQ_D1, ASTER_REQ_N, ASTER_REQ_R, ASTER_REQ_W };

// The DMA's counters (ABI 5, rtl/dma/aster_dma2.sv)
#define ASTER_DMA_CTR_BASE      0x30000100u
#define ASTER_DMA_CTR(i)        (ASTER_DMA_CTR_BASE + 8u * (i))
enum { ASTER_DMA_C_BUSY, ASTER_DMA_C_WAIT, ASTER_DMA_C_READS, ASTER_DMA_C_WRITES, ASTER_DMA_C_BYTES,
       ASTER_DMA_C_BACKING_READS, ASTER_DMA_C_BACKING_WRITES, ASTER_DMA_C_INVALIDATIONS = 9,
       ASTER_DMA_C_COMPLETED, ASTER_DMA_C_ABORTED, ASTER_DMA_C_ERRORS, ASTER_DMA_C_REJECTED };

// The NPU v2's totals (docs/npu.md §3)
#define ASTER_NPU2_GEOMETRY_REG 0x4000000Cu   // [7:0] ROWS, [15:8] COLS
#define ASTER_NPU2_JOB_TILES_REG 0x400000A8u
#define ASTER_NPU2_TOTAL_JOBS   0x40000100u   // 32-bit
#define ASTER_NPU2_TOTAL(i)     (0x40000108u + 8u * (i))   // cycles, active, MACs, bytes read, bytes written
enum { ASTER_NPU2_T_CYCLES, ASTER_NPU2_T_ACTIVE, ASTER_NPU2_T_MACS, ASTER_NPU2_T_BYTES_READ, ASTER_NPU2_T_BYTES_WRITTEN };

static inline uint64_t aster_counter_read64(uint32_t address) {
    uint32_t high, low, again;
    do {
        high = ASTER_REG32(address + 4u);
        low = ASTER_REG32(address);
        again = ASTER_REG32(address + 4u);
    } while (high != again);
    return ((uint64_t)high << 32) | low;
}

// The common window's cycles, low word: a time base both harts read alike
static inline uint32_t aster_window_now(void) { return ASTER_REG32(ASTER_ABI4_BASE(0)); }

#endif
