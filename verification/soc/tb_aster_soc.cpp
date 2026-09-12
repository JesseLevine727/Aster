#include "Vaster_minimal.h"
#include "verilated.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

static void tick(Vaster_minimal& dut) {
    dut.clk = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    std::string expected;
    bool expect_trap = false;
    unsigned boots = 1;
    unsigned max_cycles = 500000;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--expect" && i + 1 < argc) expected = argv[++i];
        else if (arg == "--trap") expect_trap = true;
        else if (arg == "--boots" && i + 1 < argc) boots = std::stoul(argv[++i]);
        else if (arg == "--cycles" && i + 1 < argc) max_cycles = std::stoul(argv[++i]);
        else if (arg.rfind("+", 0) != 0) {
            std::cerr << "Unknown/incomplete argument: " << arg << '\n';
            return EXIT_FAILURE;
        }
    }
    if (expected.empty() || boots == 0) {
        std::cerr << "Usage: +rom=image.hex --expect 'line' [--trap] [--boots N]\n";
        return EXIT_FAILURE;
    }
    Vaster_minimal dut;
    dut.uart_tx_ready = 1;
    dut.boot_we = dut.boot_addr = dut.boot_wdata = dut.boot_wstrb = 0;
    for (unsigned boot = 0; boot < boots; ++boot) {
        dut.rst_n = 0;
        for (unsigned i = 0; i < 8; ++i) tick(dut);
        if (dut.uart_tx_valid || dut.trap) {
            std::cerr << "FAIL: reset did not clear UART/trap\n";
            return EXIT_FAILURE;
        }
        dut.rst_n = 1;
        std::string line;
        bool armed = false;
        bool passed = false;
        for (unsigned cycle = 0; cycle < max_cycles; ++cycle) {
            tick(dut);
            if (dut.uart_tx_valid) {
                const char ch = static_cast<char>(dut.uart_tx_data);
                if (ch == '\n') {
                    std::cout << line << '\n';
                    if (line.rfind("FAIL", 0) == 0 || line.find(" FAIL") != std::string::npos)
                        return EXIT_FAILURE;
                    if (line == expected) armed = true;
                    line.clear();
                } else {
                    line += ch;
                    if (line.size() > 4096) return EXIT_FAILURE;
                }
            }
            if (dut.trap) {
                if (!expect_trap || !armed) {
                    std::cerr << "FAIL: unexpected/early CPU trap at cycle " << cycle << '\n';
                    return EXIT_FAILURE;
                }
                for (unsigned i = 0; i < 8; ++i) {
                    tick(dut);
                    if (!dut.trap || dut.uart_tx_valid) return EXIT_FAILURE;
                }
                passed = true;
                break;
            }
            if (armed && !expect_trap) { passed = true; break; }
        }
        if (!passed) {
            std::cerr << "FAIL: timeout waiting for " << expected << '\n';
            return EXIT_FAILURE;
        }
    }
    std::cout << "PASS: " << expected << " (" << boots << " boot(s))\n";
    return EXIT_SUCCESS;
}
