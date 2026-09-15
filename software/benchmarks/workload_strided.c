// AsterBench v10: strided memory access.
// Touches one word every STRIDED_STRIDE words, so each access moves to a new
// cache line; the checksum is independently recomputed by the host oracle.
#include <stdint.h>
#include "workload.h"

#ifndef STRIDED_WORDS
#define STRIDED_WORDS 1024u
#endif
#ifndef STRIDED_STRIDE
#define STRIDED_STRIDE 16u
#endif

_Static_assert(STRIDED_WORDS >= 64u && STRIDED_WORDS <= 16384u, "strided words out of range");
_Static_assert((STRIDED_WORDS & (STRIDED_WORDS - 1u)) == 0u, "strided words must be a power of two");
_Static_assert(STRIDED_STRIDE >= 1u && STRIDED_STRIDE < STRIDED_WORDS, "strided stride out of range");

static volatile uint32_t array[STRIDED_WORDS];

__attribute__((noinline, noclone, section(".text.benchmark")))
static uint32_t strided_sum(uint32_t iterations) {
    uint32_t sum = 0;
    for (uint32_t repetition = 0; repetition < iterations; ++repetition)
        for (uint32_t index = 0; index < STRIDED_WORDS; index += STRIDED_STRIDE)
            sum += array[index];
    return sum;
}

int main(void) {
    for (uint32_t index = 0; index < STRIDED_WORDS; ++index)
        array[index] = BENCHMARK_SEED ^ (index * 0x1021u);

    aster_perf_clear();
    uint32_t sum = strided_sum(BENCHMARK_REPETITIONS);
    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);

    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    aster_workload_emit("strided", "memory", STRIDED_WORDS * 4u, BENCHMARK_REPETITIONS,
                        STRIDED_STRIDE, BENCHMARK_SEED, sum, pass, &snapshot);
    for (;;) { }
}
