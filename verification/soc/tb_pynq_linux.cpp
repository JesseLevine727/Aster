#include "Vaster_pynq_linux.h"
#include "verilated.h"
#include "../common/bench_record.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "../common/linux_bus.h"

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
                validate_bench_record(output);
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
