#include "Vaster_pynq_linux.h"
#include "verilated.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Bus {
    Vaster_pynq_linux dut;
    struct Edge {
        bool aw, w, b, ar, r;
        unsigned bresp, rresp;
        std::uint32_t data;
    };
    Edge step() {
        dut.aclk = 0; dut.eval();
        Edge e{bool(dut.s_axi_awready), bool(dut.s_axi_wready), bool(dut.s_axi_bvalid),
               bool(dut.s_axi_arready), bool(dut.s_axi_rvalid), dut.s_axi_bresp,
               dut.s_axi_rresp, dut.s_axi_rdata};
        dut.aclk = 1; dut.eval();
        return e;
    }
    void idle(unsigned cycles) {
        for (unsigned i = 0; i < cycles; ++i) step();
    }
    void reset() {
        dut.aresetn = 0;
        dut.s_axi_awvalid = dut.s_axi_wvalid = dut.s_axi_bready = 0;
        dut.s_axi_arvalid = dut.s_axi_rready = 0;
        idle(8);
        dut.aresetn = 1;
        idle(4);
    }
    void write(std::uint32_t address, std::uint32_t data, unsigned order = 0,
               unsigned strobes = 15, unsigned response = 0) {
        bool aw_done = false, w_done = false, held = false;
        dut.s_axi_awaddr = address;
        dut.s_axi_wdata = data;
        dut.s_axi_wstrb = strobes;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            dut.s_axi_awvalid = !aw_done && cycle >= (order == 1 ? 4u : 0u);
            dut.s_axi_wvalid = !w_done && cycle >= (order == 2 ? 4u : 0u);
            dut.s_axi_bready = cycle >= 10;
            Edge e = step();
            if (dut.s_axi_awvalid && e.aw) aw_done = true;
            if (dut.s_axi_wvalid && e.w) w_done = true;
            if (held) require(e.b && e.bresp == response, "write response changed under backpressure");
            if (e.b) {
                require(aw_done && w_done && e.bresp == response, "bad AXI write response");
                held = true;
                if (dut.s_axi_bready) {
                    dut.s_axi_awvalid = dut.s_axi_wvalid = dut.s_axi_bready = 0;
                    return;
                }
            }
        }
        throw std::runtime_error("AXI write timeout");
    }
    std::uint32_t read(std::uint32_t address, unsigned response = 0) {
        bool issued = false, held = false;
        std::uint32_t saved = 0;
        dut.s_axi_araddr = address;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            dut.s_axi_arvalid = !issued;
            dut.s_axi_rready = cycle >= 6;
            Edge e = step();
            if (dut.s_axi_arvalid && e.ar) issued = true;
            if (held) require(e.r && e.data == saved && e.rresp == response,
                              "read response changed under backpressure");
            if (e.r) {
                require(issued && e.rresp == response, "bad AXI read response");
                held = true;
                saved = e.data;
                if (dut.s_axi_rready) {
                    dut.s_axi_arvalid = dut.s_axi_rready = 0;
                    return e.data;
                }
            }
        }
        throw std::runtime_error("AXI read timeout");
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        require(argc == 3, "usage: simulator image.hex hello|stress|bench");
        std::ifstream input(argv[1]);
        require(input.good(), "missing firmware");
        std::vector<std::uint32_t> image;
        std::string word;
        while (input >> word) image.push_back(std::stoul(word, nullptr, 16));
        require(image.size() == 16384, "expected full 64 KiB image");
        const std::string kind(argv[2]);
        std::string expected = "Hello from Aster\n";
        if (kind == "stress") {
            expected = "UART STRESS BEGIN\n";
            for (unsigned i = 0; i < 1024; ++i) expected += static_cast<char>('!' + i % 90);
            expected += "\nUART STRESS PASS\n";
        }
        Bus bus;
        bus.reset();
        // Abort an address-only transaction with reset; it must not consume
        // the data channel of the first post-reset request.
        bus.dut.s_axi_awaddr = 0;
        bus.dut.s_axi_awvalid = 1;
        bus.step();
        bus.reset();
        require(bus.read(0x18) == 0x41535452, "wrong bridge identity");
        require(bus.read(0) == 0 && bus.read(8) == 0, "reset state wrong");
        bus.read(0x24, 2);
        bus.write(0x24, 1, 1, 15, 2);
        bus.write(0, 1, 2, 0); // disabled byte strobes must not start the CPU
        require(bus.read(0) == 0, "CONTROL ignored byte strobes");
        for (unsigned i = 0; i < image.size(); ++i) bus.write(0x10000 + 4*i, image[i], i % 3);
        // A partial programming write must preserve the other byte lanes.
        bus.write(0x10000, image[0] ^ 0xffffff00u, 2, 1);
        for (unsigned boot = 0; boot < 2; ++boot) {
            bus.write(0, 1);
            bus.write(0x10000, 0, 0, 15, 2); // ROM programming is locked while running
            bus.idle(100000); // host deliberately stops draining the serial FIFO
            require((bus.read(4) & 0x1a) == 0, "CPU trap or serial error during host pause");
            if (kind == "stress") require(bus.read(12) >= 60, "host pause did not exercise RX credits");
            std::string output;
            bool done = false;
            for (unsigned polls = 0; polls < 150000; ++polls) {
                if (bus.read(12)) {
                    const auto value = bus.read(8);
                    require(value & 0x80000000u, "empty FIFO pop despite count");
                    output += static_cast<char>(value & 255);
                    done = kind == "bench" ? output.find('\n') != std::string::npos
                                           : output.size() == expected.size();
                    if (done) break;
                }
            }
            require(done, "firmware output timeout");
            if (kind == "bench") {
                require(output.rfind("ASTERBENCH,", 0) == 0 &&
                        output.find(",status=PASS,") != std::string::npos &&
                        output.find(",accelerator_cycles=0x0000000000000000\n") != std::string::npos,
                        "incomplete benchmark serial record");
            } else require(output == expected, "serial output mismatch");
            bus.idle(1000);
            require(bus.read(12) == 0, "duplicate serial output");
            require(bus.read(0x10) == output.size() && bus.read(0x14) == output.size(),
                    "serial byte counts disagree");
            require((bus.read(4) & 0x1a) == 0, "CPU trap or serial error");
            bus.write(0, 0);
            std::cout << "PASS: Linux AXI boot + UART serial capture (" << output.size() << " bytes)\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
