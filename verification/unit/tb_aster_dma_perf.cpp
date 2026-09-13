#include "Vaster_dma_perf.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv); Vaster_dma_perf d;
        std::mt19937 rng(0xa57e7);
        std::array<std::uint64_t, 14> expected{};
        bool running = false;
        auto step = [&]() {
            d.clk = 0; d.eval();
            if (!d.resetn || d.start) { expected.fill(0); running = d.resetn && d.start; }
            else if (d.freeze) running = false;
            else if (d.resume_counting) running = true;
            else if (running) for (unsigned i = 0; i < 14; ++i) expected[i] += d.increments[i];
            d.clk = 1; d.eval();
            require(d.running == running, "DMA common-window running state mismatch");
            for (unsigned i = 0; i < 14; ++i) require(d.counters[i] == expected[i], "DMA increment/command/carry mismatch");
        };
        auto read = [&](unsigned address, std::uint32_t value) {
            d.addr = address; d.eval(); require(d.rdata == value, "DMA counter/metadata/invalid offset mismatch");
        };
        d.resetn = d.start = d.freeze = d.resume_counting = 0; step(); d.resetn = 1;
        for (unsigned command = 0; command < 8; ++command) {
            d.start = command & 1; d.freeze = (command >> 1)&1; d.resume_counting = (command >> 2)&1;
            for (unsigned i = 0; i < 14; ++i) d.increments[i] = 7;
            step();
        }
        d.start = 1; d.freeze = d.resume_counting = 0; step(); d.start = 0;
        for (unsigned i = 0; i < 14; ++i) {
            // Observation-port state deposit tests rare carry without an RTL
            // test mode or simulating billions of clocks.
            expected[i] = i%2 ? ~std::uint64_t(0)-3 : 0xfffffffc;
            d.counters[i] = expected[i];
        }
        for (unsigned n = 0; n < 10000; ++n) {
            for (unsigned i = 0; i < 14; ++i) d.increments[i] = rng()%8;
            d.start = n > 32 && rng()%191 == 0;
            d.freeze = n > 32 && rng()%23 == 0;
            d.resume_counting = n > 32 && rng()%19 == 0;
            d.resetn = n <= 32 || rng()%251 != 0;
            step();
            for (unsigned i = 0; i < 14; ++i) {
                read(0x100+8*i, expected[i]); read(0x104+8*i, expected[i] >> 32);
            }
        }
        for (unsigned address = 0; address < 4096; ++address) {
            if (address >= 0x100 && address < 0x170 && !(address%4)) continue;
            if (address >= 0x180 && address < 0x1a0 && !(address%4)) continue;
            read(address,0);
        }
        read(0x180,running); read(0x184,5); read(0x188,31250000); read(0x18c,5);
        read(0x190,4); read(0x194,16); read(0x198,0); read(0x19c,14);
        std::cout << "PASS: DMA ABI 5 fourteen-counter increments, exact common-window command priority/exclusion,"
                     " frozen reads, metadata, all offsets and 32/64-bit carry/rollover\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
