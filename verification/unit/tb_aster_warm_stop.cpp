#include "Vaster_warm_stop.h"
#include "verilated.h"
#include <iostream>
#include <stdexcept>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static void tick(Vaster_warm_stop& d) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
static void reset(Vaster_warm_stop& d) {
    d.resetn = d.host_run = d.secondary_run = d.fabric_busy = d.flush_ready = 0;
    tick(d); require(!d.hart_run && !d.admit && !d.flush_valid && !d.stop_commit, "POR not gated");
    d.resetn = 1; tick(d); require(d.stopped && !d.stop_busy, "initial stopped state missing");
    d.host_run = 1; tick(d); require(d.hart_run == 1 && d.admit == 1, "primary did not start alone");
    d.secondary_run = 1; tick(d); require(d.hart_run == 3 && d.admit == 3, "secondary release failed");
}
static void finish(Vaster_warm_stop& d, unsigned mask, unsigned running, unsigned delay) {
    for (unsigned i = 0; i < 8 && !d.flush_valid; ++i) {
        require(d.hart_run == running && !d.admit && !d.stopped, "reset occurred before dirty drain");
        tick(d);
    }
    require(d.flush_valid && d.flush_mask == mask, "wrong/missing selective flush request");
    for (unsigned i = 0; i < delay; ++i) {
        require(d.hart_run == running && !d.admit && d.flush_valid && d.flush_mask == mask && !d.stop_commit,
                "flush payload or core reset changed during backpressure");
        tick(d);
    }
    d.flush_ready = 1; d.eval(); require(d.stop_commit == mask, "stop acknowledgement not aligned with flush");
    tick(d); d.flush_ready = 0;
    require(d.hart_run == (running & ~mask) && !d.flush_valid, "wrong reset target after flush");
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv); Vaster_warm_stop d;
        unsigned cases = 0;
        for (unsigned drain : {0u, 1u, 19u, 129u}) for (unsigned delay : {0u, 1u, 31u, 257u})
        for (bool global : {false, true}) {
            reset(d);
            d.fabric_busy = 1;
            if (global) d.host_run = 0; else d.secondary_run = 0;
            d.eval(); require(d.admit == 0 && d.hart_run == 3, "stop withdrew cores / admitted new operation");
            tick(d);
            for (unsigned i = 0; i < drain; ++i) {
                require(!d.flush_valid && d.hart_run == 3 && !d.admit, "flush/reset raced accepted fabric command");
                tick(d);
            }
            d.fabric_busy = 0;
            finish(d, global ? 3 : 2, 3, delay);
            if (global) {
                require(d.stopped && !d.stop_busy, "global stop never acknowledged");
                d.secondary_run = 0; d.host_run = 1; tick(d);
                require(d.hart_run == 1, "warm start incorrectly released secondary");
            } else {
                require(!d.stopped && d.admit == 1, "selective stop changed primary run state");
                d.secondary_run = 1; tick(d); require(d.hart_run == 3, "secondary restart failed");
            }
            ++cases;
        }
        // A global stop arriving during a secondary flush must finish that
        // transaction, then flush/reset the remaining primary before STOPPED.
        reset(d); d.secondary_run = 0; tick(d);
        while (!d.flush_valid) tick(d);
        d.host_run = 0;
        finish(d, 2, 3, 8); require(!d.stopped && !d.admit, "escalated global stop acknowledged too early");
        finish(d, 3, 1, 8); require(d.stopped, "escalated global stop did not finish"); ++cases;
        // Changing requested RUN cannot cancel a flush already in progress.
        reset(d); d.host_run = 0; tick(d);
        while (!d.flush_valid) tick(d);
        d.host_run = 1; d.secondary_run = 0;
        finish(d, 3, 3, 8); require(d.stopped, "restart canceled mandatory flush");
        tick(d); require(d.hart_run == 1, "requested restart was lost"); ++cases;
#ifdef ASTER_DMA_STOP_ESCALATION
        // A transient global STOP during a selective stop is still mandatory,
        // even if a direct-SoC host reasserts RUN before the first flush ends.
        // Never widen a flush payload already offered to the cache: finish the
        // secondary flush, then independently flush/reset the primary.
        for (unsigned phase = 0; phase < 6; ++phase) for (unsigned delay : {0u,1u,31u,257u}) {
            reset(d); d.secondary_run = 0; d.fabric_busy = phase == 0; tick(d);
            if (phase >= 1) tick(d); // SETTLE1
            if (phase >= 2) tick(d); // SETTLE2
            if (phase >= 3) tick(d); // offered selective FLUSH
            if (phase == 4) for (unsigned i = 0; i < 17; ++i) tick(d);
            if (phase == 5) d.flush_ready = 1;
            d.host_run = 0; tick(d); d.host_run = 1; d.flush_ready = 0; d.eval();
            require(!d.admit,"RUN reassert canceled global escalation on selective completion");
            if (phase != 5) {
                for (unsigned i = 0; i < delay && d.fabric_busy; ++i) tick(d);
                d.fabric_busy = 0; finish(d,2,3,delay);
            }
            require(d.hart_run == 1 && !d.stopped && !d.admit,"RUN reassert reopened admissions after global escalation");
            finish(d,3,1,delay); require(d.stopped,"transient global stop failed to flush/reset primary");
            d.host_run = 0; tick(d); require(d.stopped,"STOPPED did not persist after removing RUN"); ++cases;
        }
#endif
        std::cout << "PASS: warm-stop sequencing cases=" << cases
                  << "; drain, response settlement, held flush, selective/global stop, escalation/restart\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
