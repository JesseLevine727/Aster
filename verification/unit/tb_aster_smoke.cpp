#include "Vaster_smoke.h"
#include "verilated.h"

#include <cstdlib>
#include <iostream>

static void tick(Vaster_smoke& dut) {
    dut.clk = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vaster_smoke dut;

    dut.rst_n = 0;
    tick(dut);
    if (dut.counter != 0 || dut.heartbeat != 0) {
        std::cerr << "FAIL: reset state is incorrect\n";
        return EXIT_FAILURE;
    }

    dut.rst_n = 1;
    for (int cycle = 1; cycle <= 16; ++cycle) {
        tick(dut);
        if (dut.counter != cycle) {
            std::cerr << "FAIL: expected counter " << cycle
                      << ", got " << static_cast<int>(dut.counter) << "\n";
            return EXIT_FAILURE;
        }
    }

    std::cout << "PASS: Aster Verilator smoke test (16 cycles)\n";
    return EXIT_SUCCESS;
}
