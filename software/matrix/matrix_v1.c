// 20.4's matrix: the harness around a v1 CPU kernel (software/matrix/compat/workload.h). The kernel's own
// main is compiled as v1_main (-Dmain=v1_main on its sources only); this main runs it once for a cold
// record (MATRIX_COLD, as one data word: matrix_cold.h), or twice for a warm one. A warm run's first pass is the warm-up: its record is
// dropped, .data and .bss are restored as at reset, and main is taken again, so the timed pass differs
// from the first only in its warm caches. The harness's own state lives in .private0, which the restore
// leaves alone. v1's kernels end in a loop after their record; the record's emit leaves for the return.
//
// A kernel opens its window with aster_perf_clear() or, as CoreMark and Dhrystone do, by writing START to
// v1's control word, which the shadow header points at ABI 4's (the same codes); either way the window is
// exactly v1's (START and FREEZE are the kernel's own stores), and hart 0's work interval is all of it.
//
// The restore reads .data's saved copy, so "warm" is warm after the restore; and it must never run with
// hart 1 live (the runtime's mailbox is in .bss): these kernels are hart 0's alone.
#include <stdint.h>

#include "aster.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"

#ifndef MATRIX_FAMILY
#define MATRIX_FAMILY "cpu"
#endif
#ifndef MATRIX_METHOD
#define MATRIX_METHOD "scalar"
#endif

int v1_main(void);
const volatile uint32_t matrix_v1_abi = 2u;
static void *restart[5] __attribute__((section(".private0")));
static volatile uint32_t pass_number __attribute__((section(".private0")));
static volatile uint32_t jump_reason __attribute__((section(".private0")));   // 1 the warm-up done, 2 the record out
static struct v12_record record;

// (link_matrix.ld: .data's first state is kept in .data_save, taken as main is first entered)
extern uint32_t __data_start[], __data_end[], __data_save_start[], __bss_start[], __bss_end[];

static void save_data(void) {
    uint32_t *to = __data_save_start;
    for (const uint32_t *from = __data_start; from < __data_end;) *to++ = *from++;
}

static void restore_data_and_bss(void) {
    const uint32_t *from = __data_save_start;
    for (uint32_t *to = __data_start; to < __data_end;) *to++ = *from++;
    for (uint32_t *to = __bss_start; to < __bss_end;) *to++ = 0;
    __asm__ volatile ("fence rw, rw" ::: "memory");
}

void matrix_window_prepare(void) { v12_prepare(); }

void matrix_window_end(void) {
    v12_end(&record);
    record.hart[0].work_start = 0;
    record.hart[0].work_end = (uint32_t)record.hart[0].counter[ASTER_C_CYCLES];
}

void matrix_v1_emit(const char *name, const char *category, uint32_t size, uint32_t iterations, uint32_t param,
                    uint32_t seed, uint32_t checksum, uint32_t pass) {
    (void)category;
    if (!matrix_cold && pass_number == 0) { jump_reason = 1; __builtin_longjmp(restart, 1); }   // the warm-up
    record.name = name; record.family = MATRIX_FAMILY; record.method = MATRIX_METHOD; record.window = "e2e";
    record.cache_state = MATRIX_CACHE_STATE;
    record.size = size; record.iterations = iterations; record.param = param; record.seed = seed;
    record.checksum = checksum; record.workers = 1; record.pass = (int)pass;
    v12_emit(&record);
    jump_reason = 2;
    __builtin_longjmp(restart, 1);
}

int main(void) {
    const uint32_t resumed = __builtin_setjmp(restart) ? jump_reason : 0u;
    if (resumed == 2) return record.pass ? 0 : 1;               // the record is out
    pass_number = resumed;                                      // 0 the first pass, 1 a warm run's timed pass
    if (resumed == 0) save_data();
    if (resumed == 1) restore_data_and_bss();
    v1_main();
    return 3;                                                   // (a kernel that never emitted)
}
