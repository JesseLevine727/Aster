#include "Vaster_uart_tx.h"
#include "verilated.h"
#include "../common/uart_decoder.h"

#include <cstdlib>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vaster_uart_tx dut;
    std::mt19937 rng(0xA57E);
    for (unsigned boot = 0; boot < 3; ++boot) {
        dut.rst_n = 0;
        dut.tx_valid_i = 0;
        for (unsigned i = 0; i < 4; ++i) {
            dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval();
        }
        if (!dut.tx_o || dut.busy_o || dut.ready_o) return EXIT_FAILURE;
        dut.rst_n = 1;
        SerialDecoder decoder(4);
        std::string expected;
        for (unsigned i = 0; i < 512; ++i)
            expected += static_cast<char>(i < 256 ? (i + boot * 17) & 255 : rng() & 255);
        unsigned sent = 0, stalls = 0, completed = 0;
        bool offering = false;
        for (unsigned cycle = 0; cycle < 40000; ++cycle) {
            if (!offering && sent < expected.size() && (rng() & 7) != 0) offering = true;
            dut.tx_valid_i = offering;
            dut.tx_data_i = sent < expected.size() ? static_cast<unsigned char>(expected[sent]) : 0;
            dut.clk = 0; dut.eval();
            if (offering && dut.ready_o) { ++sent; offering = false; }
            else if (offering) ++stalls;
            dut.clk = 1; dut.eval();
            decoder.sample(dut.tx_o);
            // First run is interrupted with a frame and queued bytes active.
            if (boot == 0 && cycle == 200) break;
            if (decoder.text().size() >= expected.size() && completed == 0) completed = cycle;
            if (completed && cycle > completed + 80) break;
        }
        if (boot == 0) continue;
        if (decoder.failed() || decoder.text() != expected || sent != expected.size() ||
            dut.busy_o || stalls == 0) {
            std::cerr << "FAIL: UART FIFO/reset/serial integrity, received="
                      << decoder.text().size() << " sent=" << sent << " stalls=" << stalls << '\n';
            return EXIT_FAILURE;
        }
    }
    std::cout << "PASS: UART FIFO backpressure, wraparound, all byte values and mid-frame reset\n";
    return EXIT_SUCCESS;
}
