#include "Vaster_dma_engine.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
constexpr std::uint32_t base = 0x10000000, limit = 0x10008000;
struct Transfer { std::uint32_t address, data; unsigned mask; };
static unsigned popcount(unsigned x) { unsigned n = 0; for (; x; x >>= 1) n += x & 1; return n; }
static std::uint32_t merge(std::uint32_t old, std::uint32_t value, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b) if (mask & (1u << b))
        old = (old & ~(255u << (8*b))) | (value & (255u << (8*b)));
    return old;
}
static unsigned descriptor_error(std::uint32_t s, std::uint32_t d, std::uint32_t n) {
    if (!n) return 0;
    if (s < base || std::uint64_t(s) + n > limit) return 1;
    if (d < base || std::uint64_t(d) + n > limit) return 2;
    if (std::uint64_t(s) < std::uint64_t(d) + n && std::uint64_t(d) < std::uint64_t(s) + n) return 3;
    return 0;
}

// Byte-addressed independent memory and descriptor/status oracle. No DUT private
// state, RTL helper or firmware copy routine is consulted to construct results.
class Bench {
public:
    Vaster_dma_engine d;
    std::array<std::uint8_t, limit-base> memory{}, before{}, expected{};
    std::mt19937 rng;
    std::vector<Transfer> plan;
    std::array<std::uint32_t, 3> config{};
    std::uint64_t cycles = 0, job_cycles = 0;
    unsigned status = 0, error = 0, bytes = 0, position = 0, waiting = 0;
    unsigned jobs = 0, reads = 0, writes = 0, successes = 0, aborts = 0, errors = 0, rejects = 0;
    unsigned stalls = 0, paused = 0;
    bool pending = false, abort_pending = false, force_hold = false;
    int delay;
    Transfer held{};

    explicit Bench(unsigned seed, int latency) : rng(seed), delay(latency) {
        for (auto& byte : memory) byte = rng();
        d.resetn = d.cfg_valid = d.cfg_owner = d.cfg_addr = d.cfg_wdata = d.cfg_wstrb = 0;
        d.pause = d.abort_request = d.m_ready = d.m_rdata = 0;
        reset();
    }
    void reset() {
        d.resetn = 0; d.clk = 0; d.eval(); d.clk = 1; d.eval();
        status = error = bytes = position = 0; job_cycles = 0; config.fill(0);
        pending = abort_pending = false; plan.clear();
        d.resetn = 1; d.cfg_valid = 0; d.cfg_wstrb = 0; d.pause = d.abort_request = 0;
        d.m_ready = 0; d.clk = 0; d.eval();
        check();
    }
    std::uint32_t word(std::uint32_t address) const {
        require(address >= base && address <= limit-4 && !(address & 3), "bad oracle word read");
        std::uint32_t result = 0;
        for (unsigned b = 0; b < 4; ++b) result |= std::uint32_t(memory[address-base+b]) << (8*b);
        return result;
    }
    void prepare() {
        before = memory; expected = before; plan.clear(); position = 0;
        if (descriptor_error(config[0], config[1], config[2])) return;
        if (!config[2]) return;
        std::copy_n(before.begin() + config[0]-base, config[2], expected.begin() + config[1]-base);
        unsigned copied = 0;
        while (copied < config[2]) {
            auto src = config[0]+copied, dst = config[1]+copied;
            unsigned width = !(src % 4) && !(dst % 4) && config[2]-copied >= 4 ? 4 : 1;
            plan.push_back({src & ~3u, 0, 0});
            unsigned mask = 0;
            std::uint32_t data = 0;
            for (unsigned b = 0; b < width; ++b) {
                unsigned lane = (dst+b) % 4;
                mask |= 1u << lane;
                data |= std::uint32_t(before[src-base+b]) << (8*lane);
            }
            plan.push_back({dst & ~3u, data, mask});
            copied += width;
        }
    }
    std::uint32_t register_value(unsigned address) const {
        switch (address) {
            case 0: return config[0]; case 4: return config[1]; case 8: return config[2];
            case 0x10: return status; case 0x14: return bytes; case 0x18: return error;
            case 0x1c: return 1; case 0x20: return job_cycles; case 0x24: return job_cycles >> 32;
            case 0x28: return base; case 0x2c: return limit; default: return 0;
        }
    }
    void check() {
        require(d.busy == bool(status & 1) && d.completion == bool(status & 2), "busy/completion level mismatch");
        require(d.status == status && d.error_code == error, "sticky status/error oracle mismatch");
        require(d.bytes_done == bytes && d.job_cycles == job_cycles, "byte or busy-edge cycle accounting mismatch");
        require(d.cfg_rdata == register_value(d.cfg_addr), "register readback/unsupported offset mismatch");
    }
    void check_prefix() {
        auto prefix = before;
        if (bytes) std::copy_n(before.begin()+config[0]-base, bytes, prefix.begin()+config[1]-base);
        require(memory == prefix, "complete RAM/prefix/guard oracle mismatch");
        if (!(status & 12)) require(memory == expected, "successful copy did not match full-byte oracle");
    }
    void step() {
        d.clk = 0; d.m_ready = 0; d.eval(); check();
        const bool was_busy = status & 1;
        const bool cfg = d.cfg_valid && !d.cfg_owner && d.cfg_wstrb &&
                         (d.cfg_addr == 0 || d.cfg_addr == 4 || d.cfg_addr == 8);
        const bool cmd = d.cfg_valid && !d.cfg_owner && (d.cfg_wstrb & 1) && d.cfg_addr == 12;
        unsigned command = d.cfg_wdata & 255;
        const bool start = cmd && command == 1;
        const bool accepted = start && !was_busy && !d.pause && !d.abort_request;
        const bool abort_now = abort_pending || d.abort_request || (cmd && command == 2);
        const bool rejected = (cfg && was_busy) || (cmd && command != 1 && command != 2 && command != 4) ||
                              (start && !accepted) || (cmd && command == 4 && was_busy);
        if (d.m_valid) {
            require(was_busy && position < plan.size(), "unsolicited/excess memory transaction");
            const auto& want = plan[position];
            require(d.m_addr == want.address && d.m_wdata == want.data && d.m_wstrb == want.mask,
                    "copy sequence/address/read data/byte strobe mismatch");
            if (!pending) {
                pending = true; held = {d.m_addr, d.m_wdata, d.m_wstrb};
                waiting = delay < 0 ? rng() % 37 : unsigned(delay);
            } else require(d.m_addr == held.address && d.m_wdata == held.data && d.m_wstrb == held.mask,
                           "held request payload changed");
            d.m_rdata = word(d.m_addr);
            if (!force_hold && !waiting) d.m_ready = 1;
            else { if (waiting) --waiting; ++stalls; }
        } else require(!pending, "offered transaction withdrawn without response");
        d.eval();
        const bool read = d.m_valid && d.m_ready && !d.m_wstrb;
        const bool write = d.m_valid && d.m_ready && d.m_wstrb;
        const unsigned byte_increment = write ? popcount(d.m_wstrb) : 0;
        const bool aborted = was_busy && abort_now && (!d.m_valid || d.m_ready);
        const bool success = (accepted && !config[2]) ||
                             (write && !abort_now && bytes+byte_increment == config[2]);
        const bool failed = accepted && descriptor_error(config[0], config[1], config[2]);
        require(d.event_read == read && d.event_write == write && d.event_success == success &&
                d.event_abort == aborted && d.event_error == failed && d.event_reject == rejected,
                "independent completion/error/reject/transaction event oracle mismatch");
        if (was_busy) { ++job_cycles; if (abort_now) abort_pending = true; }
        if (rejected) { status |= 16; ++rejects; }
        if (cfg && !was_busy) config[d.cfg_addr/4] = merge(config[d.cfg_addr/4], d.cfg_wdata, d.cfg_wstrb);
        if (cmd && command == 4 && !was_busy) { status = 0; error = 0; }
        if (accepted) {
            ++jobs; job_cycles = 0; bytes = 0; error = descriptor_error(config[0], config[1], config[2]);
            status = !config[2] ? 2 : error ? 6 : 1; abort_pending = false; prepare();
        }
        if (read || write) { pending = false; ++position; }
        if (read) ++reads;
        if (write) {
            for (unsigned b = 0; b < 4; ++b) if (d.m_wstrb & (1u << b))
                memory[d.m_addr-base+b] = d.m_wdata >> (8*b);
            bytes += byte_increment; ++writes;
        }
        if (success) { ++successes; status = (status & 16) | 2; abort_pending = false; }
        if (aborted) { ++aborts; status = (status & 16) | 10; abort_pending = false; }
        if (failed) ++errors;
        if (d.pause && was_busy) ++paused;
        d.clk = 1; d.eval(); ++cycles;
        check();
        if (success || aborted || failed) check_prefix();
        d.cfg_valid = 0; d.cfg_wstrb = 0;
    }
    void wr(unsigned address, std::uint32_t data, unsigned mask = 15, unsigned owner = 0) {
        d.cfg_valid = 1; d.cfg_owner = owner; d.cfg_addr = address; d.cfg_wdata = data; d.cfg_wstrb = mask; step();
    }
    void descriptor(std::uint32_t src, std::uint32_t dst, std::uint32_t size) {
        require(!(status & 1), "test attempted idle descriptor setup while busy");
        wr(0, src); wr(4, dst); wr(8, size);
    }
    void finish() {
        for (unsigned n = 0; n < 4000000 && d.busy; ++n) step();
        require(!d.busy && d.completion, "DMA failed bounded test progress");
        for (unsigned n = 0; n < 4; ++n) step(); // held completion must not repeat events/effects
        check_prefix();
    }
    void run(std::uint32_t src, std::uint32_t dst, std::uint32_t size) {
        descriptor(src, dst, size); wr(12, 1); finish();
    }
};

static unsigned directed(Bench& b) {
    unsigned cases = 0;
    const std::vector<unsigned> sizes{0,1,2,3,4,7,8,15,16,31,32,63,64,127,128,255,256,511,512,1024,2048,4096,8192};
    for (unsigned s = 0; s < 4; ++s) for (unsigned d = 0; d < 4; ++d) for (auto n : sizes) {
        b.run(base+128+s, base+16384+d, n); ++cases;
    }
    b.run(base, base+16384, 16384); ++cases;
    b.run(limit-1, base, 1); ++cases;
    b.run(base, limit-1, 1); ++cases;
    b.run(base+4, base, 4); ++cases; // adjacent non-overlapping reverse copy
    b.run(base, base+4, 4); ++cases;
    const std::vector<std::array<std::uint32_t, 3>> invalid{
        {0, 0xffffffff, 0}, {0xffffffff, 0, 0},
        {base, base, 1}, {base, base+1, 4}, {base+1, base, 4},
        {base, base+4, 8}, {base+4, base, 8},
        {base-1, base+100, 1}, {base+100, base-1, 1},
        {limit, base, 1}, {base, limit, 1}, {limit-1, base, 2}, {base, limit-1, 2},
        {0xfffffffcu, base, 8}, {base, 0xfffffffcu, 8},
        {base, base+100, 0xffffffffu}, {0, 0, 0xffffffffu},
        {0x20000000, base, 4}, {base, 0x30000000, 4},
        {0x10008000, base, 4}, {base, 0x1000c000, 4}, {base+1, base+100, 0x8000},
    };
    for (const auto& x : invalid) { b.run(x[0], x[1], x[2]); ++cases; }
    b.wr(12, 4); require(!b.status && !b.error, "ACK failed");
    for (unsigned address : {0u,4u,8u}) for (unsigned mask = 0; mask < 16; ++mask) {
        b.wr(address, b.rng(), mask); b.wr(address, b.rng(), mask, 1); ++cases;
    }
    for (unsigned offset = 0; offset < 4096; ++offset) {
        // Only aligned writable ABI addresses have effects; probe all remaining offsets.
        if (offset == 0 || offset == 4 || offset == 8 || offset == 12) continue;
        b.wr(offset, b.rng()); ++cases;
    }
    b.descriptor(base+32, base+256, 16);
    for (unsigned mask = 0; mask < 16; ++mask) {
        b.wr(12, 1, mask, 1); // no secondary writes, even to valid commands
        if (!(mask & 1)) { b.wr(12, 1, mask); require(!b.d.busy, "upper command lane started DMA"); }
        ++cases;
    }
    for (unsigned command = 0; command < 256; ++command) {
        if (command == 1 || command == 2 || command == 4) continue;
        b.wr(12, command); require(b.status == 16, "invalid command had effects"); b.wr(12, 4); ++cases;
    }
    b.wr(12, 0xffffff01); b.finish(); ++cases; // upper data bytes are not command bits
    b.wr(12, 2); require(b.status == 2, "idle ABORT destroyed completed status");
    b.d.pause = 1; b.wr(12, 1); require(b.status == 18, "paused START not rejected");
    b.d.pause = 0; b.d.abort_request = 1; b.wr(12, 1); b.d.abort_request = 0; ++cases;
    // Hold the very first read while hostile descriptor/control writes arrive.
    b.wr(12, 1); b.force_hold = true;
    while (!b.d.m_valid) b.step();
    for (unsigned owner = 0; owner < 2; ++owner) for (unsigned mask = 0; mask < 16; ++mask) {
        for (unsigned address : {0u,4u,8u}) b.wr(address, b.rng(), mask, owner);
        for (unsigned command : {0u,1u,3u,4u,5u,255u}) b.wr(12, command, mask, owner);
        ++cases;
    }
    b.force_hold = false; b.finish();
    require(b.bytes == 16 && b.status == 18, "busy writes altered job or failed sticky rejection");
    b.wr(12, 1); b.finish(); require(b.status == 2, "new START did not clear previous rejection"); ++cases;
    return cases;
}

static unsigned lifecycle(Bench& b) {
    unsigned cases = 0;
    const int old_delay = b.delay;
    for (int latency : {0, 3, 19}) for (unsigned alignment : {0u,1u}) {
        b.delay = latency;
        // Enumerate every edge of short aligned and unaligned copies. This hits
        // pre-offer, waiting read, captured read, waiting write and terminal write.
        const unsigned edges = 130;
        for (unsigned edge = 0; edge < edges; ++edge) for (bool external : {false,true}) {
            b.descriptor(base+32+alignment, base+512, 13); b.wr(12, 1);
            for (unsigned t = 0; t < edge && b.d.busy; ++t) b.step();
            if (external) { b.d.abort_request = 1; b.step(); b.d.abort_request = 0; }
            else b.wr(12, 2);
            b.finish(); ++cases;
        }
        for (unsigned edge = 0; edge < edges; ++edge) {
            b.descriptor(base+32+alignment, base+512, 13); b.wr(12, 1);
            for (unsigned t = 0; t < edge && b.d.busy; ++t) b.step();
            b.d.pause = 1;
            // An outstanding request may drain but a captured pending write is
            // retained. Pausing does not imply completion/abort of the whole job.
            for (unsigned t = 0; t < unsigned(latency)+4; ++t) b.step();
            auto bytes = b.bytes; auto position = b.position;
            for (unsigned t = 0; t < 29; ++t) b.step();
            require(b.bytes == bytes && b.position == position && !b.d.m_valid, "pause admitted a new offer");
            b.d.pause = 0; b.finish(); require(b.bytes == 13 && b.status == 2, "pause lost captured data/progress"); ++cases;
        }
    }
    b.delay = 0;
    // Testbench-only state deposit into the top-level sequential counter avoids
    // billions of clocks. Verify both carry and modulo-64-bit rollover, with
    // live low/high register reads. No synthetic event or RTL test mode is used.
    for (std::uint64_t initial : {std::uint64_t(0xfffffff0), ~std::uint64_t(0)-15}) {
        b.descriptor(base+32, base+512, 4); b.wr(12, 1);
        while (!b.d.m_valid) b.step();
        b.force_hold = true; b.job_cycles = initial; b.d.job_cycles = initial;
        for (unsigned n = 0; n < 37; ++n) b.wr(n%2 ? 0x20 : 0x24, 0, 0);
        b.force_hold = false; b.finish(); ++cases;
    }
    for (bool write : {false, true}) {
        b.descriptor(base+32, base+512, 4); b.wr(12, 1);
        while (!b.d.m_valid || bool(b.d.m_wstrb) != write) b.step();
        b.force_hold = true; b.d.pause = b.d.abort_request = 1;
        for (unsigned n = 0; n < 1025; ++n) b.step();
        require(b.d.busy && b.d.m_valid && !b.d.completion, "abort pretended to recover a stalled responder");
        b.d.abort_request = 0; b.force_hold = false; b.finish();
        require(b.status == 10 && b.bytes == (write ? 4u : 0u), "abort prefix/drain or terminal priority wrong");
        b.d.pause = 0; ++cases;
    }
    // Explicit destructive POR at each stage is not the cooperative STOP ABI.
    for (unsigned edge = 0; edge < 50; ++edge) {
        b.delay = 3; b.descriptor(base+33, base+512, 17); b.wr(12, 1);
        for (unsigned t = 0; t < edge; ++t) b.step();
        b.reset(); b.run(base+32, base+512, 17); ++cases;
    }
    b.delay = old_delay;
    return cases;
}

static unsigned randomized(Bench& b) {
    for (unsigned trial = 0; trial < 256; ++trial) {
        unsigned n = b.rng() % 1025, src = 64 + b.rng() % 3000, dst = 17000 + b.rng() % 3000;
        b.descriptor(base+src, base+dst, n); b.wr(12, 1);
        for (unsigned t = 0; b.d.busy && t < 200000; ++t) {
            b.d.pause = b.rng() % 7 == 0;
            b.d.abort_request = b.rng() % 10007 == 0;
            unsigned kind = b.rng() % 53;
            if (kind == 0) b.wr(4*(b.rng()%3), b.rng(), b.rng()%16, b.rng()%2);
            else if (kind == 1) b.wr(12, b.rng()%256, b.rng()%16, b.rng()%2);
            else b.step();
        }
        b.d.pause = b.d.abort_request = 0; b.finish();
    }
    return 256;
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
        for (int delay : {0, 7, -1}) {
            Bench b(seed, delay);
            unsigned cases = directed(b) + lifecycle(b) + randomized(b);
            std::cout << "PASS: DMA engine seed=" << seed << " delay=" << delay << " cases=" << cases
                      << " jobs=" << b.jobs << " reads=" << b.reads << " writes=" << b.writes
                      << " success=" << b.successes << " aborted=" << b.aborts << " errors=" << b.errors
                      << " rejected=" << b.rejects << " stalls=" << b.stalls << " paused=" << b.paused
                      << "; full RAM/guards, 16 alignments, 23 sizes, permissions/wrap/overlap, register lanes,"
                         " exactly-once events, held replies, pause/drain, abort priority, restart/POR\n";
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
