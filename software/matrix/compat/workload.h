// 20.4's matrix: v1's CPU kernels (software/benchmarks) on AsterBench v12, their code and inputs
// unchanged (docs/matrix.md §4.1). This header shadows software/benchmarks/workload.h (it comes first on
// the include path) and takes over the three calls through which a v1 kernel measures itself:
//   aster_perf_clear()          v12's set-up, then START (the store, inline, as v1's)
//   aster_perf_snapshot(&s)     FREEZE (the store, inline, as v1's), then v12 reads the counters
//   aster_workload_emit(...)    the record: v12's (matrix_v1.c), or, on a warm run's first pass, a
//                               return to main's start for the timed pass
// v1's check that its counter page was the one it expected (ABI 2) is v12's to make (ABI 4's metadata,
// validated), so ASTER_PERF_ABI reads 2 here and a kernel's PASS rests on its own checks.
#ifndef ASTER_MATRIX_WORKLOAD_H
#define ASTER_MATRIX_WORKLOAD_H

#include "asterbench.h"

void matrix_window_prepare(void);
void matrix_window_end(void);
void matrix_v1_emit(const char *name, const char *category, uint32_t size, uint32_t iterations, uint32_t param,
                    uint32_t seed, uint32_t checksum, uint32_t pass);
extern const volatile uint32_t matrix_v1_abi;

#undef ASTER_PERF_ABI
#define ASTER_PERF_ABI (&matrix_v1_abi)
// v1's control word (ABI 2's START = 1, FREEZE = 2) is ABI 4's command word, whose codes are the same
#undef ASTER_PERF_CONTROL
#define ASTER_PERF_CONTROL ((volatile uint32_t *)0x20003080u)
// v1's aster_perf_clear and aster_perf_snapshot were the control word's store, inline: so are these, the
// window's START and FREEZE in the kernel's own code, v12's set-up before the one and its reading after
// the other, outside the window
#define aster_perf_clear() do { matrix_window_prepare(); __asm__ volatile ("" ::: "memory"); \
    *(volatile uint32_t *)0x20003080u = 1u; __asm__ volatile ("" ::: "memory"); } while (0)
#define aster_perf_snapshot(snapshot) do { __asm__ volatile ("" ::: "memory"); \
    *(volatile uint32_t *)0x20003080u = 2u; __asm__ volatile ("" ::: "memory"); (void)(snapshot); \
    matrix_window_end(); } while (0)
#define aster_workload_emit(name, category, size, iterations, param, seed, checksum, pass, snapshot) \
    ((void)(snapshot), matrix_v1_emit((name), (category), (size), (iterations), (param), (seed), (checksum), (pass)))

#endif
