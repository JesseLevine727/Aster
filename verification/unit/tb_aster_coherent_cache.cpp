#include "Vaster_coherent_cache.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

#ifndef ASTER_LINE_WORDS
#define ASTER_LINE_WORDS 4
#define ASTER_LINE_COUNT 16
#define ASTER_CACHE_ENABLE 1
#endif
static constexpr unsigned words = ASTER_LINE_WORDS, lines = ASTER_LINE_COUNT;
static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
static std::uint32_t merge(std::uint32_t old, std::uint32_t value, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (value & (255u << (8*b)));
    return old;
}
class Bench {
public:
    Vaster_coherent_cache d;
    std::array<std::uint32_t, 16384> ram{}, oracle{};
    std::mt19937 rng;
    int latency;
    bool pending = false;
    unsigned left = 0, held_owner = 0, held_mask = 0;
    bool held_instr = false;
    std::uint32_t held_addr = 0, held_data = 0;
    unsigned requests = 0, reads = 0, writes = 0, stalled = 0, flushes = 0;
    unsigned accesses = 0, misses = 0, interventions = 0, invalidations = 0, writebacks = 0;

    Bench(unsigned seed, int delay) : rng(seed), latency(delay) {
        for (unsigned i = 0; i < ram.size(); ++i) ram[i] = oracle[i] = rng();
        d.resetn = d.s_valid = d.s_device = d.flush_valid = d.m_ready = 0;
        d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.resetn = 1;
    }
    static unsigned index(std::uint32_t addr) { return (addr - 0x10000000)/4; }
    static bool is_ram(std::uint32_t addr) { return addr >= 0x10000000 && addr < 0x10010000; }
    void invariant(bool data_check) {
        for (unsigned n = 0; n < lines; ++n) {
            const auto a = d.observed_state[n], b = d.observed_state[lines+n];
            require(a < 3 && b < 3, "invalid MSI state");
            if (a && b && d.observed_tag[n] == d.observed_tag[lines+n]) {
                require(a == 1 && b == 1, "multiple writable or writable-plus-shared owners");
                for (unsigned w = 0; w < words; ++w)
                    require(d.observed_data[n*words+w] == d.observed_data[(lines+n)*words+w],
                            "shared copies disagree during transient");
            }
        }
        if (!data_check) return;
        for (unsigned slot = 0; slot < 2*lines; ++slot) if (d.observed_state[slot]) {
            require(ASTER_CACHE_ENABLE, "cache-disabled build allocated line");
            const auto tag = d.observed_tag[slot];
            require(is_ram(tag) && !(tag & (words*4-1)), "invalid cache tag");
            for (unsigned w = 0; w < words; ++w) {
                const auto address = tag + w*4;
                require(d.observed_data[slot*words+w] == oracle[index(address)], "cached latest-data authority incorrect");
                if (d.observed_state[slot] == 1)
                    require(ram[index(address)] == oracle[index(address)], "S state advertised stale backing RAM");
            }
        }
    }
    // Evaluate low phase, service an independently stalled physical target,
    // collect events; caller accounts for CPU/flush handshake before rise().
    void low() {
        d.clk = 0; d.m_ready = 0; d.eval();
        invariant(false);
        if (d.m_valid) {
            if (!pending) {
                pending = true; held_addr = d.m_addr; held_data = d.m_wdata;
                held_mask = d.m_wstrb; held_owner = d.m_owner; held_instr = d.m_instr;
                left = latency < 0 ? rng() % 41 : unsigned(latency);
            } else require(held_addr == d.m_addr && held_data == d.m_wdata && held_mask == d.m_wstrb &&
                           held_owner == d.m_owner && held_instr == bool(d.m_instr), "stalled maintenance/request changed");
            if (left) { --left; ++stalled; }
            else {
                d.m_ready = 1;
                d.m_rdata = is_ram(d.m_addr) ? ram[index(d.m_addr)] : (d.m_addr ^ 0xcafe0123u);
                if (d.m_wstrb) {
                    if (is_ram(d.m_addr)) ram[index(d.m_addr)] = merge(d.m_rdata, d.m_wdata, d.m_wstrb);
                    ++writes;
                } else ++reads;
                pending = false;
            }
        } else require(!pending, "stalled physical transaction withdrawn");
        d.eval();
        accesses += d.access_event; misses += d.miss_event;
        interventions += d.intervention_event; invalidations += d.invalidation_event;
        writebacks += d.writeback_event;
    }
    void rise() { d.clk = 1; d.eval(); invariant(false); }
    void flush(unsigned mask) {
        d.s_valid = 0; d.flush_valid = 1; d.flush_mask = mask;
        std::vector<unsigned> peer_state;
        std::vector<std::uint32_t> peer_data, peer_tags;
        for (unsigned slot = 0; slot < 2*lines; ++slot) if (!(mask & (1u << (slot/lines)))) {
            peer_state.push_back(d.observed_state[slot]); peer_tags.push_back(d.observed_tag[slot]);
            for (unsigned w = 0; w < words; ++w) peer_data.push_back(d.observed_data[slot*words+w]);
        }
        bool done = false;
        for (unsigned cycle = 0; cycle < 1000000; ++cycle) {
            low(); const bool ready = d.flush_ready; require(!d.s_ready, "flush produced CPU response"); rise();
            if (ready) { done = true; break; }
        }
        require(done, "flush deadlocked"); d.flush_valid = 0; ++flushes;
        unsigned n = 0, w = 0;
        for (unsigned slot = 0; slot < 2*lines; ++slot) {
            if (mask & (1u << (slot/lines))) require(!d.observed_state[slot], "flushed bank still valid/dirty");
            else {
                require(d.observed_state[slot] == peer_state[n] && d.observed_tag[slot] == peer_tags[n],
                        "selective flush changed peer state/tag"); ++n;
                for (unsigned word = 0; word < words; ++word)
                    require(d.observed_data[slot*words+word] == peer_data[w++], "selective flush changed peer data");
            }
        }
        invariant(true);
        if (mask == 3) require(ram == oracle, "full flush lost an acknowledged store");
    }
    void access(unsigned hart, std::uint32_t addr, std::uint32_t value = 0, unsigned mask = 0,
                bool instr = false, bool request_flush = false) {
        d.s_valid = 1; d.s_owner = hart; d.s_instr = instr;
        d.s_addr = addr; d.s_wdata = value; d.s_wstrb = mask;
        const auto expected = is_ram(addr) ? oracle[index(addr)] : (addr ^ 0xcafe0123u);
        bool done = false;
        for (unsigned cycle = 0; cycle < 1000000; ++cycle) {
            // Ask to flush while a CPU transaction is already in progress.
            // It must finish first, including a store being held in M.
            if (request_flush && cycle == 2) { d.flush_valid = 1; d.flush_mask = 3; }
            low(); const bool ready = d.s_ready;
            require(!d.flush_ready, "flush acknowledged before admitted CPU request");
            if (ready) {
                if (!mask || instr) require(d.s_rdata == expected, "CPU/RAM-instruction read is stale");
                else if (is_ram(addr)) oracle[index(addr)] = merge(expected, value, mask);
            }
            rise();
            if (ready) { done = true; break; }
        }
        require(done, "coherent CPU transaction deadlocked"); d.s_valid = 0; ++requests;
        invariant(true);
        if (request_flush) flush(3);
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 0xc06e6;
        unsigned requests = 0, read = 0, write = 0, stalls = 0, flushes = 0, interventions = 0;
        for (int latency : {0, 1, 17, -1}) {
            Bench b(seed, latency);
            // I->S/S, S->M/I, M->S/S, M owner transfers, dirty victims,
            // false sharing and instruction reads of a modified RAM word.
            b.access(0, 0x10000000); b.access(1, 0x10000000);
            for (unsigned mask = 1; mask < 16; ++mask) {
                b.access(0, 0x10000000, 0xf0a05123 ^ mask, mask);
                b.access(1, 0x10000000);
                b.access(1, 0x10000000, 0x080bdead ^ mask, mask);
                b.access(0, 0x10000000, 0, 0, true);
                b.access(0, 0x10000004, 0x2468ace0, mask);
                b.access(1, 0x10000004);
            }
            for (unsigned hart = 0; hart < 2; ++hart) {
                b.access(hart, 0x10000000, 5, 15);
                b.access(hart, 0x10000000 + words*lines*4, 7, 15);
                b.access(!hart, 0x10000000);
                b.access(hart, 0x10000000, 9, 15);
                b.access(hart, 0x10000000, 0, 0, true);
                b.access(hart, 0x10000000, 11, 15, false, true);
            }
            for (unsigned i = 0; i < 3000; ++i) {
                const unsigned hart = b.rng() & 1;
                const bool private_ram = (b.rng() % 4) == 0;
                const auto base = private_ram ? (hart ? 0x1000c000u : 0x10008000u) : 0x10000000u;
                const auto address = base + (b.rng() % 128)*4;
                const auto mask = b.rng() % 16;
                b.access(hart, address, b.rng(), mask, (b.rng() % 8) == 0);
                if (i % 101 == 0) b.flush(1 + b.rng() % 3);
            }
            b.access(0, 0x20, 0, 0, true); b.access(1, 0x20002000);
            b.access(0, 0x20000000, 65, 1); // MMIO bypass, never allocated.
            b.flush(1); b.flush(2); b.flush(3);
            require(b.ram == b.oracle, "final backing memory mismatch");
            if (ASTER_CACHE_ENABLE)
                require(b.accesses && b.misses && b.interventions && b.invalidations && b.writebacks,
                        "missing coherence transition/event coverage");
            else require(!b.accesses && !b.misses && !b.interventions && !b.invalidations && !b.writebacks,
                         "disabled cache emitted activity");
            requests += b.requests; read += b.reads; write += b.writes; stalls += b.stalled;
            flushes += b.flushes; interventions += b.interventions;
        }
        std::cout << "PASS: coherent cache enabled=" << ASTER_CACHE_ENABLE << " geometry=" << words << "x" << lines
                  << " seed=" << seed << " requests=" << requests << " backing=" << read << "/" << write
                  << " read/write stalled=" << stalls << " flushes=" << flushes << " interventions=" << interventions << '\n';
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n'; return 1;
    }
}
