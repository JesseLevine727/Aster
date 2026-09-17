#include "Vaster_timer.h"
#include "Vaster_timer___024root.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_timer dut;
        std::uint64_t time_value = 0, compare = 0;
        bool enabled = false, pending = false;

        auto step = [&]() {
            dut.clk = 0; dut.eval();
            if (!dut.rst_n) {
                time_value = 0; compare = 0; enabled = false; pending = false;
            } else {
                const std::uint32_t offset = dut.addr - 0x20001000u;
                const bool write_lo = dut.we && offset == 0x08;
                const bool write_hi = dut.we && offset == 0x0c;
                const bool command = dut.we && (dut.wstrb & 1) && offset == 0x10;
                const bool match = enabled && (time_value + 1) == compare;
                for (int b = 0; b < 4; ++b) {
                    if (!(dut.wstrb & (1u << b))) continue;
                    const std::uint64_t byte = (dut.wdata >> (8 * b)) & 0xffu;
                    if (write_lo) compare = (compare & ~(0xffull << (8 * b))) | (byte << (8 * b));
                    if (write_hi) compare = (compare & ~(0xffull << (32 + 8 * b))) | (byte << (32 + 8 * b));
                }
                time_value += 1;
                if (match) pending = true;
                if (command) {
                    enabled = dut.wdata & 1u;
                    if (dut.wdata & 2u) pending = false;
                }
            }
            dut.clk = 1; dut.eval();
        };
        auto write = [&](std::uint32_t offset, std::uint32_t data, std::uint32_t strobe) {
            dut.addr = 0x20001000u + offset; dut.wdata = data; dut.wstrb = strobe; dut.we = 1;
            step();
            dut.we = 0;
        };
        auto read = [&](std::uint32_t offset) {
            dut.addr = 0x20001000u + offset; dut.eval();
            return dut.rdata;
        };
        auto verify = [&]() {
            require(read(0x00) == (std::uint32_t)time_value &&
                    read(0x04) == (std::uint32_t)(time_value >> 32), "TIME mismatch");
            require(read(0x08) == (std::uint32_t)compare &&
                    read(0x0c) == (std::uint32_t)(compare >> 32), "COMPARE mismatch");
            require(read(0x14) == ((enabled ? 2u : 0u) | (pending ? 1u : 0u)), "STATUS mismatch");
            require(dut.timer_irq == pending, "IRQ mismatch");
            require(read(0x18) == 1 && read(0x1c) == 31250000u, "configuration mismatch");
        };

        dut.rst_n = 0; dut.we = 0; dut.wstrb = 0;
        step(); verify();
        dut.rst_n = 1;
        for (unsigned i = 0; i < 25; ++i) { step(); verify(); }

        // Program compare = now + 40, enable, and check the exact match cycle.
        write(0x08, (std::uint32_t)(time_value + 40), 0xf);
        write(0x0c, (std::uint32_t)((time_value + 40) >> 32), 0xf);
        write(0x10, 1, 1);
        verify();
        while (!pending) { step(); verify(); }
        require(time_value == compare, "pending did not coincide with TIME == COMPARE");
        write(0x10, 3, 1);  // enable + clear
        require(!pending, "clear did not drop pending");
        for (unsigned i = 0; i < 10; ++i) { step(); verify(); }
        require(!pending, "pending re-asserted without a new match");

        // Disable, then arm a past compare and confirm no match.
        write(0x10, 0, 1);
        write(0x08, (std::uint32_t)(time_value - 5), 0xf);
        write(0x0c, 0, 0xf);
        write(0x10, 1, 1);
        for (unsigned i = 0; i < 10; ++i) { step(); verify(); }
        require(!pending, "past compare matched");

        // Byte-strobed compare merge.
        write(0x08, 0x00000011, 0x1);
        write(0x08, 0x00002200, 0x2);
        require((compare & 0xffffu) == 0x2211u, "byte merge failed");

        // 64-bit wrap and match after wrap via storage introspection.
        time_value = UINT64_MAX - 4;
        write(0x08, 3, 0xf);
        write(0x0c, 0, 0xf);
        dut.rootp->aster_timer__DOT__time_counter = time_value;
        write(0x10, 1, 1);
        for (unsigned i = 0; i < 10; ++i) { step(); verify(); }
        require(time_value < 10, "counter did not wrap");
        require(pending, "compare after wrap did not match");

        dut.rst_n = 0; step(); verify();
        require(time_value == 0 && compare == 0 && !enabled && !pending, "reset state wrong");
        std::cout << "PASS: timer free-run, compare match, clear/enable, byte merge, 64-bit wrap and reset\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
