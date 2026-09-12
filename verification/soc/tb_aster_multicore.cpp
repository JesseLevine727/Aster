#include "Vaster_multicore.h"
#include "verilated.h"

#include <array>
#include <cstdlib>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#endif

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        Vaster_multicore d;
        d.uart_tx_ready = 1;
        d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = 0;
        for (unsigned boot = 0; boot != 2; ++boot) {
            d.rst_n = 0;
            for (unsigned c = 0; c != 8; ++c) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
            if (d.hart_trap || d.hart_run || d.retired || d.uart_tx_valid)
                throw std::runtime_error("reset did not clear hart/serial state");
            d.rst_n = 1;
            std::string line;
            std::array<unsigned, 2> retired{};
            std::array<std::set<uint32_t>, 2> pcs;
            bool pass = false;
            for (unsigned cycle = 0; cycle != 2000000; ++cycle) {
                d.clk = 0; d.eval();
                if (d.hart_trap) throw std::runtime_error("unexpected hart trap " + std::to_string(d.hart_trap));
                for (unsigned h = 0; h != 2; ++h) if (d.retired & (1u << h)) {
                    ++retired[h]; pcs[h].insert(d.retired_pc[h]);
                }
                if (d.uart_tx_valid && d.uart_tx_ready) {
                    if (d.uart_tx_data == '\n') {
                        std::cout << line << '\n';
                        if (line != "MULTICORE RUNTIME PASS") throw std::runtime_error("unexpected firmware result");
                        pass = true;
                    } else line += char(d.uart_tx_data);
                    if (line.size() > 256) throw std::runtime_error("unterminated UART line");
                }
                d.clk = 1; d.eval();
                if (pass) break;
            }
            if (!pass) throw std::runtime_error("timeout waiting for dual-hart runtime");
            for (unsigned h = 0; h != 2; ++h) {
                if (h < ASTER_HART_COUNT ? retired[h] < 200 || pcs[h].size() < 40 : retired[h] != 0)
                    throw std::runtime_error("missing/unexpected independent hardware retirement");
            }
            std::cout << "PASS: runtime boot=" << boot << " hart0_retired=" << retired[0]
                      << " hart1_retired=" << retired[1] << " hart0_pcs=" << pcs[0].size()
                      << " hart1_pcs=" << pcs[1].size() << '\n';
        }
        return EXIT_SUCCESS;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n';
        return EXIT_FAILURE;
    }
}
