#include "Vaster_minimal.h"
#include "verilated.h"

#include <cstdlib>
#include <iostream>
#include <string>

static void tick(Vaster_minimal& dut) {
    dut.clk = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vaster_minimal dut;
    constexpr const char* expected = "RV32IM PASS\n";
    std::string serial_output;

    dut.rst_n = 0;
    for (int cycle = 0; cycle < 3; ++cycle)
        tick(dut);

    dut.rst_n = 1;
    for (int cycle = 0; cycle < 12000; ++cycle) {
        tick(dut);
        if (dut.uart_tx_valid) {
            const char character = static_cast<char>(dut.uart_tx_data);
            serial_output.push_back(character);
            std::cout << character << std::flush;
            if (serial_output.find(expected) != std::string::npos) {
                std::cout << "PASS: directed RV32IM test\n";
                return EXIT_SUCCESS;
            }
            if (serial_output.find("RV32IM FAIL\n") != std::string::npos) {
                std::cerr << "FAIL: directed RV32IM firmware reported failure\n";
                return EXIT_FAILURE;
            }
        }
        if (dut.trap) {
            std::cerr << "\nFAIL: PicoRV32 trapped during directed RV32IM test\n";
            return EXIT_FAILURE;
        }
    }

    std::cerr << "\nFAIL: timed out during directed RV32IM test; received: "
              << serial_output << "\n";
    return EXIT_FAILURE;
}
