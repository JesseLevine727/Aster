#include "verilated.h"
#include "../common/linux_bus.h"
#include "../common/parallel_record.h"
#include <fstream>
#include <iostream>

static uint64_t retired(Bus& bus, unsigned hart) {
    const unsigned base = 0x30 + hart*8;
    for (unsigned i = 0; i != 10; ++i) {
        const uint32_t hi = bus.read(base+4), lo = bus.read(base), again = bus.read(base+4);
        if (hi == again) return (uint64_t(hi) << 32) | lo;
    }
    throw std::runtime_error("unstable retirement counter read");
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        require(argc == 3 || argc == 5, "usage: simulator firmware.hex runtime|parallel [jobs workers]");
        const std::string kind(argv[2]);
        require(kind == "runtime" || kind == "parallel", "unknown firmware kind");
        const unsigned jobs = kind == "parallel" && argc == 5 ? std::stoul(argv[3]) : 1;
        const unsigned workers = kind == "parallel" && argc == 5 ? std::stoul(argv[4]) : 2;
        require(jobs >= 1 && jobs <= 16 && workers >= 1 && workers <= 2, "bad job/worker count");
        std::ifstream input(argv[1]);
        require(input.good(), "missing firmware");
        std::vector<uint32_t> image;
        std::string word;
        while (input >> word) image.push_back(std::stoul(word, nullptr, 16));
        require(image.size() == 16384, "expected full 64 KiB ROM");
        Bus bus;
        bus.reset();
        // Reset partially received AXI writes. Neither channel may survive to
        // combine with a later request and silently start the RISC-V cluster.
        for (bool address : {true, false}) {
            bus.dut.s_axi_awaddr = 0;
            bus.dut.s_axi_awvalid = address;
            bus.dut.s_axi_wdata = 1; bus.dut.s_axi_wstrb = 15;
            bus.dut.s_axi_wvalid = !address;
            bus.step(); bus.reset();
        }
        require(bus.read(0x18) == 0x41535452 && bus.read(0x1c) == 0x00050001, "wrong multicore bridge ABI");
        require(bus.read(0x24) == 2 && bus.read(0x28) == 0, "wrong hart configuration/status");
        require(retired(bus, 0) == 0 && retired(bus, 1) == 0, "retirement counters not reset");
        bus.read(0x2c, 2);
        for (unsigned offset : {0x24u, 0x28u, 0x30u, 0x34u, 0x38u, 0x3cu}) bus.write(offset, 1, 1, 15, 2);
        bus.write(0, 1, 2, 14);
        require(bus.read(0) == 0, "upper-byte CONTROL store started cluster");
        for (unsigned i = 0; i != image.size(); ++i) bus.write(0x10000 + i*4, image[i], i%3);
        bus.write(0x10000, image[0] ^ 0xffffff00u, 2, 1);
        bus.write(0x10001, 0, 0, 15, 2); // reject unaligned programming
        for (unsigned boot = 0; boot != 2; ++boot) {
            bus.write(0, 1);
            bus.write(0x10000, 0, 0, 15, 2);
            bus.idle(500000); // fill real serial RX and stall writers behind credits
            require((bus.read(4) & 0x1a) == 0, "hart trap/serial error during host pause");
            if (kind == "parallel") require(bus.read(0xc) >= 60, "RX backpressure not exercised");
            std::string serial, line;
            unsigned records = 0;
            for (unsigned polls = 0; polls != 5000000 && records < jobs; ++polls) {
                const auto count = bus.read(0xc);
                for (unsigned i = 0; i != count; ++i) {
                    const auto value = bus.read(8);
                    require(value & 0x80000000u, "FIFO count/pop mismatch");
                    serial += char(value & 255); line += char(value & 255);
                    require(line.size() <= 4096 && serial.size() <= 65536, "oversized serial stream");
                    if (line.back() == '\n') {
                        if (kind == "runtime") require(line == "MULTICORE RUNTIME PASS\n", "runtime failed on AXI shell");
                        else {
                            const auto n = validate_parallel_record(line);
                            require(n.at("job") == records+1 && n.at("jobs") == jobs && n.at("workers") == workers,
                                    "wrong/reordered parallel job");
                            require(n.at("harts") == 2 && n.at("clock_hz") == 400 && n.at("sync_memory") == 1 &&
                                    n.at("l1") == 1 && n.at("line_words") == 4 && n.at("line_count") == 16 && n.at("memory_wait") == 1,
                                    "incorrect executing shell configuration");
                            const auto sums = parallel_reference(n.at("bytes")/4, n.at("rounds"), n.at("seed"), workers);
                            require(n.at("h0_checksum") == sums[0] && n.at("h1_checksum") == sums[1], "serial result differs from host reference");
                        }
                        ++records; line.clear();
                        require(records <= jobs, "extra serial job");
                    }
                }
                require((bus.read(4) & 0x1a) == 0, "hart trap/serial error");
            }
            require(records == jobs && line.empty(), "missing/partial serial jobs");
            bus.idle(10000);
            require(bus.read(0xc) == 0 && bus.read(0x10) == serial.size() && bus.read(0x14) == serial.size(),
                    "trailing/lost/duplicate serial bytes");
            require(bus.read(0x28) == 1, "worker did not finish held in reset");
            const auto r0 = retired(bus, 0), r1 = retired(bus, 1);
            require(r0 > 100 && (workers == 2 ? r1 > 100 : r1 == 0), "missing/unexpected per-hart hardware execution");
            bus.write(0, 0);
            require(bus.read(0x28) == 0 && retired(bus, 0) == 0 && retired(bus, 1) == 0, "global RUN reset not complete");
            std::cout << "PASS: dual Linux AXI/serial " << kind << " boot=" << boot << " jobs=" << jobs
                      << " workers=" << workers << " bytes=" << serial.size() << " h0_retired=" << r0 << " h1_retired=" << r1 << '\n';
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n'; return 1;
    }
}
