#include "Vaster_l2_cache.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_l2_cache dut;
        std::map<std::uint32_t, std::uint32_t> memory;

        auto tick = [&]() {
            dut.clk = 0; dut.eval();
            if (dut.m_valid) {
                if (dut.m_wstrb) {
                    memory[dut.m_addr] = merge(memory.count(dut.m_addr) ? memory[dut.m_addr] : 0,
                                               dut.m_wdata, dut.m_wstrb);
                } else {
                    dut.m_rdata = memory.count(dut.m_addr) ? memory[dut.m_addr] : 0;
                }
                dut.m_ready = 1;
            } else {
                dut.m_ready = 0;
            }
            dut.eval();
            dut.clk = 1; dut.eval();
        };
        auto access = [&](std::uint32_t addr, std::uint32_t wdata = 0, std::uint32_t wstrb = 0) {
            dut.s_valid = 1; dut.s_addr = addr; dut.s_wdata = wdata; dut.s_wstrb = wstrb;
            dut.s_instr = 0; dut.s_device = 0;
            for (unsigned cycle = 0; cycle < 1000; ++cycle) {
                dut.clk = 0; dut.eval();
                if (dut.m_valid) {
                    if (dut.m_wstrb)
                        memory[dut.m_addr] = merge(memory.count(dut.m_addr) ? memory[dut.m_addr] : 0,
                                                   dut.m_wdata, dut.m_wstrb);
                    else
                        dut.m_rdata = memory.count(dut.m_addr) ? memory[dut.m_addr] : 0;
                    dut.m_ready = 1;
                } else {
                    dut.m_ready = 0;
                }
                dut.eval();
                if (dut.s_ready) {
                    std::uint32_t value = dut.s_rdata;
                    dut.clk = 1; dut.eval();
                    dut.s_valid = 0; dut.eval();
                    return value;
                }
                dut.clk = 1; dut.eval();
            }
            throw std::runtime_error("L2 access timed out");
        };

        dut.resetn = 0; dut.s_valid = 0; dut.s_addr = 0; dut.s_wdata = 0; dut.s_wstrb = 0;
        dut.s_instr = 0; dut.s_device = 0; dut.m_ready = 0; dut.m_rdata = 0;
        for (unsigned i = 0; i < 4; ++i) tick();
        dut.resetn = 1;
        tick();

        // Non-RAM (MMIO) passes through combinationally.
        memory[0x2000'0000] = 0x1234'5678;
        require(access(0x2000'0000) == 0x1234'5678, "MMIO bypass failed");
        require(memory.count(0x1000'0000) == 0, "bypass wrote RAM");

        // Read miss refills a whole line, then hits.
        for (int i = 0; i < 4; ++i) memory[0x1000'0010 + 4*i] = 0xa000'0000u + i;
        require(access(0x1000'0010) == 0xa000'0000u, "refill word 0 wrong");
        require(access(0x1000'0014) == 0xa000'0001u, "read hit word 1 wrong");
        require(access(0x1000'001c) == 0xa000'0003u, "read hit word 3 wrong");

        // Write-through updates memory and the cached line.
        access(0x1000'0014, 0xdead'beef, 0xf);
        require(memory[0x1000'0014] == 0xdead'beef, "write-through did not reach memory");
        require(access(0x1000'0014) == 0xdead'beef, "cached line not updated");

        // Byte-strobed write-through.
        access(0x1000'0010, 0x0000'00aa, 0x1);
        require(memory[0x1000'0010] == 0xa000'00aa, "byte write-through wrong");
        require(access(0x1000'0010) == 0xa000'00aa, "cached byte update wrong");

        // Write miss does not allocate; the next read refills.
        memory[0x1000'0100] = 0x1111'1111;
        access(0x1000'0100, 0x2222'2222, 0xf);
        require(memory[0x1000'0100] == 0x2222'2222, "write miss did not reach memory");
        require(access(0x1000'0100) == 0x2222'2222, "read after write miss wrong");

        // Direct-mapped conflict evicts and re-refills.
        for (int i = 0; i < 4; ++i) memory[0x1000'0210 + 4*i] = 0xb000'0000u + i;
        require(access(0x1000'0210) == 0xb000'0000u, "conflict line refill wrong");
        require(access(0x1000'0010) == 0xa000'00aa, "evicted line re-refill wrong");

        // A device write to a cached line updates it.
        access(0x1000'0210, 0x3333'3333, 0xf);
        require(access(0x1000'0210) == 0x3333'3333, "device write update wrong");

        // Reset invalidates all lines: the next read misses and refills.
        dut.resetn = 0;
        for (unsigned i = 0; i < 4; ++i) tick();
        dut.resetn = 1;
        tick();
        require(access(0x1000'0210) == 0x3333'3333, "post-reset refill wrong");

        std::cout << "PASS: L2 read hit/miss/refill, write-through, no-allocate, bypass, conflict, reset\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
