#include <stdint.h>

#include "asterbench.h"

static volatile uint32_t source[ BENCHMARK_WORDS ];
static volatile uint32_t destination[ BENCHMARK_WORDS ];

static void copy_words(void) {
    for (uint32_t repetition = 0; repetition < BENCHMARK_REPETITIONS;
         ++repetition) {
        for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index)
            destination[index] = source[index];
    }
}

int main(void) {
    for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index) {
        source[index] = BENCHMARK_SEED ^ (index * 0x1021u);
        destination[index] = 0u;
    }

    aster_perf_clear();
    copy_words();

    struct aster_perf_snapshot snapshot;
    aster_perf_snapshot(&snapshot);

    uint32_t pass = (*ASTER_PERF_ABI == 2u);
    uint32_t checksum = 0u;
    for (uint32_t index = 0; index < BENCHMARK_WORDS; ++index) {
        if (destination[index] != source[index])
            pass = 0u;
        checksum = (checksum * 33u) ^ destination[index];
    }

    aster_bench_emit("memcpy", checksum, pass, &snapshot);

    for (;;) { }
}
