#include "Vaster_coherent_perf.h"
#include "Vaster_coherent_perf___024root.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv); Vaster_coherent_perf d;
        std::array<std::uint64_t, 14> reference{};
        bool running = false;
        auto read = [&](unsigned offset) { d.addr = 0x20003000 + offset; d.eval(); return d.rdata; };
        auto step = [&]() {
            d.clk = 0; d.eval();
            if (!d.resetn || d.start) { reference.fill(0); running = d.resetn && d.start; }
            else if (d.freeze) running = false;
            else if (d.resume_counting) running = true;
            else if (running) for (unsigned i = 0; i < 14; ++i) reference[i] += (d.events >> i) & 1;
            d.clk = 1; d.eval();
            for (unsigned i = 0; i < 14; ++i) {
                const auto value = std::uint64_t(read(i*8)) | (std::uint64_t(read(i*8+4)) << 32);
                require(value == reference[i], "ABI 4 counter/reference mismatch");
            }
            require(read(0x80) == unsigned(running), "common window status incorrect");
            require(read(0x84) == 4 && read(0x88) == 31250000 && read(0x8c) == 1 &&
                    read(0x90) == 4 && read(0x94) == 16 && read(0x98) == 0 && read(0x9c) == 14,
                    "ABI 4 metadata incorrect");
            for (unsigned offset : {1u, 0x6eu, 0x70u, 0x7cu, 0xa0u, 0x100u})
                require(read(offset) == 0, "invalid/unaligned performance read not zero");
        };
        d.resetn = d.start = d.freeze = d.resume_counting = d.events = 0;
        step(); d.resetn = 1;
        std::mt19937 rng(0xc0476);
        for (unsigned cycle = 0; cycle < 9000; ++cycle) {
            d.events = rng() & 0x3fff;
            d.start = rng() % 127 == 0; d.freeze = rng() % 29 == 0; d.resume_counting = rng() % 23 == 0;
            if (cycle == 3000 || cycle == 6000) {
                // Storage-only introspection tests carry without adding test
                // registers or alternate reset semantics to production RTL.
                for (unsigned i = 0; i < 14; ++i) {
                    reference[i] = cycle == 3000 ? 0xfffffff0ull + i : UINT64_MAX - i;
                    d.rootp->aster_coherent_perf__DOT__counters[i] = reference[i];
                }
                d.start = d.freeze = 0; d.resume_counting = 1;
            }
            if ((cycle > 3000 && cycle < 3050) || (cycle > 6000 && cycle < 6050)) {
                d.start = d.freeze = d.resume_counting = 0; d.events = 0x3fff;
            }
            step();
        }
        d.start = d.resume_counting = 0; d.freeze = 1; d.events = 0x3fff; step();
        d.freeze = 0; for (unsigned i = 0; i < 100; ++i) step();
        d.resetn = 0; step();
        std::cout << "PASS: ABI 4 fourteen-counter scoreboard, common command edges, frozen reads, metadata, 32/64-bit carry\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
