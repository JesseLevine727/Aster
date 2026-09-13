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
    for (unsigned b = 0; b < 4; ++b) if (mask & (1u << b))
        old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
class Bench {
public:
    Vaster_coherent_soc d;
    std::array<std::uint32_t,16384> oracle{};
    std::array<std::uint64_t,14> dma_counts{};
    std::array<std::array<std::uint64_t,14>,2> cpu_counts{};
    std::array<unsigned,2> retired{};
    std::mt19937 rng{0xa57e7};
    std::array<std::uint32_t,3> descriptor{};
    std::vector<std::uint8_t> source_snapshot;
    std::uint32_t copied = 0, destination = 0;
    unsigned jobs = 0, stops = 0, secondary_stops = 0, starts = 0, cpu_stores = 0, dma_stores = 0;
    unsigned dma_reads = 0, dma_writes = 0, reservation_clears = 0;
    bool counting = false, received = false, checking_uart = true;
    std::string line;

    Bench() {
        oracle.fill(0xa5a5a5a5);
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; step(); require(d.stopped, "missing initial STOPPED"); snapshot();
    }
    static unsigned index(std::uint32_t address) {
        require(address >= 0x10000000 && address < 0x10010000, "observed store escaped RAM");
        return (address-0x10000000)/4;
    }
    std::uint8_t byte(std::uint32_t address) const { return oracle[index(address)] >> (8*(address%4)); }
    void low() {
        d.clk = 0; d.uart_tx_ready = rng()%4 != 0; d.eval();
        require(!d.hart_trap && !d.fault_valid, "compiled DMA runtime trapped/faulted");
    }
    void rise() {
        unsigned clear_mask = 0, device_clear = 0;
        require(!(d.store_commit && d.dma_store_commit), "CPU/DMA architectural stores interleaved");
        require(!d.dma_request_ready || d.dma_request_pending, "DMA response without owned request");
        require(!d.atomic_active || !d.backing_device, "device traffic entered whole AMO/CPU atomic lock");
        if (d.store_commit) {
            auto n = index(d.store_addr); oracle[n] = merge(oracle[n],d.store_data,d.store_mask); ++cpu_stores;
        }
        // Observe actual CPU MMIO acceptance, not the engine's captured state.
        if (d.backing_valid && d.backing_ready && !d.backing_device && !d.backing_owner && (d.backing_addr >> 12) == 0x30000) {
            unsigned offset = d.backing_addr & 4095;
            if (offset <= 8 && !(offset%4) && d.backing_mask)
                descriptor[offset/4] = merge(descriptor[offset/4],d.backing_data,d.backing_mask);
            if (offset == 12 && (d.backing_mask & 1) && (d.backing_data & 255) == 1 && !d.dma_busy && !d.stop_busy && d.host_run) {
                ++starts; copied = 0; destination = descriptor[1]; source_snapshot.clear();
                auto s = descriptor[0], dst = descriptor[1], n = descriptor[2];
                if (n && s >= 0x10000000 && std::uint64_t(s)+n <= 0x10008000 &&
                    dst >= 0x10000000 && std::uint64_t(dst)+n <= 0x10008000 &&
                    (std::uint64_t(s)+n <= dst || std::uint64_t(dst)+n <= s))
                    for (unsigned i = 0; i < n; ++i) source_snapshot.push_back(byte(s+i));
            }
        }
        if (d.dma_store_commit) {
            require(d.backing_valid && d.backing_ready && d.backing_device && d.backing_mask,
                    "DMA architectural commit was not actual destination acceptance");
            for (unsigned b = 0; b < 4; ++b) if (d.backing_mask & (1u << b)) {
                require(copied < source_snapshot.size() && d.backing_addr+b == destination+copied,
                        "DMA copied beyond descriptor / repeated or skipped destination byte");
                require(std::uint8_t(d.backing_data >> (8*b)) == source_snapshot[copied], "DMA byte differs from independent source snapshot");
                ++copied;
            }
            oracle[index(d.backing_addr)] = merge(oracle[index(d.backing_addr)],d.backing_data,d.backing_mask);
            ++dma_stores;
            for (unsigned h = 0; h < 2; ++h)
                if ((d.reservations & (1u << h)) && d.reservation_addr[h]/4 == d.backing_addr/4)
                    device_clear |= 1u << h;
        }
        const bool read = d.dma_request_pending && d.dma_request_ready && !d.dma_request_mask;
        const bool write = d.dma_request_pending && d.dma_request_ready && d.dma_request_mask;
        if (read) { require(d.dma_request_rdata == oracle[index(d.dma_request_addr)], "coherent DMA source read stale"); ++dma_reads; }
        if (write) ++dma_writes;
        require(d.dma_events[0] == d.dma_busy && d.dma_events[1] == (d.dma_request_pending && !d.dma_request_ready) &&
                d.dma_events[2] == read && d.dma_events[3] == write, "DMA activity/transaction event mux incorrect");
        unsigned bytes = 0;
        if (d.dma_store_commit) for (unsigned b = 0; b < 4; ++b) bytes += (d.backing_mask >> b)&1;
        require(d.dma_events[4] == bytes &&
                d.dma_events[5] == (d.backing_valid && d.backing_ready && d.backing_device && !d.backing_mask) &&
                d.dma_events[6] == (d.backing_valid && d.backing_ready && d.backing_device && d.backing_mask),
                "DMA byte/backing accounting mixed payload and maintenance");
        for (unsigned h = 0; h < 2; ++h) if (d.backing_device) require(!(d.perf_events[h] & (1u << 7)), "DMA charged CPU backing counter");
        if (d.stopped || d.perf_start) { dma_counts.fill(0); cpu_counts = {}; counting = !d.stopped && d.perf_start; }
        else if (d.perf_freeze) counting = false;
        else if (d.perf_resume) counting = true;
        else if (counting) {
            for (unsigned i = 0; i < 14; ++i) {
                dma_counts[i] += d.dma_events[i];
                for (unsigned h = 0; h < 2; ++h) cpu_counts[h][i] += (d.perf_events[h] >> i)&1;
            }
        }
        if (d.stop_commit) {
            require(!d.fabric_busy && !d.dma_request_pending, "reset/flush completion raced owned DMA/CPU transaction");
            if (d.stop_commit & 1) require(!d.dma_busy, "global STOP reset an unquiesced DMA job");
            else { require(d.hart_run == 3, "selective reset lost primary"); ++secondary_stops; }
            clear_mask = d.stop_commit;
        }
        if (d.uart_tx_valid && d.uart_tx_ready && checking_uart) {
            if (d.uart_tx_data != '\n') line += char(d.uart_tx_data);
            else {
                if (line == "DMA JOB PASS") ++jobs;
                else if (line == "DMA COUNTERS PASS") received = true;
                else require(line == "DMA DIRECTED PASS" || line == "DMA RESERVATIONS PASS", line.c_str());
                line.clear();
            }
        }
        unsigned previous_reservations = d.reservations;
        bool device_effect = d.backing_valid && d.backing_ready && d.backing_device;
        d.clk = 1; d.eval();
        if (previous_reservations && !d.reservations) ++reservation_clears;
        require(!(d.reservations & clear_mask), "lifecycle reservation clear missed its edge");
        if (device_effect || read)
            require(d.reservations == (previous_reservations & ~device_clear & ~clear_mask),
                    "DMA reservation invalidation not exact on payload edge / read or maintenance cleared reservation");
        for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) ++retired[h];
        require(d.dma_counting == counting, "DMA bank common-window state mismatch");
        for (unsigned i = 0; i < 14; ++i) require(d.dma_counters[i] == dma_counts[i], "integrated DMA counter increment/carry mismatch");
    }
    void step() { low(); rise(); }
    void snapshot() {
        require(d.stopped && !d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations && !d.dma_busy,
                "STOPPED before full drain/flush/reset");
        d.boot_we = 0;
        for (unsigned i = 0; i < oracle.size(); ++i) {
            d.host_ram_addr = i*4; step(); step();
            require(!d.backing_valid && d.host_ram_rdata == oracle[i], "full stopped-RAM oracle mismatch (CPU/DMA data lost)");
        }
    }
    void stop() {
        d.host_run = 0;
        for (unsigned cycle = 0; cycle < 2000000 && !d.stopped; ++cycle) step();
        require(d.stopped, "DMA-aware warm stop deadlocked"); ++stops; snapshot();
    }
    void start(bool full) {
        require(d.stopped, "restart before safe STOPPED");
        checking_uart = full; line.clear(); received = false; jobs = secondary_stops = 0; retired = {};
        descriptor.fill(0); source_snapshot.clear(); copied = 0;
        d.host_run = 1;
        d.boot_we = 1; d.boot_addr = 0; d.boot_wdata = 0xffffffff; d.boot_wstrb = 15; // must be denied while running
    }
    void full() {
        start(true);
        unsigned cycle = 0;
        for (; cycle < 500000000 && !received; ++cycle) step();
        require(received && jobs == 3 && line.empty(), "real DMA firmware/counter capture timed out");
        require(secondary_stops == (ASTER_HART_COUNT == 2 ? 8u : 0u), "selective DMA pause/reset count mismatch");
        const auto counts = dma_counts; const auto cpu = cpu_counts;
        stop();
        const unsigned offset = 0x8000/4;
        require(oracle[offset] == 3 && oracle[offset+1] == 320 && oracle[offset+6] == ASTER_HART_COUNT && oracle[offset+7] == 1024,
                "independent runtime descriptor/job/hart result mismatch");
        require(oracle[offset+8] == 5 && oracle[offset+9] == 31250000, "firmware ABI/clock metadata mismatch");
        for (unsigned i = 0; i < 14; ++i) {
            auto captured = std::uint64_t(oracle[offset+16+2*i]) | (std::uint64_t(oracle[offset+17+2*i]) << 32);
            require(captured == counts[i], "RAM firmware DMA counter snapshot differs from actual accepted events");
            for (unsigned h = 0; h < 2; ++h) {
                auto actual = std::uint64_t(oracle[offset+44+28*h+2*i]) | (std::uint64_t(oracle[offset+45+28*h+2*i]) << 32);
                require(actual == cpu[h][i], "RAM firmware CPU counters disagree with common window");
            }
        }
        for (unsigned h = 0; h < 2; ++h) require(h < ASTER_HART_COUNT ? retired[h] > 1000 : !retired[h], "missing/unexpected real hart execution");
        require(counts[4] && counts[2] && counts[3] && counts[10] == 331+(ASTER_HART_COUNT == 2 ? 8u : 0u) &&
                counts[11] == 1 && counts[12] == 6 && counts[13] == 0,
                "runtime DMA success/abort/error/reject counts differ from the submitted jobs (including owner denial)");
        std::cout << "PASS: real DMA runtime harts=" << ASTER_HART_COUNT << " cache=" << ASTER_L1 << " wait=" << ASTER_MEMORY_WAIT
                  << " cycles=" << cycle << " bytes=" << counts[4] << " jobs=3 directed=320 selective=" << secondary_stops
                  << " retired=" << retired[0] << "," << retired[1] << "; full RAM, same-value LR/SC, peer publication, driver errors/abort, exact counters\n";
    }
    void abort(unsigned point) {
        start(false); bool reached = false;
        for (unsigned n = 0; n < 10000000; ++n) {
            low();
            bool trigger = point == 0 ? d.dma_busy && !d.dma_request_pending :
                point == 1 ? d.dma_request_pending && !d.dma_request_mask && !d.dma_request_ready :
                point == 2 ? d.dma_request_pending && d.dma_request_mask && !d.dma_request_ready :
                point == 3 ? d.dma_store_commit :
                             d.dma_request_pending && !d.dma_request_mask && d.dma_request_ready;
            if (trigger) { d.host_run = 0; d.eval(); reached = true; }
            rise(); if (reached) break;
        }
        require(reached, "DMA adversarial stop trigger was not reached"); stop();
        std::cout << "PASS: real DMA warm stop point=" << point << " full acknowledged RAM retained; no destructive reset\n";
    }
};
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc,argv); std::cout.setf(std::ios::unitbuf); Bench b;
        b.full(); b.full();
        for (unsigned point = 0; point < 5; ++point) b.abort(point);
        b.full();
        std::cout << "PASS: DMA SoC closeout stops=" << b.stops << " CPU stores=" << b.cpu_stores
                  << " DMA stores=" << b.dma_stores << " reads=" << b.dma_reads << " writes=" << b.dma_writes
                  << " reservation-clears=" << b.reservation_clears << '\n';
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
