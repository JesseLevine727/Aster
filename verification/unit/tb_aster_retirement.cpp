#include "Vaster_picorv32.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_picorv32 dut;
        dut.pcpi_wr = dut.pcpi_rd = dut.pcpi_wait = dut.pcpi_ready = dut.irq = 0;
        // Taken branches/jumps skip fetched instructions: fetch counting is not
        // retirement. MUL/DIV and stalled loads/stores each retire exactly once.
        std::array<std::uint32_t, 18> code = {
            0x00700093, 0x00300113, 0x022081b3, 0x0221c233, // addi, addi, mul, div
            0x100002b7, 0x0042a023, 0x0002a303, 0x00130463, // lui, sw, lw, beq +8
            0xffffffff, 0x00200393, 0xfff38393, 0xfe039ee3, // skipped, addi, addi, bne -4
            0x0080046f, 0xffffffff, 0x00c40067, 0xffffffff, // jal +8, skipped, jalr, skipped
            0x0000000f, 0xffffffff // fence, selected trap
        };
        const std::vector<unsigned> expected = {
            0, 4, 8, 12, 16, 20, 24, 28, 36, 40, 44, 40, 44, 48, 56, 64
        };
        const std::array<std::uint32_t, 6> faults = {
            0xffffffff, 0x00000073, 0x00100073, // illegal, ECALL, EBREAK
            0x0012a303, 0x0042a0a3, 0x0020006f // misaligned LW, SW, JAL
        };
        std::mt19937 random(0xa57e);
        for (auto fault : faults) for (unsigned boot = 0; boot < 2; ++boot) {
            code.back() = fault;
            dut.resetn = 0;
            dut.mem_ready = dut.mem_rdata = 0;
            for (unsigned i = 0; i < 8; ++i) {
                dut.clk = 0; dut.eval(); dut.clk = 1; dut.eval();
                require(!dut.instr_retired, "retirement during reset");
            }
            dut.resetn = 1;
            std::uint32_t ram = 0;
            unsigned remaining = 0, trapped_cycles = 0;
            bool pending = false;
            std::uint32_t address = 0, data = 0, mask = 0;
            std::vector<unsigned> observed;
            for (unsigned cycle = 0; cycle < 20000; ++cycle) {
                dut.clk = 0; dut.mem_ready = 0; dut.eval();
                if (dut.mem_valid) {
                    if (!pending) {
                        pending = true;
                        address = dut.mem_addr; data = dut.mem_wdata; mask = dut.mem_wstrb;
                        remaining = random() % 8;
                    } else require(address == dut.mem_addr && data == dut.mem_wdata &&
                                   mask == dut.mem_wstrb, "native request changed while stalled");
                    if (remaining) --remaining;
                    else {
                        dut.mem_ready = 1;
                        dut.mem_rdata = address < code.size()*4 ? code[address/4]
                                       : address == 0x10000000 ? ram : 0;
                        if (mask) {
                            require(address == 0x10000000, "unexpected/misaligned store reached memory");
                            for (unsigned lane = 0; lane < 4; ++lane) if (mask & (1u << lane))
                                ram = (ram & ~(255u << (lane*8))) | (data & (255u << (lane*8)));
                        }
                        pending = false;
                    }
                } else require(!pending || dut.trap, "native request withdrawn while stalled");
                dut.eval(); dut.clk = 1; dut.eval();
                if (dut.instr_retired) {
                    require(dut.retired_pc < 68, "faulting/skipped instruction retired");
                    require(dut.retired_insn == code[dut.retired_pc/4], "RVFI opcode mismatch");
                    observed.push_back(dut.retired_pc);
                }
                if (dut.trap && ++trapped_cycles == 40) break;
            }
            require(trapped_cycles == 40, "expected persistent trap did not occur");
            require(ram == 7, "arithmetic/store/load fixture failed");
            require(observed == expected, "retirement PC sequence/count mismatch");
        }
        std::cout << "PASS: exact RVFI retirement sequence, stalled memory, six trap types and warm reset\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
