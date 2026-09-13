#include "Vaster_atomic_probe.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#endif
static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
struct Atomic {
    unsigned reads = 0, writes = 0;
    bool sc_success = false, sc_failure = false;
};
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        require(argc == 2, "usage: atomic_probe firmware.bin");
        std::ifstream input(argv[1], std::ios::binary);
        require(bool(input), "cannot read firmware");
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)), {});
        require(!bytes.empty() && bytes.size() <= 65536, "firmware exceeds ROM");
        std::array<std::uint32_t, 16384> rom{}, ram{};
        for (unsigned i = 0; i < bytes.size(); ++i) rom[i/4] |= std::uint32_t(bytes[i]) << (8*(i%4));
        std::mt19937 rng(0xa70c6);
        for (auto& word : ram) word = rng();
        Vaster_atomic_probe d;
        for (int latency : {0, 1, 19, -1}) for (unsigned boot = 0; boot < 2; ++boot) {
            d.resetn = d.hart_run = d.m_ready = d.m_rdata = 0;
            for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
            require(!d.hart_trap && !d.retired && !d.fault_valid && !d.m_valid, "cluster reset failed");
            d.resetn = 1; unsigned hart_run = 1;
            std::array<unsigned, 2> retired{}, atomic_retired{}, atomic_completed{};
            std::array<std::deque<Atomic>, 2> completed;
            Atomic active;
            std::array<bool, 32> observed_ops{};
            unsigned successes = 0, failures = 0, jobs = 0, cycles = 0, postlude = 0;
            bool directed = false, pending = false;
            unsigned left = 0, owner = 0, mask = 0;
            bool instr = false, atomic = false;
            std::uint32_t address = 0, data = 0;
            std::string line;
            for (; cycles < 30000000; ++cycles) {
                d.clk = 0; d.m_ready = 0; d.hart_run = hart_run; d.eval();
                require(!d.hart_trap && !d.fault_valid, "unexpected real-core trap / fatal atomic access");
                if (d.m_valid) {
                    if (!pending) {
                        pending = true; address = d.m_addr; data = d.m_wdata; mask = d.m_wstrb;
                        owner = d.m_owner; instr = d.m_instr; atomic = d.m_atomic;
                        left = latency < 0 ? rng() % 33 : unsigned(latency);
                    } else require(address == d.m_addr && data == d.m_wdata && mask == d.m_wstrb &&
                                   owner == d.m_owner && instr == bool(d.m_instr) && atomic == bool(d.m_atomic),
                                   "real-core shared-port transfer unstable under stalls");
                    if (left) --left;
                    else {
                        d.m_ready = 1; d.m_rdata = 0;
                        const bool is_ram = address >= 0x10000000 && address < 0x10010000;
                        if (address < 65536) d.m_rdata = rom[address/4];
                        else if (is_ram) d.m_rdata = ram[(address - 0x10000000)/4];
                        else if (address == 0x20002000) d.m_rdata = owner;
                        else if (address == 0x20002004) d.m_rdata = hart_run >> 1;
                        else if (address == 0x20002008) d.m_rdata = ASTER_HART_COUNT;
                        if (atomic) {
                            require(is_ram && !instr, "atomic touched ROM/MMIO or instruction path");
                            if (mask) ++active.writes; else ++active.reads;
                        }
                        if (mask) {
                            if (is_ram) ram[(address - 0x10000000)/4] = merge(d.m_rdata, data, mask);
                            else if (address == 0x20002004 && owner == 0 && (mask & 1)) {
                                require(data == 1 && hart_run == 1, "unexpected lifecycle write in probe");
                                if (ASTER_HART_COUNT == 2) hart_run = 3;
                            } else if (address == 0x20000000 && owner == 0 && (mask & 1)) {
                                if (char(data) != '\n') line += char(data);
                                else {
                                    if (!directed) {
                                        require(line == "RV32A DIRECTED PASS", "directed firmware failed");
                                        directed = true;
                                    } else {
                                        require(line == "RV32A JOB PASS" && jobs < 3, "compiled atomic job failed");
                                        ++jobs;
                                        const auto base = 0x8000/4;
                                        require(ram[base] == jobs, "job epoch result not published");
                                        for (unsigned i = 1; i <= 4; ++i)
                                            require(ram[base+i] == 128 * ASTER_HART_COUNT,
                                                    "independent atomic/locked counter result mismatch");
                                        require(ram[base+5] == 8256 * (ASTER_HART_COUNT == 2 ? 3 : 1) &&
                                                ram[base+6] == 1 && ram[base+7] == ASTER_HART_COUNT,
                                                "protected sum/lock-free/worker result mismatch");
                                    }
                                    line.clear();
                                }
                            } else throw std::runtime_error("unexpected backing write");
                        }
                        pending = false;
                    }
                } else require(!pending, "backing transaction lost by native/PCPI merge");
                d.eval();
                if (d.atomic_complete) {
                    active.sc_success = d.sc_success; active.sc_failure = d.sc_failure;
                    completed[d.m_owner].push_back(active); active = {};
                    ++atomic_completed[d.m_owner]; successes += d.sc_success; failures += d.sc_failure;
                }
                d.clk = 1; d.eval();
                for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) {
                    ++retired[h];
                    require(d.retired_pc[h] < bytes.size() && d.retired_insn[h] == rom[d.retired_pc[h]/4],
                            "real-core retired PC/opcode not from firmware");
                    if ((d.retired_insn[h] & 127) == 0x2f) {
                        require(!completed[h].empty(), "atomic retired before completed memory transaction");
                        auto record = completed[h].front(); completed[h].pop_front();
                        const unsigned op = d.retired_insn[h] >> 27;
                        observed_ops[op] = true; ++atomic_retired[h];
                        if (op == 2) require(record.reads == 1 && record.writes == 0 && !record.sc_success && !record.sc_failure,
                                             "LR retired with incorrect side effects");
                        else if (op == 3) require(record.reads == 0 && record.writes == unsigned(record.sc_success) &&
                                                 record.sc_success != record.sc_failure, "SC retired with incorrect side effects");
                        else require(record.reads == 1 && record.writes == 1 && !record.sc_success && !record.sc_failure,
                                     "AMO retired without exactly one read and write");
                    }
                }
                if (jobs == 3 && ++postlude == 2000) break;
            }
            require(directed && jobs == 3 && line.empty() && postlude == 2000, "runtime timed out");
            for (unsigned h = 0; h < 2; ++h) {
                require(atomic_completed[h] == atomic_retired[h] && completed[h].empty(),
                        "missing/duplicate atomic retirement at job closeout");
                require(h < ASTER_HART_COUNT ? retired[h] > 1000 && atomic_retired[h] > 100 : retired[h] == 0,
                        "missing/unexpected actual hart execution");
            }
            for (unsigned op : {0u, 1u, 2u, 3u, 4u, 8u, 12u, 16u, 20u, 24u, 28u})
                require(observed_ops[op], "full RV32A operation coverage missing from actual retirement");
            require(successes && failures, "actual SC success/failure coverage missing");
            std::cout << "PASS: compiled RV32IMA runtime harts=" << ASTER_HART_COUNT << " latency=" << latency
                      << " boot=" << boot << " jobs=" << jobs << " cycles=" << cycles
                      << " retired=" << retired[0] << "," << retired[1] << " A-retired="
                      << atomic_retired[0] << "," << atomic_retired[1] << " SC=" << successes << "/" << failures << '\n';
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n'; return 1;
    }
}
