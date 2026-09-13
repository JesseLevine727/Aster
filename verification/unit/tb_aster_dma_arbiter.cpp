#include "Vaster_dma_arbiter.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <deque>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
struct Transfer {
    bool device, owner, instr;
    std::uint32_t addr, data;
    unsigned mask;
    bool operator==(const Transfer& r) const {
        return device == r.device && owner == r.owner && instr == r.instr &&
               addr == r.addr && data == r.data && mask == r.mask;
    }
};
struct Transaction { std::vector<Transfer> operations; unsigned begin_delay, middle_delay, end_delay; };

// A mock atomic fabric retains busy across 0/1/2 memory operations and arbitrary
// gaps/response settlement. Zero operations model rejected/faulting requests;
// two model an indivisible AMO. The memory oracle checks the exact whole-group
// sequence and never derives legal ordering from the arbiter's private state.
class Bench {
public:
    Vaster_dma_arbiter d;
    std::mt19937 rng;
    std::deque<Transaction> cpus;
    std::deque<Transfer> dmas;
    std::vector<Transfer> expected, actual;
    std::array<unsigned, 2> completed{}, bypassed{};
    Transaction cpu{};
    unsigned cpu_stage = 0, cpu_delay = 0, memory_delay = 0, cycles = 0, stalled = 0, gaps = 0;
    bool cpu_active = false, memory_pending = false, cpu_enabled = true;
    Transfer held{};
    int delay;
    Bench(unsigned seed, int latency) : rng(seed), delay(latency) {
        d.resetn = d.cpu_request = d.cpu_busy = d.cpu_valid = d.dma_valid = d.m_ready = 0;
        d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.resetn = 1;
    }
    Transaction transaction(unsigned kind, unsigned gap) {
        Transaction result{{}, gap, gap, gap};
        bool owner = rng()%2;
        std::uint32_t address = 0x10000000 + 4*(rng()%256);
        for (unsigned n = 0; n < kind; ++n) result.operations.push_back({false,owner,kind == 1 && bool(rng()%2),
            address, std::uint32_t(rng()), kind == 2 ? (n ? 15u : 0u) : unsigned(rng()%16)});
        return result;
    }
    Transfer device() {
        return {true,false,false,std::uint32_t(0x10004000u+4*(rng()%256)),std::uint32_t(rng()),unsigned(rng()%16)};
    }
    void fair(unsigned finished, bool cpu_pending, bool dma_pending) {
        unsigned peer = !finished;
        bypassed[finished] = 0;
        if (peer == 0 ? cpu_pending : dma_pending)
            require(++bypassed[peer] <= 1, "eligible requester bypassed multiple completed groups");
        else bypassed[peer] = 0;
        ++completed[finished];
    }
    void step() {
        d.clk = 0; d.m_ready = 0;
        d.cpu_request = !cpus.empty() && cpu_enabled;
        d.cpu_busy = cpu_active;
        d.cpu_valid = cpu_active && !cpu_delay && cpu_stage < cpu.operations.size();
        const auto current = d.cpu_valid ? cpu.operations[cpu_stage] : Transfer{};
        d.cpu_owner = current.owner; d.cpu_instr = current.instr;
        d.cpu_addr = current.addr; d.cpu_wdata = current.data; d.cpu_wstrb = current.mask;
        d.dma_valid = !dmas.empty();
        const auto dma = d.dma_valid ? dmas.front() : Transfer{};
        d.dma_addr = dma.addr; d.dma_wdata = dma.data; d.dma_wstrb = dma.mask;
        d.eval();
        require(!d.cpu_admit || (!cpu_active && d.cpu_request && !d.m_valid), "invalid/duplicate CPU admission");
        require(!cpu_active || !d.m_device, "DMA interleaved inside CPU/AMO transaction (including gap)");
        if (d.m_valid) {
            Transfer observed{bool(d.m_device),bool(d.m_owner),bool(d.m_instr),d.m_addr,d.m_wdata,d.m_wstrb};
            require(observed == (d.m_device ? dma : current), "request ownership/payload mux mismatch");
            require(d.m_device || d.cpu_valid, "CPU downstream offer without producer");
            if (!memory_pending) {
                memory_pending = true; held = observed;
                memory_delay = delay < 0 ? rng()%73 : unsigned(delay);
            } else require(held == observed, "stalled coherent request stolen/changed");
            if (memory_delay) { --memory_delay; ++stalled; }
            else d.m_ready = 1;
        } else require(!memory_pending, "downstream offer withdrawn under backpressure");
        d.m_rdata = d.m_addr ^ 0x5a17cafe;
        d.eval();
        require(d.cpu_ready == (d.m_valid && !d.m_device && d.m_ready) &&
                d.dma_ready == (d.m_valid && d.m_device && d.m_ready), "response went to wrong requester");
        require(d.cpu_rdata == (d.m_valid && !d.m_device ? d.m_rdata : 0) &&
                d.dma_rdata == (d.m_valid && d.m_device ? d.m_rdata : 0), "response data leak/corruption");
        require(d.busy || (!cpu_active && !d.dma_valid && !d.cpu_admit), "lifecycle busy missed offered/owned request");
        bool cpu_done = false;
        if (d.cpu_admit) {
            cpu_active = true; cpu = cpus.front(); cpus.pop_front(); cpu_stage = 0; cpu_delay = cpu.begin_delay;
            expected.insert(expected.end(), cpu.operations.begin(), cpu.operations.end());
        } else if (cpu_active) {
            if (cpu_delay) { --cpu_delay; ++gaps; }
            else if (cpu_stage == cpu.operations.size()) { cpu_active = false; cpu_done = true; }
            else if (d.cpu_ready) {
                ++cpu_stage;
                cpu_delay = cpu_stage == cpu.operations.size() ? cpu.end_delay : cpu.middle_delay;
            }
        }
        if (cpu_done) fair(0, d.cpu_request, d.dma_valid);
        if (d.dma_ready) {
            require(!cpu_active, "device accepted within whole CPU transaction");
            expected.push_back(dma); dmas.pop_front(); fair(1, d.cpu_request, d.dma_valid);
        }
        if (d.m_valid && d.m_ready) {
            actual.push_back(held); memory_pending = false;
            require(actual.size() <= expected.size() && actual.back() == expected[actual.size()-1],
                    "memory serialization oracle mismatch");
        }
        d.clk = 1; d.eval(); ++cycles;
    }
    void finish() {
        for (unsigned n = 0; n < 2000000 && (!cpus.empty() || !dmas.empty() || cpu_active || d.busy); ++n) step();
        require(cpus.empty() && dmas.empty() && !cpu_active && !d.busy, "arbiter progress timeout");
        require(actual == expected, "missing or duplicate completed memory effects");
        for (unsigned n = 0; n < 4; ++n) step();
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
        for (int delay : {0, 1, 31, -1}) {
            Bench b(seed, delay);
            for (unsigned kind : {0u,1u,2u}) for (unsigned gap : {0u,1u,23u,129u}) {
                for (unsigned n = 0; n < 8; ++n) { b.cpus.push_back(b.transaction(kind,gap)); b.dmas.push_back(b.device()); }
                b.finish();
            }
            // DMA keeps progressing while new CPU admission is paused. An
            // already admitted CPU/AMO must still finish without losing its lock.
            for (unsigned edge = 0; edge < 64; ++edge) {
                b.cpus.push_back(b.transaction(2,7)); b.dmas.push_back(b.device());
                for (unsigned n = 0; n < edge; ++n) b.step();
                b.cpu_enabled = false;
                for (unsigned n = 0; n < 300; ++n) b.step();
                b.cpu_enabled = true; b.finish();
            }
            // Arrival order, asymmetric traffic, transaction kinds and gaps.
            for (unsigned cycle = 0; cycle < 30000; ++cycle) {
                if (b.rng()%19 == 0) b.cpus.push_back(b.transaction(b.rng()%3,b.rng()%17));
                if (b.rng()%23 == 0) b.dmas.push_back(b.device());
                b.step();
            }
            b.finish();
            // Long downstream stalls cannot be repaired by dropping ownership.
            int old_delay = b.delay; b.delay = 1025;
            b.cpus.push_back(b.transaction(2,129)); b.dmas.push_back(b.device()); b.finish(); b.delay = old_delay;
            std::cout << "PASS: DMA arbiter seed=" << seed << " delay=" << delay
                      << " cpu_groups=" << b.completed[0] << " device_groups=" << b.completed[1]
                      << " operations=" << b.actual.size() << " stalls=" << b.stalled << " gaps=" << b.gaps
                      << "; indivisible AMO/response/gap, fault-only groups, round-robin bound, ownership,"
                         " held replies, admission pause and exact serialized memory history\n";
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
