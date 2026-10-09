// 20.4's matrix: the runtime's dispatch and join (docs/matrix.md §4.4's "dispatch and join cycles"; soc.md §7.3;
// software/runtime/aster_smp.h): hart 0 hands hart 1 an empty job. Two records:
//   smp_round_trip  SMP_TRIPS trips, the window the sum of one interval a trip: hart 0's START (RESUME) just
//                   before aster_smp_dispatch to its FREEZE as aster_smp_join returns.
//   smp_handoff     one trip in its window, its four moments stamped in window time (the counter page's
//                   cycles, which both harts read): hart 0 just before the dispatch (h0_work_start) and as the
//                   join returns (h0_work_end), hart 1 as the job begins (h1_work_start) and ends
//                   (h1_work_end). The dispatch takes h1_work_start - h0_work_start, the join h0_work_end -
//                   h1_work_end; each stamp is a counter read, so each difference holds about one read's delay.
// (The command word is hart 0's alone, soc.md §8, so hart 1 cannot close or open a window itself.) A stamp is a
// call and a counter read, about 20 cycles (24 at R, measured in a window of 64). The job counts its calls: each
// record's checksum is the count in its window (SMP_TRIPS, or 1). A cold run is the round trip as the first
// pass after reset (hart 1 released and ready before it); a warm run is, for each window, an untimed pass of the
// same code (round trips; one handoff), then the window.
#include <stdint.h>

#include "aster.h"
#include "aster_smp.h"
#include "asterbench_v12.h"
#include "matrix_cold.h"
#include "matrix_window.h"

#ifndef SMP_TRIPS
#define SMP_TRIPS 64u
#endif

static volatile uint32_t calls;

static void job_plain(void *arg) { (void)arg; calls = calls + 1u; }

static void job_stamped(void *arg) {
    (void)arg;
    v12_work_start(1);
    calls = calls + 1u;
    v12_work_end(1);
}

static void round_trips(int timed) {
    for (uint32_t trip = 0; trip < SMP_TRIPS; ++trip) {
        if (timed) matrix_open(trip == 0);
        aster_smp_dispatch(job_plain, 0);
        aster_smp_join();
        if (timed) matrix_close();
    }
}

// one stamped handoff; the same code warm (untimed) and timed, so both harts' caches hold it
static __attribute__((noinline)) void handoff(int timed) {
    if (timed) matrix_open(1);
    v12_work_start(0);
    aster_smp_dispatch(job_stamped, 0);
    aster_smp_join();
    v12_work_end(0);
    if (timed) matrix_close();
}

static int emit(struct v12_record *record, const char *name, uint32_t want) {
    record->name = name; record->family = "coherence"; record->method = "multicore";
    record->window = "e2e"; record->cache_state = MATRIX_CACHE_STATE;
    record->size = want; record->iterations = 1; record->param = 0; record->seed = 0;
    record->checksum = calls; record->workers = 2; record->pass = calls == want;
    v12_emit(record);
    return !record->pass;
}

int main(void) {
    static struct v12_record record;
    aster_smp_start();
    if (!matrix_cold) round_trips(1);                  // the warm-up pass (the same code, its window too)
    calls = 0;
    v12_prepare();
    round_trips(1);
    v12_end(&record);
    const uint32_t cycles = (uint32_t)record.hart[0].counter[ASTER_C_CYCLES];
    for (int h = 0; h < 2; ++h) { record.hart[h].work_start = 0; record.hart[h].work_end = cycles; }
    int failed = emit(&record, "smp_round_trip", SMP_TRIPS);
    if (!matrix_cold) {
        handoff(1);                                    // the handoff's warm-up (the same code, its window too)
        calls = 0;
        v12_prepare();
        handoff(1);
        v12_end(&record);
        failed |= emit(&record, "smp_handoff", 1u);
    }
    return failed;
}
