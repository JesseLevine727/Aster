#include "Vaster_minimal.h"
#include "verilated.h"

#include <iostream>
#include <stdexcept>
#include <string>

// Minimal-SoC harness for the generic AsterBench v10 workload record. It runs
// the ROM to completion and prints the single serial record; the strict
// validator runs on the host via scripts/asterbench_v10.py.

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        unsigned max_cycles = 200000000;
        for (int i = 1; i < argc; ++i) {
            if (std::string(argv[i]) == "--max-cycles" && i + 1 < argc)
                max_cycles = std::stoul(argv[++i]);
        }
        Vaster_minimal dut;
        dut.uart_tx_ready = 1;
        dut.boot_we = dut.boot_addr = dut.boot_wdata = dut.boot_wstrb = 0;
        auto tick = [&]() { dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval(); };
        dut.rst_n = 0;
        for (unsigned i = 0; i < 8; ++i) tick();
        dut.rst_n = 1;
        std::string serial;
        bool done = false;
        unsigned completed = 0;
        for (unsigned cycle = 0; cycle < max_cycles; ++cycle) {
            tick();
            if (dut.trap) throw std::runtime_error("PicoRV32 trapped during workload");
            if (dut.uart_tx_valid) {
                serial += static_cast<char>(dut.uart_tx_data);
                if (serial.size() > 65536) throw std::runtime_error("oversized workload record");
                if (serial.back() == '\n' && !done) { done = true; completed = cycle; }
            }
            if (done && cycle > completed + 1000) {
                std::cout << serial;
                return 0;
            }
        }
        throw std::runtime_error("workload serial timeout");
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
