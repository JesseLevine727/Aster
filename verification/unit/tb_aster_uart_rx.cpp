#include "Vaster_uart_rx.h"
#include "verilated.h"

#include <iostream>
#include <stdexcept>
#include <string>

struct Receiver {
    Vaster_uart_rx dut;
    std::string bytes;
    unsigned errors = 0;
    void drive(bool level, unsigned cycles) {
        dut.rx_i = level;
        for (unsigned i = 0; i < cycles; ++i) {
            dut.clk = 0; dut.eval();
            dut.clk = 1; dut.eval();
            if (dut.valid_o) bytes += static_cast<char>(dut.data_o);
            if (dut.framing_error_o) ++errors;
        }
    }
    void reset() {
        dut.rst_n = 0;
        drive(true, 8);
        dut.rst_n = 1;
        drive(true, 40);
    }
    void frame(unsigned byte, bool stop = true) {
        drive(false, 40);
        for (unsigned bit = 0; bit < 8; ++bit) drive((byte >> bit) & 1, 40);
        drive(stop, 40);
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Receiver receiver;
    receiver.reset();
    // A start glitch shorter than half a bit must be rejected.
    receiver.drive(false, 4);
    receiver.drive(true, 500);
    if (!receiver.bytes.empty() || receiver.errors) return 1;
    // Full low stop bit raises exactly one error and never a valid byte.
    receiver.frame(0xa5, false);
    receiver.drive(true, 500);
    if (!receiver.bytes.empty() || receiver.errors != 1) return 1;
    // Reset while receiving data aborts the partial frame cleanly.
    receiver.drive(false, 140);
    receiver.reset();
    std::string expected;
    for (unsigned value = 0; value < 256; ++value) {
        expected += static_cast<char>(value);
        receiver.frame(value);
    }
    receiver.drive(true, 100);
    if (receiver.bytes != expected || receiver.errors != 1) {
        std::cerr << "FAIL: UART RX framing/glitch/reset/all-byte test\n";
        return 1;
    }
    std::cout << "PASS: UART RX false starts, framing errors, reset and all 256 bytes\n";
    return 0;
}
