#include "Vaster_perf_counters.h"
#include "Vaster_perf_counters___024root.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_perf_counters dut;
        std::array<std::uint64_t, 8> reference{};
        bool running = false;
        auto step = [&]() {
            dut.clk = 0; dut.eval();
            const bool command = dut.we && (dut.wstrb & 1) && dut.addr == 0x20003038;
            const unsigned op = command ? dut.wdata & 255 : 0;
            if (!dut.rst_n || op == 1) {
                reference.fill(0);
                running = dut.rst_n && op == 1;
            } else if (op == 2) running = false;
            else if (op == 4) running = true;
            else if (running) {
                const std::array<unsigned, 8> events = {
                    dut.cycle_en, dut.instr_retired, dut.mem_transaction, dut.cache_access,
                    dut.cache_miss, dut.dma_bytes, dut.accelerator_active, dut.backing_transaction
                };
                for (unsigned i = 0; i < 8; ++i) reference[i] += events[i];
            }
            dut.clk = 1; dut.eval();
        };
        auto read = [&](unsigned offset) {
            dut.addr = 0x20003000 + offset; dut.eval();
            return dut.rdata;
        };
        auto verify = [&]() {
            for (unsigned i = 0; i < 8; ++i) {
                const unsigned offset = i == 7 ? 0x40 : 8*i;
                const std::uint64_t actual = read(offset) | (std::uint64_t(read(offset+4)) << 32);
                require(actual == reference[i], "counter/reference mismatch");
            }
            require(read(0x38) == unsigned(running), "running status mismatch");
            require(read(1) == 0 && read(0x3c) == 0 && read(0x60) == 0, "invalid read not zero");
            require(read(0x48) == 2 && read(0x4c) == 31250000 && read(0x50) == 1 &&
                    read(0x54) == 4 && read(0x58) == 16 && read(0x5c) == 0,
                    "configuration register mismatch");
        };
        dut.rst_n = 0; dut.we = 0;
        step(); verify();
        dut.rst_n = 1;
        std::mt19937 random(0xa57e);
        for (unsigned cycle = 0; cycle < 6000; ++cycle) {
            dut.cycle_en = random() & 1;
            dut.instr_retired = random() & 1;
            dut.mem_transaction = random() & 1;
            dut.cache_access = random() & 1;
            dut.cache_miss = random() & 1;
            dut.dma_bytes = random();
            dut.accelerator_active = random() & 1;
            dut.backing_transaction = random() & 1;
            dut.we = random() % 4 == 0;
            dut.wstrb = random() & 15;
            dut.wdata = random() & 7;
            dut.addr = 0x20003038 + (random() % 4 == 0 ? 4 : 0);
            // Seed only storage near overflow via Verilator introspection; no
            // test-only MMIO or production RTL reset semantics are introduced.
            if (cycle == 3000) {
                for (unsigned i = 0; i < 8; ++i) {
                    reference[i] = UINT64_MAX - i;
                    dut.rootp->aster_perf_counters__DOT__counters[i] = reference[i];
                }
                dut.we = 1; dut.wstrb = 1; dut.wdata = 4; dut.addr = 0x20003038;
            }
            if (cycle > 3000 && cycle < 3020) {
                dut.we = 0;
                dut.cycle_en = dut.instr_retired = dut.mem_transaction = dut.cache_access = 1;
                dut.cache_miss = dut.accelerator_active = dut.backing_transaction = 1;
                dut.dma_bytes = 0xffffffffu;
            }
            step(); verify();
        }
        // Freeze with every event active; all counters share the same edge.
        dut.we = 1; dut.wstrb = 1; dut.wdata = 2; dut.addr = 0x20003038;
        step(); verify();
        dut.we = 0;
        for (unsigned i = 0; i < 100; ++i) { step(); verify(); }
        dut.rst_n = 0; step(); verify();
        std::cout << "PASS: counters/events, command strobes, freeze/resume, reset and 64-bit rollover\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
