#include "Vaster_pcpi_probe.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

// This is an interface feasibility test. A mock backend returns distinctive
// values; it deliberately does NOT implement or claim AMO/LR/SC semantics.
static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
static std::uint32_t addi(unsigned rd, unsigned rs, unsigned imm) {
    return ((imm & 4095) << 20) | (rs << 15) | (rd << 7) | 0x13;
}
static std::uint32_t sw(unsigned rs, unsigned offset) {
    return ((offset >> 5) << 25) | (rs << 20) | (8 << 15) | (2 << 12) |
           ((offset & 31) << 7) | 0x23;
}
static std::uint32_t atomic(unsigned op, unsigned order, unsigned rd,
                            unsigned rs2, unsigned width = 2) {
    return (op << 27) | (order << 25) | (rs2 << 20) | (1 << 15) |
           (width << 12) | (rd << 7) | 0x2f;
}
struct Case {
    unsigned op = 0, order = 0, rd = 7, rs2 = 2, width = 2;
    std::uint32_t address = 0x10000040;
    unsigned fault = 0;
    bool illegal = false;
};
static std::vector<std::uint32_t> program(const Case& c) {
    return {
        ((c.address + 0x800u) & 0xfffff000) | (1 << 7) | 0x37,
        addi(1, 1, c.address & 4095), addi(2, 0, 37),
        0x10000437, addi(9, 0, 7), sw(9, 0), 0x00042183,
        addi(4, 0, 3), 0x024182b3, 0x0242c333, 0x0ff0000f,
        atomic(c.op, c.order, c.rd, c.rs2, c.width),
        sw(c.rd, 4), sw(6, 8), addi(10, 0, 99), 0x00100073
    };
}

struct Counts {
    unsigned runs = 0, commands = 0, completions = 0, resets = 0;
    unsigned overlapping_fetches = 0;
};
static Counts counts;
static std::mt19937 rng(0xa57e6);

static void reset(Vaster_pcpi_probe& d) {
    d.resetn = 0;
    d.mem_ready = d.mem_rdata = d.cmd_ready = d.cmd_result = d.cmd_fault = 0;
    d.eval();
    require(!d.cmd_valid && !d.pcpi_wait && !d.pcpi_ready && !d.mem_valid &&
            !d.instr_retired && !d.fault_valid, "reset did not gate outputs immediately");
    for (unsigned i = 0; i < 8; ++i) {
        d.clk = 0; d.eval(); d.clk = 1; d.eval();
        require(!d.busy && !d.fault_valid, "reset retained adapter state");
    }
    d.resetn = 1;
}

// reset_point: 1=unaccepted command after long stall, 2=completed command
// before PCPI retirement, 3=recorded fault before timeout reaches trap.
// These test destructive component reset only, NOT the future warm-stop drain.
static void run(Vaster_pcpi_probe& d, const Case& c, int latency, unsigned reset_point = 0) {
    reset(d);
    const auto code = program(c);
    constexpr unsigned atomic_pc = 44;
    const auto result = 0x8ac01234u ^ (c.op << 16) ^ (c.order << 8);
    std::array<std::uint32_t, 3> ram = {0, 0xdeadbeef, 0xabcdef01};
    std::vector<unsigned> retired;
    unsigned native_left = 0, cmd_left = 0, trap_cycles = 0, pending_cycles = 0;
    unsigned responses = 0, successes = 0, ready_edges = 0, reads = 0, writes = 0;
    bool native_pending = false, cmd_pending = false;
    std::uint32_t addr = 0, data = 0, mask = 0, instr = 0;
    const unsigned fault = c.illegal ? 0 : (c.address & 3) ? (c.op == 2 ? 4 : 6) : c.fault;
    const bool will_halt = c.illegal || fault;
    for (unsigned cycle = 0; cycle < 30000; ++cycle) {
        d.clk = 0; d.mem_ready = 0; d.cmd_ready = 0; d.eval();
        if ((reset_point == 1 && d.cmd_valid && pending_cycles == 65) ||
            (reset_point == 2 && d.pcpi_ready) ||
            (reset_point == 3 && d.fault_valid)) {
            require(retired.size() <= atomic_pc/4, "atomic retired before reset fixture");
            require(successes == (reset_point == 2 ? 1u : 0u), "unexpected reset-side commit count");
            reset(d); ++counts.resets; return;
        }
        if (d.cmd_valid) {
            require(!c.illegal && !(c.address & 3), "illegal/misaligned command reached backend");
            require(d.cmd_addr == c.address && d.cmd_op == c.op && d.cmd_order == c.order,
                    "atomic address/op/order changed or decoded incorrectly");
            const auto operand = c.rs2 == 0 ? 0u : c.rs2 == 1 ? c.address : 37u;
            require(d.cmd_operand == operand, "PCPI operand/alias capture incorrect");
            require(d.pcpi_wait && !d.pcpi_ready, "timeout not held during backend request");
            if (!cmd_pending) {
                require(responses == 0, "command duplicated while PCPI valid held");
                cmd_pending = true;
                cmd_left = reset_point == 1 ? 100 : latency < 0 ? rng() % 97 : unsigned(latency);
            }
            ++pending_cycles;
            if (cmd_left) --cmd_left;
            else {
                d.cmd_ready = 1; d.cmd_result = result; d.cmd_fault = c.fault;
                ++responses; if (!c.fault) ++successes;
                cmd_pending = false;
            }
        } else require(!cmd_pending, "atomic command withdrawn while stalled");

        if (d.mem_valid) {
            if (d.cmd_valid && d.mem_instr) ++counts.overlapping_fetches;
            if (!native_pending) {
                native_pending = true;
                addr = d.mem_addr; data = d.mem_wdata; mask = d.mem_wstrb; instr = d.mem_instr;
                native_left = latency < 0 ? rng() % 33 : unsigned(latency);
            } else require(addr == d.mem_addr && data == d.mem_wdata && mask == d.mem_wstrb &&
                           instr == d.mem_instr, "native request changed under PCPI/prefetch stall");
            if (native_left) --native_left;
            else {
                d.mem_ready = 1;
                d.mem_rdata = addr < code.size()*4 ? code[addr/4] :
                    addr >= 0x10000000 && addr < 0x1000000c ? ram[(addr - 0x10000000)/4] : 0;
                if (mask) {
                    require(addr >= 0x10000000 && addr < 0x1000000c && mask == 15,
                            "unexpected native store");
                    ram[(addr - 0x10000000)/4] = data; ++writes;
                } else if (!instr) ++reads;
                native_pending = false;
            }
        } else require(!native_pending, "native request disappeared while stalled");
        d.eval();
        if (d.pcpi_ready && d.pcpi_valid) ++ready_edges;
        if (d.fault_valid) {
            require(fault && d.fault_cause == fault && d.fault_addr == c.address &&
                    d.fault_insn == code[atomic_pc/4], "fatal atomic fault lost cause/address/instruction");
            require(!d.pcpi_wait && !d.pcpi_ready && !d.cmd_valid,
                    "fault did not release timeout / exposed a transaction");
        }
        d.clk = 1; d.eval();
        if (d.instr_retired) {
            const auto expected_pc = unsigned(retired.size()*4);
            require(d.retired_pc == expected_pc && d.retired_insn == code[expected_pc/4],
                    "retirement is not exactly once/in order with correct opcode");
            require(d.retired_pc < (will_halt ? atomic_pc : 60), "faulting instruction retired");
            retired.push_back(d.retired_pc);
        }
        if (d.trap) {
            ++trap_cycles;
            if (trap_cycles > 2)
                require(d.retired_pc == (will_halt ? atomic_pc : 60) &&
                        d.retired_insn == code[d.retired_pc/4], "fatal trap instruction identity incorrect");
            if (trap_cycles == 40) break;
        }
    }
    require(!reset_point, "reset fixture was not reached");
    require(trap_cycles == 40, "core did not reach persistent terminal trap");
    require(retired.size() == (will_halt ? 11u : 15u), "retirement count incorrect");
    require(reads == 1 && writes == (will_halt ? 1u : 3u), "native accesses missing/duplicated");
    require(ram[0] == 7, "native load/store fixture corrupted");
    require(ram[1] == (will_halt ? 0xdeadbeefu : c.rd ? result : 0u), "PCPI register writeback/x0 incorrect");
    require(ram[2] == (will_halt ? 0xabcdef01u : 7u), "internal MUL/DIV or fault isolation incorrect");
    require(successes == (will_halt ? 0u : 1u) && ready_edges == successes,
            "atomic completed/acknowledged more or less than once");
    require(responses == (c.illegal || (c.address & 3) ? 0u : 1u), "backend response count incorrect");
    require(bool(d.fault_valid) == bool(fault), "fault sticky state incorrect");
    ++counts.runs; counts.commands += responses; counts.completions += successes;
}

int main(int argc, char** argv) {
    std::string context;
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_pcpi_probe d;
        const std::array<unsigned, 11> ops = {2, 3, 1, 0, 4, 12, 8, 16, 20, 24, 28};
        for (int latency : {0, 1, 19, 65, -1})
        for (unsigned boot = 0; boot < 2; ++boot)
        for (auto op : ops) for (unsigned order = 0; order < 4; ++order)
        for (auto rd : {0u, 1u, 2u, 7u}) for (auto rs2 : {1u, 2u}) {
            Case c; c.op = op; c.order = order; c.rd = rd; c.rs2 = op == 2 ? 0 : rs2;
            context = "legal op=" + std::to_string(op) + " order=" + std::to_string(order) +
                      " rd=" + std::to_string(rd) + " latency=" + std::to_string(latency);
            run(d, c, latency);
        }
        for (int latency : {0, 65, -1}) for (auto op : ops)
        for (unsigned order = 0; order < 4; ++order) {
            Case c; c.op = op; c.order = order; c.rs2 = op == 2 ? 0 : 2;
            context = "fault op=" + std::to_string(op) + " latency=" + std::to_string(latency);
            for (unsigned offset = 1; offset <= 3; ++offset) {
                c.address = 0x10000040 + offset; run(d, c, latency);
            }
            // Backend fault classification is supplied by this mock, not proof
            // of actual permission decoding. Full memory-engine tests follow.
            for (auto address : {0u, 0x20000000u, 0x10010000u, 0xfffffffcu}) {
                c.address = address; c.fault = op == 2 ? 5 : 7; run(d, c, latency);
            }
        }
        for (unsigned op = 0; op < 32; ++op) for (unsigned width = 0; width < 8; ++width) {
            bool legal = false;
            for (auto valid : ops) if (op == valid && width == 2) legal = true;
            if (legal) continue;
            Case c; c.op = op; c.width = width; c.illegal = true;
            context = "illegal op=" + std::to_string(op) + " width=" + std::to_string(width);
            run(d, c, -1);
        }
        for (unsigned rs2 = 1; rs2 < 32; ++rs2) {
            Case c; c.op = 2; c.rs2 = rs2; c.illegal = true;
            context = "malformed LR rs2=" + std::to_string(rs2); run(d, c, 19);
        }
        for (unsigned point = 1; point <= 3; ++point) for (unsigned repeat = 0; repeat < 8; ++repeat) {
            Case c; if (point == 3) c.fault = 7;
            context = "reset point=" + std::to_string(point);
            run(d, c, -1, point);
            run(d, Case{}, -1); // Same physical model, no inherited command/fault.
        }
        require(counts.overlapping_fetches != 0, "no native fetch/PCPI overlap exercised");
        std::cout << "PASS: PCPI real-core boundary: " << counts.runs << " programs, "
                  << counts.commands << " command responses, " << counts.completions
                  << " exactly-once completions, " << counts.resets << " destructive reset probes, "
                  << counts.overlapping_fetches << " overlapping native-fetch cycles; seed=0xa57e6\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << context << ": " << e.what() << '\n'; return 1;
    }
}
