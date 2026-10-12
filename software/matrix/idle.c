// Phase 21 (docs/phase21.md §4, D4): the idle SoC, the baseline of each energy figure's "above idle" form. One e2e
// window of IDLE_CYCLES on hart 0, spinning on its cycle CSR (no memory access, its loop in the instruction cache);
// the DMA and the NPU idle. Hart 1 as a program of each kind leaves it:
//   IDLE_HART1 0: held in reset, as a single-worker program leaves it;
//   IDLE_HART1 1: released and waiting on the dispatch word in its own data cache (aster_smp_start), as a two-hart
//                 program leaves it between jobs.
// The record's checksum is the window's spin count (its passes through the loop).
#include <stdint.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef IDLE_CYCLES
#define IDLE_CYCLES 100000u
#endif
#ifndef IDLE_HART1
#define IDLE_HART1 0
#endif

static inline uint32_t cycle_low(void) {
    uint32_t value;
    __asm__ volatile ("csrr %0, cycle" : "=r"(value));
    return value;
}

static __attribute__((noinline)) uint32_t spin(void) {
    const uint32_t start = cycle_low();
    uint32_t passes = 0;
    while (cycle_low() - start < IDLE_CYCLES) passes++;
    return passes;
}

int main(void) {
    static struct v12_record record;
#if IDLE_HART1
    aster_smp_start();
#endif
    v12_prepare();
    matrix_open(1);
    const uint32_t passes = spin();
    matrix_close();
    v12_end(&record);
    matrix_hart0_whole(&record);
    record.name = IDLE_HART1 ? "idle_hart1_spinning" : "idle_hart1_reset";
    record.family = "cpu"; record.method = "scalar"; record.window = "e2e"; record.cache_state = MATRIX_CACHE_STATE;
    record.size = IDLE_CYCLES; record.iterations = 1; record.param = IDLE_HART1; record.seed = 0;
    record.checksum = passes; record.workers = 1; record.pass = passes > 0;
    v12_emit(&record);
    return !record.pass;
}
