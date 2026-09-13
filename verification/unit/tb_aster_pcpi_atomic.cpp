#include "Vaster_pcpi_atomic.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
#include <stdexcept>

static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
static void tick(Vaster_pcpi_atomic& d) {
    d.clk = 0; d.eval(); d.clk = 1; d.eval();
}
static void reset(Vaster_pcpi_atomic& d) {
    d.resetn = 0; d.pcpi_valid = d.cmd_ready = d.cmd_fault = 0;
    tick(d);
    require(!d.cmd_valid && !d.pcpi_ready && !d.pcpi_wait && !d.fault_valid && !d.busy,
            "reset retained adapter transaction");
    d.resetn = 1;
}
static bool legal_op(unsigned op) {
    switch (op) {
        case 0: case 1: case 2: case 3: case 4: case 8: case 12:
        case 16: case 20: case 24: case 28: return true;
        default: return false;
    }
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_pcpi_atomic d;
        unsigned decoded = 0, completed = 0;
        // Exhaustive opcode/funct3/funct5 decode; malformed LR encoded rs2
        // receives separate exhaustive coverage in the real-core probe.
        for (unsigned opcode = 0; opcode < 128; ++opcode)
        for (unsigned width = 0; width < 8; ++width)
        for (unsigned op = 0; op < 32; ++op) {
            reset(d);
            d.pcpi_insn = (op << 27) | (width << 12) | opcode;
            d.pcpi_rs1 = 0x10000040; d.pcpi_rs2 = 0;
            d.pcpi_valid = 1; d.eval();
            const bool legal = opcode == 0x2f && width == 2 && legal_op(op);
            require(bool(d.pcpi_wait) == legal, "incorrect immediate PCPI claim");
            tick(d);
            require(bool(d.cmd_valid) == legal && !d.pcpi_ready && !d.fault_valid,
                    "illegal encoding claimed / legal command lost");
            ++decoded;
        }
        for (unsigned op = 0; op < 32; ++op) if (legal_op(op))
        for (unsigned order = 0; order < 4; ++order) {
            reset(d);
            d.pcpi_insn = (op << 27) | (order << 25) | (2 << 12) | 0x2f;
            d.pcpi_rs1 = 0x10008000; d.pcpi_rs2 = op == 2 ? 0 : 0xf00d1234;
            const auto operand = d.pcpi_rs2;
            d.pcpi_valid = 1; tick(d);
            // Payload must come from the capture, not changing input wires.
            d.pcpi_insn = 0xffffffff; d.pcpi_rs1 = 3; d.pcpi_rs2 = 5;
            for (unsigned i = 0; i < 80; ++i) {
                tick(d);
                require(d.cmd_valid && d.pcpi_wait && !d.pcpi_ready &&
                        d.cmd_addr == 0x10008000 && d.cmd_operand == operand &&
                        d.cmd_op == op && d.cmd_order == order, "stalled captured payload unstable");
            }
            d.cmd_ready = 1; d.cmd_result = 0xbaad8765; tick(d);
            for (unsigned i = 0; i < 40; ++i) {
                require(d.pcpi_ready && d.pcpi_wr && d.pcpi_rd == 0xbaad8765 &&
                        !d.cmd_valid && !d.pcpi_wait && !d.fault_valid,
                        "held PCPI valid duplicated command or changed result");
                d.cmd_result ^= 0xffffffff; tick(d);
            }
            d.pcpi_valid = 0; d.eval();
            require(!d.pcpi_ready && !d.pcpi_wr, "response not gated by PCPI valid");
            tick(d); require(!d.busy, "completed command did not return idle");
            ++completed;
        }
        std::cout << "PASS: PCPI adapter: " << decoded << " decode combinations, "
                  << completed << " stalled/held-response transactions\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n'; return 1;
    }
}
