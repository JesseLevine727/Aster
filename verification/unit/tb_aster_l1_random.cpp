#include "Vaster_l1_cache.h"
#include "verilated.h"

#include <cstdint>
#include <deque>
#include <iostream>
#include <map>
#include <random>
#include <stdexcept>
#include <vector>

constexpr unsigned Words = ASTER_LINE_WORDS;
constexpr unsigned Lines = ASTER_LINE_COUNT;
constexpr unsigned LineBytes = Words*4;
constexpr unsigned Capacity = LineBytes*Lines;
constexpr std::uint32_t Ram = 0x10000000, Mmio = 0x20000000;

static void require(bool good, const char* message) {
    if (!good) throw std::runtime_error(message);
}

struct Request {
    std::uint32_t address, data = 0;
    unsigned mask = 0;
    bool cacheable = true;
};

class Scoreboard {
    Vaster_l1_cache dut;
    std::mt19937 random;
    // Architectural memory and the actual serviced backing memory are separate.
    std::map<std::uint32_t, std::uint32_t> reference_memory, backing_memory;
    std::vector<std::uint32_t> tags = std::vector<std::uint32_t>(Lines);
    std::vector<bool> valid = std::vector<bool>(Lines);
    std::deque<Request> expected_beats;
    Request request{}, held{};
    bool active = false, lower_pending = false, hit = false;
    unsigned delay = 0, mmio_reads = 0, expected_mmio_reads = 0;
    std::uint64_t accesses = 0, misses = 0, expected_accesses = 0, expected_misses = 0;

    static std::uint32_t initial(std::uint32_t address) {
        const auto x = (address / 4) * 0x9e3779b9u;
        return x ^ (x >> 16) ^ 0xa57ecafeu;
    }
    static std::uint32_t read(const std::map<std::uint32_t, std::uint32_t>& memory,
                              std::uint32_t address) {
        auto found = memory.find(address);
        return found == memory.end() ? initial(address) : found->second;
    }
    static unsigned index(std::uint32_t address) { return address / LineBytes % Lines; }
    static std::uint32_t tag(std::uint32_t address) { return address / Capacity; }

public:
    unsigned completed = 0, aborted = 0, stalls = 0, lower_reads = 0, lower_writes = 0;
    unsigned read_hits = 0, read_misses = 0, write_hits = 0, write_misses = 0, bypasses = 0;
    unsigned mask_coverage = 0;
    explicit Scoreboard(unsigned seed) : random(seed) { reset(); }

    void begin(Request next) {
        require(!active && expected_beats.empty() && !lower_pending, "test issued overlapping requests");
        request = next;
        active = true;
        hit = next.cacheable && valid[index(next.address)] && tags[index(next.address)] == tag(next.address);
        if (next.cacheable && !hit) ++expected_misses;
        if (!next.cacheable || next.mask) expected_beats.push_back(next);
        else if (!hit) for (unsigned word = 0; word < Words; ++word)
            expected_beats.push_back({next.address / LineBytes * LineBytes + word*4});
        dut.cpu_valid = 1;
        dut.cpu_cacheable = next.cacheable;
        dut.cpu_addr = next.address;
        dut.cpu_wdata = next.data;
        dut.cpu_wstrb = next.mask;
    }

    // Return true only for a CPU completion. Every lower beat must match the
    // independent direct-mapped model's exact address/data/strobe sequence.
    bool step(bool force_stall = false) {
        dut.clk = 0; dut.lower_ready = 0; dut.eval();
        if (lower_pending) require(dut.lower_valid && held.address == dut.lower_addr &&
            held.data == dut.lower_wdata && held.mask == dut.lower_wstrb,
            "lower request changed/withdrew under backpressure");
        if (dut.lower_valid) {
            require(active && !expected_beats.empty(), "unexpected/duplicate lower transaction");
            const auto expected = expected_beats.front();
            require(dut.lower_addr == expected.address && dut.lower_wstrb == expected.mask &&
                    (!expected.mask || dut.lower_wdata == expected.data), "incorrect lower beat");
            if (!lower_pending) {
                held = {dut.lower_addr, dut.lower_wdata, dut.lower_wstrb};
                lower_pending = true;
                delay = random() % 8;
            }
            dut.lower_ready = !force_stall && delay == 0;
            dut.lower_rdata = dut.lower_ready
                ? (held.address >= Mmio ? 0x600d0000u ^ (held.address/4) ^ mmio_reads
                                        : read(backing_memory, held.address))
                : random(); // poison unaccepted refill data
            if (!dut.lower_ready) { ++stalls; if (delay) --delay; }
        } else {
            // Ready is allowed high in idle; it is not a transaction by itself.
            dut.lower_ready = random() & 1;
            dut.lower_rdata = random();
        }
        dut.eval();
        const bool cpu_fire = dut.cpu_valid && dut.cpu_ready;
        require(!cpu_fire || active, "unexpected/duplicate CPU response");
        require(bool(dut.cache_access) == (cpu_fire && request.cacheable), "cache-access pulse mismatch");
        accesses += dut.cache_access;
        misses += dut.cache_miss;
        if (dut.lower_valid && dut.lower_ready) {
            if (held.mask) {
                ++lower_writes;
                auto value = read(backing_memory, held.address);
                for (unsigned lane = 0; lane < 4; ++lane) if (held.mask & (1u << lane)) {
                    const auto byte = (held.data >> (lane*8)) & 255u;
                    value = (value & ~(255u << (lane*8))) | (byte << (lane*8));
                }
                backing_memory[held.address] = value;
            } else { ++lower_reads; if (held.address >= Mmio) ++mmio_reads; }
            expected_beats.pop_front();
            lower_pending = false;
        }
        if (cpu_fire) {
            require(expected_beats.empty(), "CPU completed before all lower beats");
            if (request.mask) {
                std::uint32_t mask = 0;
                for (unsigned lane = 0; lane < 4; ++lane) if (request.mask & (1u << lane))
                    mask |= 0xffu << (lane*8);
                reference_memory[request.address] = (read(reference_memory, request.address) & ~mask) |
                                                    (request.data & mask);
                require(read(backing_memory, request.address) == reference_memory[request.address],
                        "write-through byte merge disagrees with architectural memory");
                mask_coverage |= 1u << request.mask;
            } else {
                const auto expected = request.address >= Mmio
                    ? 0x600d0000u ^ (request.address/4) ^ expected_mmio_reads++
                    : read(reference_memory, request.address);
                require(dut.cpu_rdata == expected, "cache data differs from architectural memory");
                if (request.cacheable) { valid[index(request.address)] = true; tags[index(request.address)] = tag(request.address); }
            }
            if (!request.cacheable) ++bypasses;
            else if (request.mask) { if (hit) ++write_hits; else ++write_misses; }
            else { if (hit) ++read_hits; else ++read_misses; }
            expected_accesses += request.cacheable;
            require(accesses == expected_accesses && misses == expected_misses, "access/miss totals differ from model");
            active = false;
            ++completed;
        }
        dut.clk = 1; dut.eval();
        return cpu_fire;
    }

    void transfer(Request next) {
        begin(next);
        for (unsigned cycle = 0; cycle < Words*16 + 100; ++cycle) if (step()) return;
        throw std::runtime_error("cache transfer timed out");
        // cpu_valid stays high between calls, including identical requests.
    }

    void idle() {
        dut.cpu_valid = 0;
        for (unsigned i = 0; i < 3; ++i) step();
    }

    void reset() {
        if (active) ++aborted;
        // Hold a request and ready high while resetting: no transaction may leak.
        dut.rst_n = 0;
        dut.lower_ready = 1;
        for (unsigned i = 0; i < 3; ++i) {
            dut.clk = 0; dut.eval();
            require(!dut.cpu_ready && !dut.lower_valid && !dut.cache_access, "activity during reset");
            dut.clk = 1; dut.eval();
            require(!dut.cache_miss, "miss pulse survived reset");
        }
        valid.assign(Lines, false);
        expected_beats.clear();
        active = lower_pending = false;
        accesses = misses = expected_accesses = expected_misses = 0;
        dut.cpu_valid = 0;
        dut.rst_n = 1;
        idle();
    }

    void abort_after_beats(Request next, unsigned beats) {
        begin(next);
        const auto initial_beats = expected_beats.size();
        for (unsigned cycle = 0; cycle < Words*16 + 100; ++cycle) {
            if (initial_beats - expected_beats.size() == beats && cycle > 0) {
                for (unsigned i = 0; i < 4 && !expected_beats.empty(); ++i)
                    require(!step(true), "stalled request completed before reset");
                reset();
                transfer({Ram}); // a reset must invalidate even old/partially overwritten lines
                return;
            }
            require(!step(beats == 0), "request completed before intended reset point");
        }
        throw std::runtime_error("could not reach reset injection point");
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 0xa57e;
        Scoreboard test(seed);
        std::mt19937 random(seed ^ 0x12345678);
        test.transfer({Ram});
        for (unsigned i = 0; i < 16; ++i) test.transfer({Ram}); // identical back-to-back hits
        for (unsigned mask = 1; mask < 16; ++mask) {
            test.transfer({Ram, std::uint32_t(random()), mask});
            test.transfer({Ram});
            test.transfer({Ram + 2*Capacity, std::uint32_t(random()), mask}); // no write allocate
            test.transfer({Ram}); // a store miss must not evict the resident conflicting line
        }
        test.transfer({Ram + Capacity});
        test.transfer({Ram});
        for (unsigned i = 0; i < 8; ++i) test.transfer({Mmio, 0, 0, false});
        test.abort_after_beats({Ram + Capacity}, 0);
        test.abort_after_beats({Ram + Capacity}, Words/2);
        test.abort_after_beats({Ram + Capacity}, Words); // reset in RESPONSE, before CPU acceptance
        test.abort_after_beats({Ram, 0xdeadbeef, 15}, 0);
        test.abort_after_beats({Mmio, 0xdeadbeef, 5, false}, 0);
        for (unsigned iteration = 0; iteration < 5000; ++iteration) {
            const bool cacheable = random() % 8 != 0;
            const auto address = cacheable
                ? Ram + 4*(random() % (Words*Lines*(iteration % 2 ? 8 : 1)))
                : Mmio + 4*(random() % 16);
            const unsigned mask = random() % 3 == 0 ? 1 + random() % 15 : 0;
            test.transfer({std::uint32_t(address), std::uint32_t(random()), mask, cacheable});
            if (iteration % 97 == 0) test.idle();
            if (iteration % 503 == 0) test.reset();
        }
        test.idle();
        require(test.mask_coverage == 0xfffe && test.aborted == 5 && test.stalls &&
                test.read_hits && test.read_misses && test.write_hits && test.write_misses && test.bypasses,
                "required cache scenario coverage missing");
        std::cout << "PASS: cache scoreboard words=" << Words << " lines=" << Lines << " seed=" << seed
                  << " completed=" << test.completed << " aborted=" << test.aborted
                  << " stalls=" << test.stalls << " reads=" << test.lower_reads
                  << " writes=" << test.lower_writes << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
