#include "Vaster_coherent_soc.h"
#include "verilated.h"

#include <iostream>
#include <stdexcept>
#include <string>

// Coherent-SoC harness for generic AsterBench v10 records (parallel reduction).
// Prints the single serial record; scripts/asterbench_v10.py validates it.

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_coherent_soc d;
        d.resetn = 0; d.host_run = 0; d.boot_we = 0; d.boot_addr = 0;
        d.boot_wdata = 0; d.boot_wstrb = 0; d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        auto tick = [&]() { d.clk = 0; d.eval(); d.clk = 1; d.eval(); };
        for (unsigned i = 0; i < 8; ++i) tick();
        d.resetn = 1; tick();
        if (!d.stopped) throw std::runtime_error("coherent workload did not reach STOPPED");
        d.host_run = 1;
        std::string serial;
        bool done = false;
        unsigned completed = 0;
        for (unsigned cycle = 0; cycle < 200000000u; ++cycle) {
            tick();
            if (d.hart_trap || d.fault_valid) throw std::runtime_error("coherent workload trapped");
            if (d.uart_tx_valid && d.uart_tx_ready) {
                serial += static_cast<char>(d.uart_tx_data);
                if (serial.size() > 65536) throw std::runtime_error("oversized workload record");
                if (serial.back() == '\n' && !done) { done = true; completed = cycle; }
            }
            if (done && cycle > completed + 1000) { std::cout << serial; return 0; }
        }
        throw std::runtime_error("coherent workload serial timeout");
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
