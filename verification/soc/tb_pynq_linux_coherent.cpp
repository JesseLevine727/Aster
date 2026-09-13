#include "../common/linux_bus.h"
#include "Vaster_pynq_linux___024root.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <set>
#include <string>
#include <vector>
#include <unistd.h>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_L1 1
#endif
#ifndef ASTER_DMA
#define ASTER_DMA 0
#endif
#ifndef ASTER_CLOCK
#define ASTER_CLOCK 400
#endif
static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
static void evidence_file(const std::string& path, const std::vector<uint8_t>& data) {
    const int fd = open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
    require(fd >= 0,"functional evidence exists or cannot be created");
    for (size_t offset = 0; offset < data.size();) {
        const auto count = write(fd,data.data()+offset,data.size()-offset);
        if (count <= 0) { close(fd); throw std::runtime_error("functional evidence write failed"); }
        offset += count;
    }
    require(close(fd) == 0,"functional evidence close failed");
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
        Verilated::commandArgs(argc, argv); std::cout.setf(std::ios::unitbuf);
        require(argc == 3 || argc == 4, "usage: linux_coherent firmware.hex runtime|lifecycle|dma|publication [DMA-evidence-prefix]");
        const bool lifecycle = std::string(argv[2]) == "lifecycle";
        const bool dma = std::string(argv[2]) == "dma";
        const bool publication = std::string(argv[2]) == "publication";
        require(lifecycle || std::string(argv[2]) == "runtime" || (ASTER_DMA && (dma || publication)), "unknown firmware kind");
        require(argc == 3 || (ASTER_DMA && (dma || publication) && argv[3][0]), "evidence prefix requires DMA functional firmware");
        std::ifstream file(argv[1]); require(bool(file), "cannot read firmware");
        std::vector<std::uint32_t> image;
        for (std::string word; file >> word;) image.push_back(std::stoul(word, nullptr, 16));
        require(image.size() == 16384, "firmware must fill the 64 KiB boot store");
        Bus b;
        std::array<std::uint32_t, 16384> oracle{};
        unsigned stores = 0, snapshots = 0;
        std::array<std::array<uint64_t,14>,2> cpu_counts{}, frozen_cpu{};
        std::array<uint64_t,14> dma_counts{}, frozen_dma{};
        bool counting = false;
        unsigned starts = 0, freezes = 0, payload_bytes = 0;
        std::array<unsigned,2> ram_retired{};
        std::array<std::set<uint32_t>,2> ram_pcs;
        bool held_read = false;
        std::uint32_t held_read_data = 0;
        unsigned held_read_response = 0;
        b.before_edge = [&]() {
            if (held_read)
                require(b.dut.s_axi_rvalid && b.dut.s_axi_rdata == held_read_data && b.dut.s_axi_rresp == held_read_response,
                        "RAM reply changed under concurrent RUN/backpressure");
            const auto* root = b.dut.rootp;
            for (unsigned h = 0; h < 2; ++h) {
                auto pc = root->aster_pynq_linux__DOT__hart_pc[h];
                if ((root->aster_pynq_linux__DOT__hart_retired & (1u << h)) && pc >= 0x10000000 && pc < 0x10008000) {
                    ++ram_retired[h]; ram_pcs[h].insert(pc);
                }
            }
            if (ASTER_DMA) {
                if (root->aster_pynq_linux__DOT__coherent_stopped) { counting = false; cpu_counts = {}; dma_counts = {}; }
                else if (root->aster_pynq_linux__DOT__coherent_perf_start) {
                    require(!counting,"overlapping DMA functional counter windows"); counting = true; cpu_counts = {}; dma_counts = {}; ++starts;
                } else if (root->aster_pynq_linux__DOT__coherent_perf_freeze) {
                    require(counting,"freeze without DMA functional window"); counting = false; frozen_cpu = cpu_counts; frozen_dma = dma_counts; ++freezes;
                } else if (counting) {
                    for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i)
                        cpu_counts[h][i] += (root->aster_pynq_linux__DOT__coherent_perf_events[h] >> i)&1;
                    for (unsigned i = 0; i < 14; ++i) dma_counts[i] += root->aster_pynq_linux__DOT__dma_events[i];
                }
                const bool read = root->aster_pynq_linux__DOT__dma_request_pending && root->aster_pynq_linux__DOT__dma_request_ready &&
                    !root->aster_pynq_linux__DOT__dma_request_mask;
                if (read) {
                    const auto addr = root->aster_pynq_linux__DOT__dma_request_addr;
                    require(addr >= 0x10000000 && addr < 0x10008000 && !(addr&3), "DMA read escaped shared RAM");
                    require(root->aster_pynq_linux__DOT__dma_request_rdata == oracle[(addr-0x10000000)/4], "AXI-shell DMA source was stale");
                }
                if (root->aster_pynq_linux__DOT__dma_store_commit) {
                    const auto addr = root->aster_pynq_linux__DOT__dma_backing_addr;
                    const auto mask = root->aster_pynq_linux__DOT__dma_backing_mask;
                    require(!root->aster_pynq_linux__DOT__coherent_store_commit && root->aster_pynq_linux__DOT__dma_backing_valid &&
                        root->aster_pynq_linux__DOT__dma_backing_ready && root->aster_pynq_linux__DOT__dma_backing_device && mask &&
                        addr >= 0x10000000 && addr < 0x10008000 && !(addr&3), "DMA payload acceptance misrouted");
                    oracle[(addr-0x10000000)/4] = merge(oracle[(addr-0x10000000)/4],root->aster_pynq_linux__DOT__dma_backing_data,mask);
                    for (unsigned i = 0; i < 4; ++i) payload_bytes += (mask >> i)&1;
                }
            }
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
        require(b.read(0x18) == 0x41535452 && b.read(0x1c) == (ASTER_DMA ? 0x00070001 : 0x00060001) &&
                b.read(0x20) == ASTER_CLOCK && b.read(0x24) == ASTER_HART_COUNT &&
                b.read(0x44) == (1u | unsigned(ASTER_L1) << 1 | unsigned(ASTER_DMA) << 2), "incorrect coherent bridge identity/configuration");
        require(b.read(0x40) == 1, "initial bridge STOPPED missing");
        // Complete host diagnostic aperture: aligned reads only, never writes,
        // with split AW/W and held R/B on the common Bus helper.
        for (unsigned addr = 0x80; addr < 0x114; ++addr) {
            const bool allowed = ASTER_DMA && !(addr&3) && addr < 0x110;
            auto value = b.read(addr, allowed ? 0 : 2);
            require(value == (allowed && addr == 0x80 ? 1u : allowed && addr == 0x9c ? 5u : 0u), "wrong initial/denied DMA diagnostic value");
            for (unsigned strobe : {0u,1u,15u}) b.write(addr,0xffffffff,addr%3,strobe,2);
        }
        b.read(0x20001, 2); b.read(0x30000, 2); b.write(0x20000, 7, 0, 15, 2);
        load(image);
        b.write(0x10000, 0xffffffff, 0, 0); // Zero strobe must not corrupt ROM.
        const std::string expected = publication ? "DMA CODE PASS\n" :
            dma ? "DMA DIRECTED PASS\nDMA RESERVATIONS PASS\nDMA JOB PASS\nDMA JOB PASS\nDMA JOB PASS\nDMA COUNTERS PASS\n" :
            lifecycle ? "COHERENT LIFECYCLE PASS\n" :
            "RV32A DIRECTED PASS\nRV32A JOB PASS\nRV32A JOB PASS\nRV32A JOB PASS\n";
        for (unsigned boot = 0; boot < 2; ++boot) {
            ram_retired = {}; ram_pcs = {};
            b.write(0, 1, boot % 3);
            b.write(0x10000, 0xffffffff, 2, 15, 2); // ROM is not writable while running.
            require(b.read(0x20000, 2) == 0, "running cache/RAM snapshot was accepted");
            b.idle(200000); // Host pause with bounded serial receive buffering.
            std::string output;
            for (unsigned polls = 0; polls < (dma ? 30000000u : 1000000u) && output != expected; ++polls) {
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
            uint64_t job_cycles = 0;
            if (dma || publication) {
                require(starts == boot+1 && freezes == boot+1 && !counting &&
                    (publication ? payload_bytes == 64*(boot+1) : payload_bytes > 50000*(boot+1)), "missing actual DMA functional execution");
                require(b.read(0x84) == 2 && b.read(0x88) == (publication ? 8u : 1024u) && b.read(0x8c) == 0 && b.read(0x98) == 0,
                        "wrong live terminal DMA diagnostics");
                const auto lo = b.read(0x90), hi = b.read(0x94); job_cycles = (uint64_t(hi) << 32) | lo;
                require(job_cycles == b.dut.rootp->aster_pynq_linux__DOT__dma_job_cycles,"host last-job cycles differ from actual engine");
                for (unsigned i = 0; i < 14; ++i) {
                    const auto lo = b.read(0xa0+i*8), hi = b.read(0xa4+i*8);
                    require((uint64_t(hi) << 32 | lo) == frozen_dma[i], "host DMA counter differs from actual window");
                    require(oracle[0x8000/4+16+i*2] == uint32_t(frozen_dma[i]) && oracle[0x8000/4+17+i*2] == uint32_t(frozen_dma[i] >> 32),
                            "DMA RAM publication differs from actual counter window");
                    for (unsigned h = 0; h < 2; ++h) require(oracle[0x8000/4+44+h*28+i*2] == uint32_t(frozen_cpu[h][i]) &&
                        oracle[0x8000/4+45+h*28+i*2] == uint32_t(frozen_cpu[h][i] >> 32), "CPU RAM publication differs from actual counter window");
                }
                require(frozen_dma[10] == (publication ? 8u : 331+(ASTER_HART_COUNT == 2 ? 8 : 0)) &&
                    frozen_dma[11] == (publication ? 0u : 1u) && frozen_dma[12] == (publication ? 0u : 6u) && frozen_dma[13] == 0,
                        "missing/extra DMA functional completions/errors");
            }
            stop(true);
            if (dma || publication) {
                for (unsigned addr = 0x84; addr < 0x110; addr += 4)
                    require(b.read(addr) == (addr == 0x9c ? 5u : 0u), "STOPPED retained DMA state/counters");
                require(oracle[0x8000/4] == (publication ? 8u : 3u) && oracle[0x8000/4+1] == (publication ? 0u : 320u) &&
                    oracle[0x8000/4+7] == (publication ? 8u : 1024u) &&
                    oracle[0x8000/4+8] == 5 && oracle[0x8000/4+9] == ASTER_CLOCK &&
                    oracle[0x8000/4+10] == 6+ASTER_L1, "retained DMA runtime metadata is wrong");
                if (publication) {
                    require(oracle[0x8000/4+3] == 0x138 && oracle[0x8000/4+4] == (ASTER_HART_COUNT == 2 ? 0x138u : 0u),
                            "published RAM code was not executed correctly by both consumers");
                    const auto code_address = oracle[0x8000/4+5], source_address = oracle[0x8000/4+11];
                    for (auto address : {code_address, source_address})
                        require(address >= 0x10000000 && address <= 0x10008000-64 && address%64 == 0, "invalid code/source allocation");
                    require(code_address+64 <= source_address || source_address+64 <= code_address, "overlapping code/source allocations");
                    const std::set<uint32_t> expected_pcs{code_address,code_address+4};
                    require(ram_retired[0] == 32 && ram_pcs[0] == expected_pcs &&
                        (ASTER_HART_COUNT == 2 ? ram_retired[1] == 16 && ram_pcs[1] == expected_pcs : ram_retired[1] == 0 && ram_pcs[1].empty()),
                        "missing actual RAM-code instruction retirements on the consumers");
                    for (unsigned i = 0; i < 16; ++i) {
                        const auto code_word = oracle[(code_address-0x10000000)/4+i];
                        const auto source_word = oracle[(source_address-0x10000000)/4+i];
                        require(code_word == (i == 0 ? 0x13800513u : i == 1 ? 0x8067u : 0xc0010000u^i) &&
                            source_word == (i == 0 ? 0x13800513u : i == 1 ? 0x8067u : 0xa57e0000u^i), "code/source guards changed during DMA publication");
                    }
                    require(frozen_dma[2] == 16 && frozen_dma[3] == 16 && frozen_dma[4] == 64, "RAM code was not actually copied by DMA");
                }
            } else if (!lifecycle) for (unsigned i = 1; i <= 4; ++i)
                require(oracle[0x8000/4+i] == 128 * ASTER_HART_COUNT, "independent atomic result mismatch");
            std::cout << "PASS: coherent AXI/serial " << argv[2] << " harts=" << ASTER_HART_COUNT
                      << " cache=" << ASTER_L1 << " boot=" << boot << " bytes=" << output.size()
                      << " retired=" << h0 << "," << h1 << " retained_RAM=65536\n";
            if (argc == 4) {
                const std::string prefix = std::string(argv[3])+".boot"+std::to_string(boot+1);
                std::vector<uint8_t> ram;
                for (unsigned i = 0; i < oracle.size(); ++i) {
                    const auto word = b.read(0x20000+i*4);
                    require(word == oracle[i],"functional evidence differs from architectural store oracle");
                    for (unsigned byte = 0; byte < 4; ++byte) ram.push_back(word >> (byte*8));
                }
                evidence_file(prefix+".ram",ram);
                evidence_file(prefix+".uart",std::vector<uint8_t>(output.begin(),output.end()));
                std::cout << "DMA_FUNCTIONAL_OBS {\"boot\":" << boot+1 << ",\"kind\":\"" << argv[2]
                          << "\",\"cpu\":[";
                for (unsigned h = 0; h < 2; ++h) {
                    std::cout << (h ? ",[" : "[");
                    for (unsigned i = 0; i < 14; ++i) std::cout << (i ? "," : "") << frozen_cpu[h][i];
                    std::cout << ']';
                }
                std::cout << "],\"dma\":[";
                for (unsigned i = 0; i < 14; ++i) std::cout << (i ? "," : "") << frozen_dma[i];
                std::cout << "],\"job_cycles\":" << job_cycles << ",\"ram_retired\":[" << ram_retired[0] << ',' << ram_retired[1]
                          << "],\"ram_pcs\":[";
                for (unsigned h = 0; h < 2; ++h) {
                    std::cout << (h ? ",[" : "["); bool first = true;
                    for (auto pc : ram_pcs[h]) { std::cout << (first ? "" : ",") << pc; first = false; }
                    std::cout << ']';
                }
                std::cout << "]}\n";
            }
        }
        if (dma) {
            // ARM requests a global STOP while a real transfer is in flight.
            // Byte 32 is deliberately far from the end of these descriptors;
            // the split-channel control write cannot merely catch an idle job.
            for (unsigned phase = 0; phase < 3; ++phase) {
                load(image); b.write(0,1,phase);
                bool reached = false;
                for (unsigned cycle = 0; cycle < 150000000; ++cycle) {
                    b.dut.aclk = 0; b.dut.eval(); const auto* root = b.dut.rootp;
                    bool active = root->aster_pynq_linux__DOT__dma_busy && root->aster_pynq_linux__DOT__dma_bytes_done == 32;
                    bool trigger = phase == 0 ? root->aster_pynq_linux__DOT__dma_request_pending && !root->aster_pynq_linux__DOT__dma_request_ready &&
                            !root->aster_pynq_linux__DOT__dma_request_mask :
                        phase == 1 ? root->aster_pynq_linux__DOT__dma_request_pending && !root->aster_pynq_linux__DOT__dma_request_ready &&
                            root->aster_pynq_linux__DOT__dma_request_mask : root->aster_pynq_linux__DOT__dma_store_commit;
                    if (active && trigger) { reached = true; break; }
                    b.step();
                }
                require(reached,"did not reach in-flight DMA AXI stop stage");
                const auto before = payload_bytes; stop(true);
                require(payload_bytes >= before && payload_bytes <= before+8 && b.read(0x84) == 0 && b.read(0x98) == 0,
                        "global stop issued extra DMA payload / did not quiesce");
                std::cout << "PASS: DMA AXI in-flight stop phase=" << phase << " retained_RAM=65536 drained_bytes=" << payload_bytes-before << '\n';
            }
            // Actual-core LR.W on DMA MMIO must fault, not submit a descriptor.
            load({0x300000b7,0x1000a12f,0x0000006f}); b.write(0,1);
            bool faulted = false;
            for (unsigned i = 0; i < 1000; ++i) if (b.read(4)&2) { faulted = true; break; }
            require(faulted && b.read(0x50) == 0x15 && b.read(0x54) == 0x30000000 &&
                b.read(0x58) == 0x1000a12f && b.read(0x5c) == 4 && b.read(0x84) == 0, "actual-core DMA MMIO LR permission fault incorrect");
            stop(false);
            // Jumping into the data-only DMA page must not execute register
            // values or produce DMA side effects; the denied fetch traps.
            load({0x300000b7,0x00008067}); b.write(0,1); faulted = false;
            for (unsigned i = 0; i < 1000; ++i) if (b.read(4)&2) { faulted = true; break; }
            require(faulted && b.read(0x84) == 0 && b.read(0x88) == 0, "DMA MMIO instruction fetch was not denied");
            stop(false);
            std::cout << "PASS: actual-core DMA MMIO atomic and instruction-fetch denial\n";
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
