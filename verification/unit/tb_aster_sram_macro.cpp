#include "Vaster_sram_macro.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_sram_macro dut;
        const std::uint32_t base = 0x10000000u;
        std::map<std::uint32_t, std::uint32_t> memory;

        auto tick = [&]() {
            dut.clk = 0; dut.eval();
            dut.clk = 1; dut.eval();
        };
        auto write = [&](std::uint32_t addr, std::uint32_t data, unsigned mask) {
            dut.addr = addr; dut.wdata = data; dut.wstrb = mask; dut.we = 1;
            tick();
            dut.we = 0; dut.eval();
            for (unsigned b = 0; b < 4; ++b)
                if (mask & (1u << b)) {
                    std::uint32_t& word = memory[addr & ~3u];
                    word = (word & ~(255u << (8*b))) | (data & (255u << (8*b)));
                }
        };
        auto read = [&](std::uint32_t addr) {
            dut.addr = addr; dut.we = 0;
            tick();
            return dut.rdata;
        };

        write(base + 0, 0x11223344u, 0xf);
        require(read(base + 0) == 0x11223344u, "full-word readback");
        write(base + 4, 0xaabbccddu, 0xf);
        require(read(base + 4) == 0xaabbccddu, "second-word readback");

        write(base + 0, 0x00ee0000u, 0b0100);
        require(read(base + 0) == 0x11ee3344u, "byte-strobe merge");

        write(base + 511 * 4, 0xdeadbeefu, 0xf);
        require(read(base + 511 * 4) == 0xdeadbeefu, "last-word readback");
        require(read(base + 0) == 0x11ee3344u, "address aliasing");

        std::cout << "PASS: SRAM macro wrapper full-word, byte-strobe and boundary readback\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
