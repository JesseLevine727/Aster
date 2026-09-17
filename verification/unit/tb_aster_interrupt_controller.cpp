#include "Vaster_interrupt_controller.h"
#include "verilated.h"

#include <cstdint>
#include <iostream>
#include <stdexcept>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_interrupt_controller dut;
        std::uint32_t pending = 0, enable0 = 0, enable1 = 0, sources_q = 0;

        auto step = [&]() {
            dut.clk = 0; dut.eval();
            if (!dut.rst_n) {
                pending = enable0 = enable1 = sources_q = 0;
            } else {
                const std::uint32_t offset = dut.addr - 0x20004000u;
                const bool lane0 = dut.wstrb & 1;
                const bool wr0 = dut.we && lane0 && offset == 0x00;
                const bool wr1 = dut.we && lane0 && offset == 0x04;
                const bool wrp = dut.we && lane0 && offset == 0x08;
                const bool wra = dut.we && lane0 && offset == 0x14;
                const std::uint32_t rising = dut.sources & ~sources_q;
                std::uint32_t next = pending | rising;
                if (wra) next |= 1u << 3;
                if (wrp) next &= ~(dut.wdata & 0xfu);
                sources_q = dut.sources;
                pending = next;
                if (wr0) enable0 = dut.wdata & 0xfu;
                if (wr1) enable1 = dut.wdata & 0xfu;
            }
            dut.clk = 1; dut.eval();
        };
        auto write = [&](std::uint32_t offset, std::uint32_t data) {
            dut.addr = 0x20004000u + offset; dut.wdata = data; dut.wstrb = 1; dut.we = 1;
            step();
            dut.we = 0;
        };
        auto read = [&](std::uint32_t offset) {
            dut.addr = 0x20004000u + offset; dut.eval();
            return dut.rdata;
        };
        auto verify = [&]() {
            require(read(0x00) == enable0, "ENABLE0 mismatch");
            require(read(0x04) == enable1, "ENABLE1 mismatch");
            require(read(0x08) == pending, "PENDING mismatch");
            require(read(0x0c) == (pending & enable0), "ACTIVE0 mismatch");
            require(read(0x10) == (pending & enable1), "ACTIVE1 mismatch");
            require(read(0x18) == 1 && read(0x1c) == 4, "configuration mismatch");
            require(dut.irq0 == ((pending & enable0) != 0), "irq0 mismatch");
            require(dut.irq1 == ((pending & enable1) != 0), "irq1 mismatch");
        };

        dut.rst_n = 0; dut.we = 0; dut.wstrb = 0; dut.sources = 0;
        step(); verify();
        dut.rst_n = 1;
        for (unsigned i = 0; i < 5; ++i) { step(); verify(); }

        // Rising-edge capture, level routing to hart 0 only, survives deassert.
        write(0x00, 0x1);
        dut.sources = 0x1; step(); verify();
        require(pending == 0x1, "rising edge not captured");
        require(dut.irq0 && !dut.irq1, "source did not route to hart 0 only");
        dut.sources = 0; step(); verify();
        require(pending == 0x1, "pending did not survive source deassertion");

        // W1C: a zero write leaves pending, a one write clears it.
        write(0x08, 0x0); require(pending == 0x1, "zero W1C cleared pending");
        write(0x08, 0x1); require(pending == 0x0, "W1C failed"); verify();

        // Re-route the same source to hart 1.
        write(0x00, 0x0);
        write(0x04, 0x1);
        dut.sources = 0x1; step(); verify();
        require(dut.irq1 && !dut.irq0, "source did not route to hart 1 only");
        write(0x08, 0x1);

        // Independent per-hart masks over two sources.
        write(0x00, 0x1);   // hart 0: timer
        write(0x04, 0x2);   // hart 1: DMA
        dut.sources = 0x0; step();
        dut.sources = 0x1; step(); verify();
        require(dut.irq0 && !dut.irq1, "hart 0 timer routing failed");
        dut.sources = 0x3; step(); verify();
        require(dut.irq0 && dut.irq1, "independent masks failed");
        write(0x08, 0x3);
        require(pending == 0, "W1C of two bits failed"); verify();

        // Software source via RAISE.
        write(0x14, 0x8); require(pending == 0x8, "RAISE did not set the software bit");
        write(0x00, 0x8); verify();
        require(dut.irq0, "software source did not route");
        write(0x08, 0x8); require(pending == 0, "software W1C failed"); verify();

        // A clear in the same cycle as a new edge wins.
        dut.sources = 0x0; step();
        dut.sources = 0x1;
        write(0x08, 0x1);
        require(pending == 0, "clear did not win over a simultaneous edge");

        // Byte lane 0 only: a strobe on a higher lane is ignored.
        write(0x00, 0x0);
        dut.addr = 0x20004000u; dut.wdata = 0x1; dut.wstrb = 0x2; dut.we = 1;
        step();
        dut.we = 0;
        require(enable0 == 0, "non-zero byte lane modified ENABLE0");

        // Reset clears everything.
        dut.rst_n = 0; step(); verify();
        require(pending == 0 && enable0 == 0 && enable1 == 0, "reset state wrong");
        std::cout << "PASS: interrupt controller edge capture, W1C, RAISE, per-hart masks, reset\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
