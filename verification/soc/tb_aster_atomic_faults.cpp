#include "Vaster_atomic_probe.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

#ifndef ASTER_ATOMIC_CACHE
#define ASTER_ATOMIC_CACHE 0
#define ASTER_LINE_WORDS 4
#define ASTER_LINE_COUNT 16
#endif
static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
static std::uint32_t encode(unsigned op, unsigned order, unsigned rs2, unsigned width = 2) {
    return (op << 27) | (order << 25) | (rs2 << 20) | (1 << 15) | (width << 12) | (7 << 7) | 0x2f;
}
static unsigned total = 0;
static std::mt19937 rng(0xfa0176);
static void run(Vaster_atomic_probe& d, unsigned hart, unsigned op, unsigned order,
                std::uint32_t address, bool reserved, int latency, bool illegal = false) {
    const auto fault_insn = encode(op, order, op == 2 ? 0 : 2, illegal ? 3 : 2);
    const unsigned cause = illegal ? 0 : address & 3 ? (op == 2 ? 4 : 6) : (op == 2 ? 5 : 7);
    const std::array<std::uint32_t, 11> code = {
        0x10000437, 0x00700493, 0x00942023, 0x00040093, // RAM, 7, SW, address
        reserved ? encode(2, 0, 0) : 0x00000013,
        ((address + 0x800u) & 0xfffff000u) | 0xb7,
        ((address & 4095u) << 20) | 0x8093, 0x06300113,
        fault_insn, 0x00042023, 0x00100073 // Poison store must never execute.
    };
    std::array<std::uint32_t, 16384> ram;
    for (unsigned i = 0; i < ram.size(); ++i) ram[i] = 0xa57e0000u + i;
    d.resetn = d.hart_run = d.m_ready = d.m_rdata = d.flush_valid = 0;
    for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    d.resetn = 1; d.hart_run = 1u << hart;
    bool pending = false;
    unsigned left = 0, source = 0, mask = 0, traps = 0, retired = 0, atomic_ops = 0, atomic_retired = 0;
    unsigned accepted_atomic_transfers = 0;
    bool instr = false;
    std::uint32_t held_addr = 0, held_data = 0;
    for (unsigned cycle = 0; cycle < 30000; ++cycle) {
        d.clk = 0; d.m_ready = 0; d.eval();
        if (d.m_valid) {
            if (!pending) {
                pending = true; held_addr = d.m_addr; held_data = d.m_wdata; mask = d.m_wstrb;
                source = d.m_owner; instr = d.m_instr;
                left = latency < 0 ? rng() % 49 : unsigned(latency);
            } else require(held_addr == d.m_addr && held_data == d.m_wdata && mask == d.m_wstrb &&
                           source == d.m_owner && instr == bool(d.m_instr), "fault disturbed admitted transfer");
            if (left) --left;
            else {
                require(source == hart, "fault touched peer's physical port");
                const bool rom = held_addr < 65536;
                const bool ram_line = held_addr >= 0x10000000 && held_addr < 0x10000000 + ASTER_LINE_WORDS*4;
                // The only lawful data is the initial sentinel store / optional
                // LR and its cache line. All atomic target faults are side-effect free.
                require(rom && instr && !mask || ram_line && !instr, "fault reached ROM data/MMIO/denied RAM");
                d.m_ready = 1;
                d.m_rdata = rom ? (held_addr < code.size()*4 ? code[held_addr/4] : 0) : ram[(held_addr - 0x10000000)/4];
                if (mask) {
                    const unsigned index = (held_addr - 0x10000000)/4;
                    require(mask == 15 && held_data == (index ? 0xa57e0000u + index : 7),
                            "faulting or subsequent store escaped to backing memory");
                    ram[index] = held_data;
                }
                pending = false;
            }
        } else require(!pending, "fault withdrew admitted backing request");
        d.eval();
        if (d.op_accepted && d.op_atomic) ++accepted_atomic_transfers;
        if (d.atomic_complete) ++atomic_ops;
        d.clk = 1; d.eval();
        require(!(d.retired & (1u << !hart)) && !(d.hart_trap & (1u << !hart)), "inactive peer executed/trapped");
        if (d.retired & (1u << hart)) {
            require(retired < 8 && d.retired_pc[hart] == retired*4 && d.retired_insn[hart] == code[retired],
                    "fault retired / wrong exact prefix retirement sequence");
            if ((d.retired_insn[hart] & 127) == 0x2f) ++atomic_retired;
            ++retired;
        }
        if (d.hart_trap & (1u << hart)) {
            if (++traps > 2) {
                require(d.retired_pc[hart] == 32 && d.retired_insn[hart] == fault_insn, "wrong trapped PC/opcode");
                require(bool(d.fault_valid & (1u << hart)) == !illegal, "fault/illegal classification incorrect");
                if (!illegal)
                    require(d.fault_cause[hart] == cause && d.fault_addr[hart] == address &&
                            d.fault_insn[hart] == fault_insn, "typed fatal EEI cause/address/opcode incorrect");
            }
            if (traps == 50) break;
        }
    }
    require(traps == 50 && retired == 8, "fault did not persistently halt after exact instruction prefix");
    require(atomic_ops == unsigned(reserved) && atomic_retired == unsigned(reserved) &&
            accepted_atomic_transfers == unsigned(reserved), "faulting atomic completed / accessed memory");
    auto latest = ram[0];
    for (unsigned slot = 0; slot < 2*ASTER_LINE_COUNT; ++slot) if (d.observed_state[slot]) {
        require(d.observed_tag[slot] == 0x10000000, "fault allocated a denied cache line");
        for (unsigned word = 0; word < ASTER_LINE_WORDS; ++word)
            require(d.observed_data[slot*ASTER_LINE_WORDS+word] == (word ? 0xa57e0000u + word : 7),
                    "fault/subsequent poison store changed cached data");
        latest = d.observed_data[slot*ASTER_LINE_WORDS];
    }
    require(latest == 7, "legitimate store before fault missing");
    ++total;
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_atomic_probe d;
        for (int latency : {0, 19, -1}) for (unsigned hart = 0; hart < 2; ++hart)
        for (unsigned op : {0u, 1u, 2u, 3u, 4u, 8u, 12u, 16u, 20u, 24u, 28u})
        for (unsigned order = 0; order < 4; ++order) {
            const auto peer = hart ? 0x10008000u : 0x1000c000u;
            for (auto address : {0u, 0x20000000u, 0x20002004u, 0x20003038u, peer, peer+0x3ffcu,
                                 0x10010000u, 0xfffffffcu, 0x10000001u, 0x10000002u, 0x10000003u}) {
                run(d, hart, op, order, address, false, latency);
                if (op == 3) run(d, hart, op, order, address, true, latency);
            }
            run(d, hart, op, order, 0x10000000, false, latency, true);
        }
        std::cout << "PASS: integrated RV32A faults cache=" << ASTER_ATOMIC_CACHE << " programs=" << total
                  << "; both real harts, all A/order bits, misalignment/access/.D, SC with/without LR, no fault effects/retirement\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: after " << total << " integrated fault programs: " << e.what() << '\n'; return 1;
    }
}
