#include <stdint.h>
#include "asterbench.h"

// One dependent read per hop. Sequential and shuffled rings use the same
// measured loop; no RNG or index-table traversal occurs inside the window.
static volatile uint32_t links[BENCHMARK_WORDS] __attribute__((section(".bss.benchmark"), aligned(64)));
static uint32_t visited[(BENCHMARK_WORDS+31u)/32u];
#if BENCH_RANDOM
static uint32_t permutation[BENCHMARK_WORDS];
#endif
static volatile uint32_t observed_sum;
static volatile uint32_t observed_last;

__attribute__((noinline, noclone, section(".text.benchmark"))) static void walk(uint32_t steps) {
    uint32_t position = 0, sum = 0;
    for (uint32_t i = 0; i < steps; ++i) {
        position = links[position];
        sum += position;
    }
    observed_last = position;
    observed_sum = sum;
}

__attribute__((noinline, noclone, section(".text.benchmark")))
static void measure(struct aster_perf_snapshot *snapshot) {
    aster_perf_clear();
    walk(BENCHMARK_WORDS * BENCHMARK_REPETITIONS);
    aster_perf_snapshot(snapshot);
}

int main(void) {
#if BENCH_RANDOM
    uint32_t state = BENCHMARK_SEED;
    for (uint32_t i = 0; i < BENCHMARK_WORDS; ++i) permutation[i] = i;
    // Full Fisher-Yates permutation; the LCG works even with seed zero.
    for (uint32_t i = BENCHMARK_WORDS-1; i > 0; --i) {
        state = state * 1664525u + 1013904223u;
        uint32_t j = state % (i+1);
        uint32_t tmp = permutation[i]; permutation[i] = permutation[j]; permutation[j] = tmp;
    }
    for (uint32_t i = 0; i < BENCHMARK_WORDS; ++i)
        links[permutation[i]] = permutation[(i+1) % BENCHMARK_WORDS];
#else
    for (uint32_t i = 0; i < BENCHMARK_WORDS; ++i) links[i] = (i+1) % BENCHMARK_WORDS;
#endif
    // One full warm-up traversal is excluded, including all permutation work.
    walk(BENCHMARK_WORDS);
    struct aster_perf_snapshot snapshot;
    measure(&snapshot);
    const uint32_t expected = (BENCHMARK_WORDS * (BENCHMARK_WORDS-1u) / 2u) * BENCHMARK_REPETITIONS;
    uint32_t pass = observed_last == 0 && observed_sum == expected;
    // Independent post-window graph check: every node occurs exactly once,
    // all links are in range, and the ring returns to its start after N hops.
    uint32_t position = 0;
    for (uint32_t i = 0; i < BENCHMARK_WORDS; ++i) {
        if (position >= BENCHMARK_WORDS || (visited[position/32u] & (1u << (position%32u)))) {
            pass = 0;
            break;
        }
        visited[position/32u] |= 1u << (position%32u);
        position = links[position];
    }
    if (position != 0) pass = 0;
    aster_bench_emit(BENCH_RANDOM ? "walk_random" : "walk_sequential", observed_sum, pass, &snapshot);
    for (;;) { }
}
