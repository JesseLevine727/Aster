// 20.4's matrix: the cost of a work stamp (matrix_window.h: a call to v12_work_end, which reads the window's cycle
// counter), the figure docs/phase20.md quotes. Two windows on hart 0, each STAMP_COUNT steps of a loop: one
// storing a word each step, one stamping each step; the difference over STAMP_COUNT is a stamp's cost beyond a
// store. Each record's checksum is its step count. Warm only: each window is run untimed first.
#include <stdint.h>

#include "aster.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef STAMP_COUNT
#define STAMP_COUNT 64u
#endif

static volatile uint32_t sink, steps;

static __attribute__((noinline)) void loop(int stamps, int timed) {
    if (timed) matrix_open(1);
    for (uint32_t i = 0; i < STAMP_COUNT; ++i) {
        if (stamps) v12_work_end(0); else sink = i;
        steps = steps + 1u;
    }
    if (timed) matrix_close();
}

int main(void) {
    static struct v12_record record;
    int failed = 0;
    for (int stamps = 0; stamps < 2; ++stamps) {
        loop(stamps, 1);                               // (its warm-up: the same code, its window too)
        steps = 0;
        v12_prepare();
        loop(stamps, 1);
        v12_end(&record);
        matrix_hart0_whole(&record);
        record.name = stamps ? "stamp_cost_stamps" : "stamp_cost_stores"; record.family = "coherence";
        record.method = "scalar"; record.window = "kernel"; record.cache_state = MATRIX_CACHE_STATE;
        record.size = STAMP_COUNT; record.iterations = 1; record.param = 0; record.seed = 0;
        record.checksum = steps; record.workers = 1; record.pass = steps == STAMP_COUNT;
        v12_emit(&record);
        failed |= !record.pass;
    }
    return failed;
}
