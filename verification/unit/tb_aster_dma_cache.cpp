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
constexpr unsigned words = ASTER_LINE_WORDS, lines = ASTER_LINE_COUNT;
constexpr std::uint32_t base = 0x10000000;
static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static std::uint32_t merge(std::uint32_t old, std::uint32_t value, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b) if (mask & (1u << b))
        old = (old & ~(255u << (8*b))) | (value & (255u << (8*b)));
    return old;
}
struct Line {
    unsigned state;
    std::uint32_t tag;
    std::array<std::uint32_t, words> data;
    bool operator==(const Line& other) const { return state == other.state && tag == other.tag && data == other.data; }
};
struct Transfer {
    bool device, instr;
    unsigned owner, mask;
    std::uint32_t addr, data;
    bool operator==(const Transfer& other) const {
        return device == other.device && instr == other.instr && owner == other.owner &&
               mask == other.mask && addr == other.addr && (!mask || data == other.data);
    }
};
class Bench {
public:
    Vaster_coherent_cache d;
    std::array<std::uint32_t, 16384> ram{}, oracle{};
    std::vector<Transfer> trace;
    std::mt19937 rng;
    int delay;
    bool pending = false;
    Transfer held{};
    unsigned waiting = 0, cpu_ops = 0, dma_ops = 0, stalled = 0, flushes = 0;
    unsigned forwarded = 0, invalidated = 0, drained = 0, commits = 0;
    unsigned cpu_accesses = 0, cpu_misses = 0, cpu_interventions = 0, cpu_invalidations = 0, cpu_writebacks = 0;
    unsigned operation_commits = 0;

    Bench(unsigned seed, int latency) : rng(seed), delay(latency) {
        for (unsigned i = 0; i < ram.size(); ++i) ram[i] = oracle[i] = rng();
        d.resetn = d.s_valid = d.s_device = d.flush_valid = d.m_ready = 0;
        d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.resetn = 1;
    }
    static unsigned index(std::uint32_t addr) {
        require(addr >= base && addr < base+65536 && addr%4 == 0, "backing request escaped aligned RAM");
        return (addr-base)/4;
    }
    std::array<Line, 2*lines> snapshot() const {
        std::array<Line, 2*lines> result{};
        for (unsigned n = 0; n < result.size(); ++n) {
            result[n].state = d.observed_state[n]; result[n].tag = d.observed_tag[n];
            for (unsigned w = 0; w < words; ++w) result[n].data[w] = d.observed_data[n*words+w];
        }
        return result;
    }
    void invariant(bool settled) {
        for (unsigned n = 0; n < lines; ++n) {
            unsigned a = d.observed_state[n], b = d.observed_state[lines+n];
            require(a < 3 && b < 3, "invalid MSI state");
            if (a && b && d.observed_tag[n] == d.observed_tag[lines+n]) {
                require(a == 1 && b == 1, "multiple M / M plus S copies");
                for (unsigned w = 0; w < words; ++w)
                    require(d.observed_data[n*words+w] == d.observed_data[(lines+n)*words+w], "shared copies diverged");
            }
        }
        if (!settled) return;
        for (const auto& line : snapshot()) if (line.state) {
            require(ASTER_CACHE_ENABLE && !(line.tag % (4*words)), "invalid allocated line/tag");
            for (unsigned w = 0; w < words; ++w) {
                unsigned n = index(line.tag + 4*w);
                require(line.data[w] == oracle[n], "cached data is not architectural latest value");
                if (line.state == 1) require(ram[n] == oracle[n], "S line inconsistent with backing RAM");
            }
        }
    }
    void low() {
        d.clk = 0; d.m_ready = 0; d.eval(); invariant(false);
        if (d.m_valid) {
            Transfer offered{bool(d.m_device),bool(d.m_instr),d.m_owner,d.m_wstrb,d.m_addr,d.m_wdata};
            if (!pending) {
                pending = true; held = offered;
                waiting = delay < 0 ? rng()%41 : unsigned(delay);
            } else require(held == offered && held.data == offered.data, "held DMA/maintenance transaction changed");
            if (waiting) { --waiting; ++stalled; }
            else { d.m_ready = 1; d.m_rdata = ram[index(d.m_addr)]; }
        } else require(!pending, "cache withdrew offered backing transaction");
        d.eval();
        if (d.m_valid && d.m_ready) {
            trace.push_back(held); pending = false;
            if (d.m_wstrb) ram[index(d.m_addr)] = merge(d.m_rdata, d.m_wdata, d.m_wstrb);
        }
        require(!d.device_store_commit || (d.m_valid && d.m_ready && d.m_device && d.m_wstrb && !d.device_writeback),
                "architectural DMA commit is not a distinct accepted payload store");
        require(!d.device_writeback || (d.m_valid && d.m_ready && d.m_device && d.m_wstrb == 15),
                "DMA dirty writeback event not tied to actual word acceptance");
        if (d.device_store_commit) {
            require(d.s_device && d.m_addr == d.s_addr && d.m_wstrb == d.s_wstrb && d.m_wdata == d.s_wdata,
                    "payload commit address/data/strobes do not match architectural destination");
            require(++operation_commits == 1, "duplicate DMA architectural store");
            oracle[index(d.s_addr)] = merge(oracle[index(d.s_addr)], d.s_wdata, d.s_wstrb);
        }
        if (d.s_device && d.s_valid)
            require(!d.access_event && !d.miss_event && !d.intervention_event && !d.invalidation_event && !d.writeback_event,
                    "DMA activity incorrectly charged to CPU event bank");
        forwarded += d.device_read_forward; invalidated += d.device_invalidations;
        drained += d.device_writeback; commits += d.device_store_commit;
        cpu_accesses += d.access_event; cpu_misses += d.miss_event; cpu_interventions += d.intervention_event;
        cpu_invalidations += d.invalidation_event; cpu_writebacks += d.writeback_event;
    }
    void rise() { d.clk = 1; d.eval(); invariant(false); }
    void access(bool device, unsigned owner, std::uint32_t address, std::uint32_t value = 0,
                unsigned mask = 0, bool instr = false, bool flush_inflight = false) {
        d.s_valid = 1; d.s_device = device; d.s_owner = owner; d.s_instr = instr;
        d.s_addr = address; d.s_wdata = value; d.s_wstrb = mask;
        const auto expected_read = oracle[index(address)];
        auto expected_cache = snapshot(); auto expected_ram = ram;
        std::vector<Transfer> expected_trace;
        unsigned hit_count = 0, dirty_count = 0;
        const unsigned old_forward = forwarded, old_invalidated = invalidated, old_drained = drained, old_commits = commits;
        if (device) {
            require(!instr && address < base+32768, "invalid device test descriptor");
            for (unsigned n = 0; n < expected_cache.size(); ++n) {
                auto& line = expected_cache[n];
                if (!line.state || line.tag != address-address%(4*words)) continue;
                ++hit_count;
                if (!mask) continue;
                if (line.state == 2) {
                    ++dirty_count;
                    for (unsigned w = 0; w < words; ++w) {
                        expected_trace.push_back({true,false,n/lines,15,line.tag+4*w,line.data[w]});
                        expected_ram[index(line.tag+4*w)] = line.data[w];
                    }
                }
                line.state = 0;
            }
            if (mask || !hit_count) expected_trace.push_back({true,false,owner,mask,address,value});
            if (mask) expected_ram[index(address)] = merge(expected_ram[index(address)], value, mask);
        }
        trace.clear(); operation_commits = 0;
        bool complete = false;
        for (unsigned cycle = 0; cycle < 2000000; ++cycle) {
            if (flush_inflight && cycle == 2) { d.flush_valid = 1; d.flush_mask = 3; }
            low(); bool ready = d.s_ready;
            require(!d.flush_ready, "flush acknowledged before owned device/CPU response");
            if (ready) {
                if (!mask || instr) require(d.s_rdata == expected_read, "CPU/device/RAM-code read returned stale data");
                else if (!device) oracle[index(address)] = merge(expected_read, value, mask);
                else require(operation_commits == 1, "DMA response preceded global write visibility");
            }
            rise();
            if (ready) { complete = true; break; }
        }
        require(complete, "coherent CPU/device transaction made no progress");
        d.s_valid = 0;
        invariant(true);
        if (device) {
            ++dma_ops;
            require(trace == expected_trace && ram == expected_ram, "exact device backing history/whole-RAM oracle mismatch");
            require(snapshot() == expected_cache, "DMA allocated/downgraded/lost unrelated cache state or data");
            require(forwarded-old_forward == unsigned(!mask && hit_count) &&
                    invalidated-old_invalidated == (mask ? hit_count : 0) &&
                    drained-old_drained == dirty_count*words && commits-old_commits == unsigned(mask != 0),
                    "independent device snoop/invalidation/writeback/commit counts mismatch");
        } else {
            ++cpu_ops;
            require(forwarded == old_forward && invalidated == old_invalidated && drained == old_drained && commits == old_commits,
                    "CPU transaction produced DMA events from stale device context");
        }
        if (flush_inflight) flush(3);
    }
    void flush(unsigned mask) {
        d.s_valid = 0; d.flush_valid = 1; d.flush_mask = mask;
        const auto old = snapshot();
        const unsigned old_forward = forwarded, old_invalidated = invalidated, old_drained = drained, old_commits = commits;
        bool complete = false; trace.clear();
        for (unsigned cycle = 0; cycle < 2000000; ++cycle) {
            low(); bool ready = d.flush_ready; require(!d.s_ready, "flush invented response"); rise();
            if (ready) { complete = true; break; }
        }
        require(complete, "flush failed progress"); d.flush_valid = 0; ++flushes;
        const auto now = snapshot();
        for (unsigned n = 0; n < now.size(); ++n)
            require(mask & (1u << (n/lines)) ? !now[n].state : now[n] == old[n], "flush lost peer/retained target");
        require(forwarded == old_forward && invalidated == old_invalidated && drained == old_drained && commits == old_commits,
                "flush falsely emitted DMA events after device job");
        for (const auto& transfer : trace) require(!transfer.device, "global/selective flush retained DMA-origin tag");
        invariant(true);
        if (mask == 3) require(ram == oracle, "full cache flush lost acknowledged CPU/DMA bytes");
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
        for (int delay : {0,1,17,-1}) {
            Bench b(seed, delay);
            for (unsigned owner = 0; owner < 2; ++owner) for (unsigned mask = 1; mask < 16; ++mask) {
                b.flush(3);
                // Fill every dirty neighbor, not merely the DMA target word.
                for (unsigned w = 0; w < words; ++w) b.access(false,owner,base+4*w,0xc0de1234u ^ (w*719+mask),15);
                const auto dirty = b.snapshot();
                if (ASTER_CACHE_ENABLE) require(dirty[owner*lines].state == 2, "dirty-owner setup failed");
                b.access(true,!owner,base); b.access(true,owner,base+4*(words-1));
                b.access(true,!owner,base+4*(words/2),0xa51e7bcd,mask);
                // The existing RAM instruction path must see the device write.
                b.access(false,owner,base+4*(words/2),0,0,true);
                b.access(false,!owner,base+4*(words/2));
                b.flush(3);
                b.access(false,0,base); b.access(false,1,base);
                if (ASTER_CACHE_ENABLE) require(b.snapshot()[0].state == 1 && b.snapshot()[lines].state == 1,
                                               "shared-copy setup failed");
                b.access(true,owner,base); b.access(true,owner,base,0x11223344 ^ mask,mask,false,true);
                b.access(false,0,base); b.access(false,1,base);
            }
            // Dirty aliases in both banks must not be evicted by a device miss.
            b.flush(3);
            b.access(false,0,base,0x12345,15);
            b.access(false,1,base+4*words*lines,0x67890,15);
            b.access(true,0,base+4*words*lines*2);
            b.access(true,0,base+4*words*lines*2,0xcafe,15);
            for (unsigned n = 0; n < 4000; ++n) {
                bool device = b.rng()%3 == 0;
                unsigned owner = b.rng()%2, mask = b.rng()%16;
                auto address = base + 4*std::uint32_t(b.rng()%2048);
                b.access(device,owner,address,b.rng(),mask,!device && b.rng()%11 == 0);
                if (n%97 == 0) b.flush(1+b.rng()%3);
            }
            b.access(true,0,base+32764,0x12345678,15); b.access(false,1,base+32764);
            b.flush(1); b.flush(2); b.flush(3);
            if (ASTER_CACHE_ENABLE) require(b.forwarded && b.invalidated && b.drained, "missing device coherence coverage");
            else require(!b.forwarded && !b.invalidated && !b.drained, "disabled cache claimed DMA snoop maintenance");
            require(b.commits, "missing device commits");
            std::cout << "PASS: DMA cache enabled=" << ASTER_CACHE_ENABLE << " geometry=" << words << "x" << lines
                      << " seed=" << seed << " delay=" << delay << " cpu=" << b.cpu_ops << " device=" << b.dma_ops
                      << " forward=" << b.forwarded << " invalidated=" << b.invalidated << " dirty_words=" << b.drained
                      << " commits=" << b.commits << " flushes=" << b.flushes << " stalls=" << b.stalled
                      << "; latest-byte oracle, M/S/I snoops, whole-line dirty neighbor preservation,"
                         " no device allocation, exact backing history/events, RAM-code visibility, selective/global flush\n";
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
