#include "../common/linux_bus.h"
#include "Vaster_pynq_linux___024root.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_L1 1
#endif
static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
static std::uint64_t retired(Bus& b, unsigned h) {
    const unsigned base = 0x30 + h*8;
    for (;;) {
        const auto hi = b.read(base+4), lo = b.read(base);
        if (hi == b.read(base+4)) return std::uint64_t(lo) | (std::uint64_t(hi) << 32);
    }
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        require(argc == 3, "usage: linux_coherent firmware.hex runtime|lifecycle");
        const bool lifecycle = std::string(argv[2]) == "lifecycle";
        require(lifecycle || std::string(argv[2]) == "runtime", "unknown firmware kind");
        std::ifstream file(argv[1]); require(bool(file), "cannot read firmware");
        std::vector<std::uint32_t> image;
        for (std::string word; file >> word;) image.push_back(std::stoul(word, nullptr, 16));
        require(image.size() == 16384, "firmware must fill the 64 KiB boot store");
        Bus b;
        std::array<std::uint32_t, 16384> oracle{};
        unsigned stores = 0, snapshots = 0;
        bool held_read = false;
        std::uint32_t held_read_data = 0;
        unsigned held_read_response = 0;
        b.before_edge = [&]() {
            if (held_read)
                require(b.dut.s_axi_rvalid && b.dut.s_axi_rdata == held_read_data && b.dut.s_axi_rresp == held_read_response,
                        "RAM reply changed under concurrent RUN/backpressure");
            const auto* root = b.dut.rootp;
            if (root->aster_pynq_linux__DOT__coherent_store_commit) {
                const auto addr = root->aster_pynq_linux__DOT__coherent_store_addr;
                require(addr >= 0x10000000 && addr < 0x10010000, "invalid architectural store observation");
                oracle[(addr - 0x10000000)/4] = merge(oracle[(addr - 0x10000000)/4],
                    root->aster_pynq_linux__DOT__coherent_store_data,
                    root->aster_pynq_linux__DOT__coherent_store_mask);
                ++stores;
            }
        };
        auto snapshot = [&]() {
            require(b.read(0x40) == 1 && b.read(0x28) == 0, "RAM snapshot before full stop");
            for (unsigned i = 0; i < oracle.size(); ++i)
                require(b.read(0x20000 + i*4) == oracle[i], "AXI synchronous RAM snapshot lost/changed acknowledged data");
            ++snapshots;
        };
        auto wait_stopped = [&]() {
            bool stopped = false;
            for (unsigned i = 0; i < 100000; ++i) {
                if (b.read(0x40) == 1) { stopped = true; break; }
            }
            require(stopped && b.read(0) == 0 && b.read(4) == 0 && b.read(0x28) == 0,
                    "AXI warm stop failed / serial path reset too early or too late");
        };
        auto stop = [&](bool early_checks) {
            b.write(0, 0);
            if (early_checks) {
                // Fixed bridge geometry scans 32 slots even when clean; these
                // writes arrive during the mandatory stop, not after STOPPED.
                b.write(0, 1, 0, 15, 2);
                b.write(0x10000, 0xffffffff, 1, 15, 2);
                require(b.read(0x20000, 2) == 0, "RAM leaked before STOPPED");
            }
            wait_stopped(); snapshot();
        };
        auto load = [&](const std::vector<std::uint32_t>& code) {
            require(b.read(0x40) == 1, "loader did not wait for safe STOPPED");
            for (unsigned i = 0; i < code.size(); ++i) b.write(0x10000 + i*4, code[i], i % 3);
        };
        b.reset();
        require(b.read(0x18) == 0x41535452 && b.read(0x1c) == 0x00060001 &&
                b.read(0x20) == 400 && b.read(0x24) == ASTER_HART_COUNT &&
                b.read(0x44) == (1u | unsigned(ASTER_L1) << 1), "incorrect coherent bridge identity/configuration");
        require(b.read(0x40) == 1, "initial bridge STOPPED missing");
        b.read(0x20001, 2); b.read(0x30000, 2); b.write(0x20000, 7, 0, 15, 2);
        load(image);
        b.write(0x10000, 0xffffffff, 0, 0); // Zero strobe must not corrupt ROM.
        const std::string expected = lifecycle ? "COHERENT LIFECYCLE PASS\n" :
            "RV32A DIRECTED PASS\nRV32A JOB PASS\nRV32A JOB PASS\nRV32A JOB PASS\n";
        for (unsigned boot = 0; boot < 2; ++boot) {
            b.write(0, 1, boot % 3);
            b.write(0x10000, 0xffffffff, 2, 15, 2); // ROM is not writable while running.
            require(b.read(0x20000, 2) == 0, "running cache/RAM snapshot was accepted");
            b.idle(200000); // Host pause with bounded serial receive buffering.
            std::string output;
            for (unsigned polls = 0; polls < 1000000 && output != expected; ++polls) {
                const auto count = b.read(0x0c);
                for (unsigned i = 0; i < count; ++i) {
                    const auto value = b.read(8);
                    require(value & 0x80000000, "serial count/pop mismatch");
                    output += char(value);
                    require(output.size() <= expected.size() && expected.compare(0, output.size(), output) == 0,
                            "unexpected real-serial coherent firmware output");
                }
                require((b.read(4) & 0x1a) == 0, "real serial/core error");
            }
            require(output == expected, "coherent serial capture timed out");
            b.idle(2048);
            require(b.read(0x0c) == 0 && b.read(0x10) == output.size() && b.read(0x14) == output.size(),
                    "serial count mismatch / trailing bytes");
            const auto h0 = retired(b, 0), h1 = retired(b, 1);
            require(h0 > 100 && (ASTER_HART_COUNT == 2 ? h1 > 100 : h1 == 0), "physical hart retirement missing");
            stop(true);
            if (!lifecycle) for (unsigned i = 1; i <= 4; ++i)
                require(oracle[0x8000/4+i] == 128 * ASTER_HART_COUNT, "independent atomic result mismatch");
            std::cout << "PASS: coherent AXI/serial " << argv[2] << " harts=" << ASTER_HART_COUNT
                      << " cache=" << ASTER_L1 << " boot=" << boot << " bytes=" << output.size()
                      << " retired=" << h0 << "," << h1 << " retained_RAM=65536\n";
        }
        // Fatal atomic diagnostics must be available BEFORE stop clears them.
        load({0x200000b7, 0x1000a12f, 0x0000006f}); // LR.W from UART faults at PC=4.
        b.write(0, 1);
        bool trapped = false;
        for (unsigned i = 0; i < 1000; ++i) if (b.read(4) & 2) { trapped = true; break; }
        require(trapped && b.read(0x50) == 0x15 && b.read(0x54) == 0x20000000 &&
                b.read(0x58) == 0x1000a12f && b.read(0x5c) == 4, "AXI atomic fault identity incorrect");
        stop(false);
        require(b.read(0x50) == 0 && b.read(0x60) == 0, "warm reset retained fault status");
        // Dirty sentinel + infinite UART producer. No host reads while the
        // receive FIFO fills: explicit STOP must discard serial work, not RAM.
        load({0x100000b7, 0x05500113, 0x0020a023, 0x200001b7, 0x04100113, 0x0021a023, 0xffdff06f});
        b.write(0, 1); b.idle(300000);
        require(b.read(0x0c) >= 60 && !(b.read(4) & 0x1a), "UART saturation fixture failed / overflowed");
        stop(true); require(oracle[0] == 0x55, "UART-stop flush lost dirty sentinel");
        load(image);
        // RUN write commits on the same edge as RAM AR admission. The delayed
        // snapshot response must fail closed if RUN changed before capture.
        b.dut.s_axi_awaddr = 0; b.dut.s_axi_wdata = 1; b.dut.s_axi_wstrb = 15;
        b.dut.s_axi_awvalid = b.dut.s_axi_wvalid = 1;
        b.dut.s_axi_bready = b.dut.s_axi_rready = 0;
        auto edge = b.step(); require(edge.aw && edge.w, "concurrent RUN fixture did not latch write");
        b.dut.s_axi_awvalid = b.dut.s_axi_wvalid = 0;
        b.dut.s_axi_araddr = 0x20000; b.dut.s_axi_arvalid = 1;
        edge = b.step(); require(edge.ar, "concurrent RAM fixture not admitted");
        b.dut.s_axi_arvalid = 0; b.step();
        held_read = true; held_read_data = 0; held_read_response = 2;
        b.idle(12);
        b.dut.s_axi_bready = b.dut.s_axi_rready = 1;
        edge = b.step(); require(edge.b && edge.bresp == 0 && edge.r && edge.rresp == 2, "concurrent RUN/RAM responses incorrect");
        held_read = false; b.dut.s_axi_bready = b.dut.s_axi_rready = 0; b.idle(1);
        stop(false);
        // Conversely, once a valid stopped snapshot was captured, a later RUN
        // must not change that held AXI reply while the CPU resumes work.
        const auto replacement = oracle[0] == 0x66 ? 0x77u : 0x66u;
        load({0x100000b7, (replacement << 20) | 0x113, 0x0020a023, 0x0000006f});
        b.dut.s_axi_araddr = 0x20000; b.dut.s_axi_arvalid = 1;
        edge = b.step(); require(edge.ar, "held snapshot fixture not admitted");
        b.dut.s_axi_arvalid = 0; b.step();
        held_read = true; held_read_data = oracle[0]; held_read_response = 0;
        b.write(0, 1); b.idle(1000);
        require(oracle[0] == replacement && oracle[0] != held_read_data, "held-reply fixture did not change actual RAM");
        b.dut.s_axi_rready = 1; edge = b.step();
        require(edge.r && edge.rresp == 0 && edge.data == held_read_data, "captured snapshot did not survive later RUN");
        held_read = false; b.dut.s_axi_rready = 0; b.idle(1);
        stop(false);
        // Return to the user's valid image and finish safely stopped.
        load(image);
        require(b.read(0x40) == 1 && b.read(0x28) == 0, "bridge not left safely stopped");
        std::cout << "PASS: coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, "
                  << "full-UART stop; snapshots=" << snapshots << " observed_stores=" << stores << '\n';
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
