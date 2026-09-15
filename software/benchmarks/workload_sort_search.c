// AsterBench v10: sorting and binary search CPU baseline.
// Seeded pseudo-random data is insertion-sorted in place, folded into a
// checksum, and a binary search result is folded in. The host oracle
// recomputes the same checksum independently.
#include <stdint.h>
#include "workload.h"

#ifndef SORT_ITEMS
#define SORT_ITEMS 256u
#endif

_Static_assert(SORT_ITEMS >= 8u && SORT_ITEMS <= 4096u, "sort items out of range");

static uint32_t values[SORT_ITEMS];

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t generate(uint32_t index) {
    uint32_t value = BENCHMARK_SEED ^ (index * 0x9e3779b9u);
    value ^= value >> 16;
    value *= 0x7feb352du;
    value ^= value >> 15;
    value *= 0x846ca68bu;
    return value ^ (value >> 16);
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static void insertion_sort(void) {
    for (uint32_t i = 1; i < SORT_ITEMS; ++i) {
        const uint32_t key = values[i];
        uint32_t j = i;
        while (j > 0u && values[j - 1u] > key) {
            values[j] = values[j - 1u];
            --j;
        }
        values[j] = key;
    }
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t binary_search(uint32_t target) {
    uint32_t low = 0, high = SORT_ITEMS;
    while (low < high) {
        const uint32_t mid = (low + high) / 2u;
        if (values[mid] < target) low = mid + 1u;
        else high = mid;
    }
    return low;
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t run(uint32_t iterations) {
    uint32_t checksum = 0;
    for (uint32_t repetition = 0; repetition < iterations; ++repetition) {
        for (uint32_t index = 0; index < SORT_ITEMS; ++index) values[index] = generate(index);
        insertion_sort();
        for (uint32_t index = 0; index < SORT_ITEMS; ++index)
            checksum = (checksum * 33u) ^ values[index];
        checksum = (checksum * 33u) ^ binary_search(values[SORT_ITEMS / 3u]);
    }
    return checksum;
}

int main(void) {
    aster_perf_clear();
    uint32_t checksum = run(BENCHMARK_REPETITIONS);
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);

    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    for (uint32_t index = 1; index < SORT_ITEMS; ++index)
        if (values[index - 1u] > values[index]) pass = 0u;
    aster_workload_emit("sort_search", "cpu", SORT_ITEMS * 4u, BENCHMARK_REPETITIONS,
                        SORT_ITEMS, BENCHMARK_SEED, checksum, pass, &snapshot);
    for (;;) { }
}
