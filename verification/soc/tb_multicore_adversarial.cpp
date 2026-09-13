#include "Vaster_multicore_probe.h"
#include "verilated.h"
#include <array>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static void edge(Vaster_multicore_probe& d) { d.clk = 1; d.eval(); d.clk = 0; d.eval(); }
static void reset(Vaster_multicore_probe& d) {
    d.rst_n = 0; d.clk = 0; d.eval();
    for (unsigned i = 0; i != 8; ++i) edge(d);
    require(!d.lower_valid && !d.lower_ready && !d.hart_run && !d.hart_trap && !d.retired, "reset did not clear core/fabric state");
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        require(argc >= 2, "need --faults or --resets seed");
        const bool faults = std::string(argv[1]) == "--faults";
        require(faults || (std::string(argv[1]) == "--resets" && argc >= 3), "bad test mode");
        Vaster_multicore_probe d;
        d.uart_tx_ready = 1; d.probe_index = 0;
        reset(d);
        if (faults) {
            for (unsigned boot = 0; boot != 2; ++boot) {
                reset(d); d.rst_n = 1; d.eval();
                unsigned trap_count = 0, trap_age = 0, primary_during_trap = 0;
                bool previous = false, pass = false;
                std::string serial;
                for (unsigned cycle = 0; cycle != 5000000; ++cycle) {
                    require(!(d.hart_trap & 1), "primary unexpectedly trapped");
                    const bool trapped = d.hart_trap & 2;
                    if (trapped && !previous) { ++trap_count; trap_age = 0; }
                    if (trapped) {
                        ++trap_age;
                        if (trap_age > 4) require(!(d.retired & 2), "trapped secondary falsely retired instructions");
                        primary_during_trap += d.retired & 1;
                    }
                    previous = trapped;
                    if (d.uart_tx_valid) {
                        serial += char(d.uart_tx_data);
                        if (serial.back() == '\n') {
                            if (serial != "MULTICORE FAULTS PASS\n")
                                throw std::runtime_error("fault firmware output: " + serial + " traps=" + std::to_string(trap_count));
                            pass = true; break;
                        }
                        require(serial.size() < 128, "unterminated fault output");
                    }
                    edge(d);
                }
                require(pass && trap_count == 12 && primary_during_trap > 1200, "missing traps or independent primary progress");
                std::cout << "PASS: real-core faults boot=" << boot << " traps=" << trap_count
                          << " primary_retired_while_peer_trapped=" << primary_during_trap << '\n';
            }
        } else {
            const uint32_t seed = std::stoul(argv[2], nullptr, 0);
            std::mt19937 rng(seed);
            unsigned aborted_stores = 0, aborted_reads = 0, preserved_stores = 0;
            // Both owners, ROM refills/fetches, RAM reads and RAM writes. Include
            // randomized delays and an accepted-store retention test each round.
            for (unsigned trial = 0; trial != 24; ++trial) {
                reset(d); d.rst_n = 1; d.eval();
                const unsigned owner = trial % 2, kind = (trial/2) % 3;
                unsigned skip = rng()%7;
                bool found = false;
                for (unsigned cycle = 0; cycle != 3000000; ++cycle) {
                    require(!d.hart_trap, "stress firmware unexpectedly trapped");
                    const bool ram = d.lower_addr >= 0x10000000 && d.lower_addr < 0x10010000;
                    const bool match = kind == 0 ? d.lower_instr && d.lower_addr < 0x10000 :
                                       kind == 1 ? ram && !d.lower_wstrb : ram && d.lower_wstrb;
                    if (d.lower_valid && !d.lower_ready && d.lower_owner == owner && match) {
                        if (skip) { --skip; edge(d); continue; }
                        d.probe_index = (d.lower_addr >> 2) & 16383; d.eval();
                        const uint32_t old = d.probe_word;
                        const bool store = d.lower_wstrb != 0;
                        reset(d);
                        require(d.probe_word == old, "global reset committed an unaccepted store or changed RAM");
                        if (store) ++aborted_stores; else ++aborted_reads;
                        found = true; break;
                    }
                    edge(d);
                }
                require(found, "never reached requested real-core pending transaction");
                d.rst_n = 1; d.eval(); found = false;
                for (unsigned cycle = 0; cycle != 3000000; ++cycle) {
                    if (d.lower_valid && d.lower_ready && d.lower_owner == owner && d.lower_wstrb &&
                        d.lower_addr >= 0x10000000 && d.lower_addr < 0x10010000) {
                        d.probe_index = (d.lower_addr >> 2) & 16383; d.eval();
                        uint32_t expected = d.probe_word;
                        for (unsigned b = 0; b != 4; ++b) if (d.lower_wstrb & (1u << b))
                            expected = (expected & ~(255u << (b*8))) | (d.lower_wdata & (255u << (b*8)));
                        edge(d); reset(d);
                        require(d.probe_word == expected, "accepted store did not survive global reset");
                        ++preserved_stores; found = true; break;
                    }
                    edge(d);
                }
                require(found, "no accepted store observed");
            }
            require(aborted_stores == 8 && aborted_reads == 16 && preserved_stores == 24, "reset coverage incomplete");
            std::cout << "PASS: real-core reset seed=" << seed << " aborted_stores=" << aborted_stores
                      << " aborted_reads=" << aborted_reads << " preserved_stores=" << preserved_stores << '\n';
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
