#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_MEMORY_WAIT 0
#define ASTER_L1 1
#endif
static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
class Bench {
public:
    Vaster_coherent_soc d;
    std::array<std::uint32_t, 16384> oracle;
    std::mt19937 rng{0x570f6};
    std::array<unsigned, 2> retired{};
    unsigned stores = 0, stops = 0, jobs = 0;
    unsigned secondary_stops = 0, primary_entries = 0;
    bool lifecycle = false;
    bool block_uart = false, checking_uart = false;
    std::string line;
    Bench() {
        // Invocation supplies +ram_fill=a5a5a5a5; no hidden reference RAM writes.
        oracle.fill(0xa5a5a5a5);
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        require(!d.hart_run && !d.retired && !d.backing_valid, "POR did not gate the cluster");
        d.resetn = 1; low(); rise(); require(d.stopped, "initial stopped acknowledgement missing");
        snapshot();
    }
    void low() {
        d.clk = 0; d.uart_tx_ready = !block_uart && rng() % 4 != 0; d.eval();
        require(!d.hart_trap && !d.fault_valid, "unexpected coherent SoC trap");
    }
    void rise() {
        if (d.stop_commit == 2) {
            require(d.hart_run == 3, "selective stop lost primary before commit");
            require((d.reservations & 2) != 0, "secondary-reset fixture did not hold its LR reservation");
            ++secondary_stops;
        }
        if (d.store_commit) {
            require(d.store_addr >= 0x10000000 && d.store_addr < 0x10010000 && d.store_mask,
                    "invalid acknowledged store observation");
            const auto i = (d.store_addr - 0x10000000)/4;
            oracle[i] = merge(oracle[i], d.store_data, d.store_mask); ++stores;
        }
        if (d.uart_tx_valid && d.uart_tx_ready && checking_uart) {
            if (char(d.uart_tx_data) != '\n') line += char(d.uart_tx_data);
            else {
                if (lifecycle) {
                    require(line == "COHERENT LIFECYCLE PASS", "secondary lifecycle firmware failed"); ++jobs;
                } else if (line == "RV32A JOB PASS") ++jobs;
                else require(line == "RV32A DIRECTED PASS", "coherent SoC runtime reported failure");
                line.clear();
            }
        }
        d.clk = 1; d.eval();
        for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) ++retired[h];
        if ((d.retired & 1) && d.retired_pc[0] == 0) ++primary_entries;
    }
    void snapshot() {
        require(d.stopped && !d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations,
                "STOPPED before drain/flush/reset/reservation completion");
        d.boot_we = 0;
        for (unsigned i = 0; i < oracle.size(); ++i) {
            d.host_ram_addr = i*4;
            low(); rise(); low(); rise(); // Valid for asynchronous AND synchronous RAM.
            require(!d.store_commit && !d.backing_valid, "stopped RAM snapshot raced a transaction");
            require(d.host_ram_rdata == oracle[i], "warm stop lost or invented acknowledged RAM data");
        }
    }
    void finish_stop() {
        d.host_run = 0;
        unsigned cycles = 0;
        while (!d.stopped && cycles++ < 1000000) {
            low(); require(!d.uart_tx_valid, "global stop exposed discarded UART bytes"); rise();
        }
        require(d.stopped && cycles < 1000000, "warm stop deadlocked (including full UART sink)");
        ++stops; d.boot_we = 0; snapshot();
    }
    void start(bool full) {
        require(d.stopped, "attempted warm start before stop acknowledged");
        line.clear(); jobs = 0; retired = {}; checking_uart = full; block_uart = false;
        secondary_stops = primary_entries = 0;
        d.host_run = 1;
        // Attempt to corrupt the reset vector while RUN/drain is active. The
        // next warm boot proves the loader gate did not accept this poison.
        d.boot_addr = 0; d.boot_wdata = 0xffffffff; d.boot_wstrb = 15; d.boot_we = 1;
    }
    void full() {
        start(true);
        unsigned cycles = 0;
        const unsigned expected_jobs = lifecycle ? 1 : 3;
        for (; cycles < 10000000 && jobs < expected_jobs; ++cycles) { low(); rise(); }
        require(jobs == expected_jobs && line.empty(), "coherent SoC C workload timed out");
        for (unsigned h = 0; h < 2; ++h)
            require(h < ASTER_HART_COUNT ? retired[h] > (lifecycle && ASTER_HART_COUNT == 1 ? 100u : 1000u) : retired[h] == 0,
                    "missing/unexpected actual hart work");
        require(primary_entries == 1, "primary restarted unexpectedly during secondary lifecycle");
        finish_stop();
        if (lifecycle) require(secondary_stops == (ASTER_HART_COUNT == 2 ? 8u : 0u), "selective reset count incorrect");
        else {
            for (unsigned i = 1; i <= 4; ++i)
                require(oracle[0x8000/4+i] == 128 * ASTER_HART_COUNT, "independent completed-job counter mismatch");
            require(oracle[0x8000/4+5] == 8256 * (ASTER_HART_COUNT == 2 ? 3 : 1), "independent locked sum mismatch");
        }
        std::cout << "PASS: coherent SoC " << (lifecycle ? "lifecycle" : "runtime") << " harts=" << ASTER_HART_COUNT << " caches=" << ASTER_L1
                  << " wait=" << ASTER_MEMORY_WAIT << " jobs=" << jobs << " cycles=" << cycles
                  << " retired=" << retired[0] << "," << retired[1] << " safe RAM snapshot=65536 bytes\n";
    }
    void abort(unsigned point) {
        start(false); block_uart = point == 7;
        bool reached = false;
        for (unsigned cycle = 0; cycle < 10000000; ++cycle) {
            low();
            const bool ram = d.backing_addr >= 0x10000000 && d.backing_addr < 0x10010000;
            bool trigger = false;
            switch (point) {
                case 0: trigger = cycle == 1; break;
                case 1: trigger = d.backing_valid && ram && d.backing_mask == 0; break;
                case 2: trigger = d.backing_valid && ram && d.backing_mask != 0; break;
                case 3: trigger = d.atomic_read_commit; break;
                case 4: trigger = d.atomic_write_pending; break;
                case 5: trigger = d.reservations != 0; break;
                case 6: trigger = d.store_commit && d.store_mask == 1; break;
                case 7: trigger = d.backing_valid && d.backing_addr == 0x20000000 && !d.backing_ready; break;
                case 8: trigger = d.atomic_active && d.store_owner == 1; break;
                case 9: trigger = d.backing_valid && ram && !d.backing_ready; break;
            }
            if (trigger) { d.host_run = 0; d.eval(); reached = true; }
            rise();
            if (reached) break;
        }
        require(reached, "adversarial stop fixture not reached");
        finish_stop();
        std::cout << "PASS: coherent SoC adversarial warm stop point=" << point
                  << " all acknowledged RAM retained, reservations cleared\n";
    }
};
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Bench b;
        for (int i = 1; i < argc; ++i) if (std::string(argv[i]) == "--lifecycle") b.lifecycle = true;
        b.full(); b.full();
        for (unsigned point = 0; point < 10 && !b.lifecycle; ++point) {
            if (point == 8 && ASTER_HART_COUNT == 1) continue;
            if (point == 9 && ASTER_MEMORY_WAIT == 0) continue;
            b.abort(point);
        }
        b.full(); // Includes reset-vector loader protection after the last stop.
        std::cout << "PASS: coherent SoC closeout stops=" << b.stops << " architectural stores=" << b.stores
                  << "; no destructive reset used after initial POR\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
