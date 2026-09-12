#include "Vaster_l1_cache.h"
#include "verilated.h"

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

namespace {
struct StepResult {
    bool cpu_fire;
    std::uint32_t cpu_rdata;
    bool lower_fire;
};

struct CacheTestbench {
    Vaster_l1_cache dut;
    std::map<std::uint32_t, std::uint32_t> memory;
    std::uint64_t lower_reads = 0;
    std::uint64_t lower_writes = 0;
    std::uint64_t accesses = 0;
    std::uint64_t misses = 0;
    unsigned lower_stall_cycles = 0;

    std::uint32_t read_memory(std::uint32_t address) {
        auto found = memory.find(address);
        return found == memory.end() ? 0u : found->second;
    }

    void write_memory(std::uint32_t address, std::uint32_t value,
                      std::uint8_t strobes) {
        std::uint32_t merged = read_memory(address);
        for (unsigned byte = 0; byte < 4; ++byte) {
            if (strobes & (1u << byte)) {
                const std::uint32_t mask = 0xffu << (byte * 8);
                merged = (merged & ~mask) | (value & mask);
            }
        }
        memory[address] = merged;
    }

    StepResult step() {
        dut.clk = 0;
        dut.eval();

        // The lower level is an always-ready word memory. Re-evaluate after
        // observing the cache's combinational request so refill data is
        // available before the rising edge.
        dut.lower_ready = dut.lower_valid && lower_stall_cycles == 0;
        dut.lower_rdata = read_memory(dut.lower_addr);
        dut.eval();

        if (dut.lower_valid && lower_stall_cycles != 0)
            --lower_stall_cycles;

        const StepResult result{
            dut.cpu_valid && dut.cpu_ready,
            dut.cpu_rdata,
            dut.lower_valid && dut.lower_ready,
        };
        if (dut.cache_access)
            ++accesses;
        if (dut.cache_miss)
            ++misses;
        if (result.lower_fire) {
            if (dut.lower_wstrb == 0)
                ++lower_reads;
            else {
                ++lower_writes;
                write_memory(dut.lower_addr, dut.lower_wdata,
                             static_cast<std::uint8_t>(dut.lower_wstrb));
            }
        }

        dut.clk = 1;
        dut.eval();
        return result;
    }

    void reset() {
        dut.rst_n = 0;
        dut.cpu_valid = 0;
        dut.cpu_cacheable = 0;
        dut.cpu_addr = 0;
        dut.cpu_wdata = 0;
        dut.cpu_wstrb = 0;
        dut.lower_ready = 0;
        dut.lower_rdata = 0;
        step();
        step();
        dut.rst_n = 1;
    }

    void stall_lower(unsigned cycles) { lower_stall_cycles = cycles; }

    std::uint32_t read(std::uint32_t address, bool cacheable = true) {
        dut.cpu_valid = 1;
        dut.cpu_cacheable = cacheable;
        dut.cpu_addr = address;
        dut.cpu_wdata = 0;
        dut.cpu_wstrb = 0;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            const StepResult result = step();
            if (result.cpu_fire) {
                dut.cpu_valid = 0;
                dut.eval();
                return result.cpu_rdata;
            }
        }
        throw std::runtime_error("read timed out");
    }

    void write(std::uint32_t address, std::uint32_t value,
               std::uint8_t strobes, bool cacheable = true) {
        dut.cpu_valid = 1;
        dut.cpu_cacheable = cacheable;
        dut.cpu_addr = address;
        dut.cpu_wdata = value;
        dut.cpu_wstrb = strobes;
        for (unsigned cycle = 0; cycle < 100; ++cycle) {
            const StepResult result = step();
            if (result.cpu_fire) {
                dut.cpu_valid = 0;
                dut.eval();
                return;
            }
        }
        throw std::runtime_error("write timed out");
    }
};

void require(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\n";
        std::exit(EXIT_FAILURE);
    }
}
} // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    CacheTestbench test;

    constexpr std::uint32_t first_line = 0x10000000u;
    constexpr std::uint32_t conflict_line = 0x10000100u;
    constexpr std::uint32_t store_miss_line = 0x10000200u;
    constexpr std::uint32_t bypass_address = 0x20000004u;

    test.memory[first_line] = 0xa1000001u;
    test.memory[first_line + 4] = 0xa1000002u;
    test.memory[first_line + 8] = 0xa1000003u;
    test.memory[first_line + 12] = 0xa1000004u;
    test.memory[conflict_line] = 0xb2000001u;
    test.memory[conflict_line + 4] = 0xb2000002u;
    test.memory[conflict_line + 8] = 0xb2000003u;
    test.memory[conflict_line + 12] = 0xb2000004u;
    test.memory[bypass_address] = 0xcafebabeu;

    test.reset();

    // The cache must hold the first refill request until the lower level is
    // ready; two cycles of backpressure must not drop or duplicate a word.
    test.stall_lower(2);
    require(test.read(first_line) == 0xa1000001u,
            "first read returned incorrect refill data");
    require(test.lower_reads == 4, "first read did not fetch one full line");
    require(test.accesses == 1 && test.misses == 1,
            "first read did not produce one cache miss");

    const std::uint64_t reads_after_refill = test.lower_reads;
    require(test.read(first_line + 4) == 0xa1000002u,
            "line hit returned incorrect data");
    require(test.lower_reads == reads_after_refill,
            "line hit unexpectedly accessed lower memory");
    require(test.accesses == 2 && test.misses == 1,
            "line hit changed miss accounting");

    test.write(first_line + 4, 0xdeadbeefu, 0x5u);
    const std::uint32_t merged = (0xa1000002u & ~0x00ff00ffu) |
        (0xdeadbeefu & 0x00ff00ffu);
    require(test.memory[first_line + 4] == merged,
            "write-through store did not update lower memory byte lanes");
    require(test.read(first_line + 4) == merged,
            "write-through store did not update cached byte lanes");
    require(test.lower_writes == 1,
            "cache hit store did not produce one lower-level write");
    require(test.misses == 1, "cache hit store was counted as a miss");

    const std::uint64_t reads_before_conflict = test.lower_reads;
    require(test.read(conflict_line) == 0xb2000001u,
            "conflicting line returned incorrect data");
    require(test.lower_reads == reads_before_conflict + 4,
            "conflicting line did not refill four words");
    require(test.read(first_line + 4) == merged,
            "evicted line did not refill updated lower-memory data");
    require(test.lower_reads == reads_before_conflict + 8,
            "eviction did not cause a second line refill");

    const std::uint64_t misses_before_store_miss = test.misses;
    test.write(store_miss_line + 4, 0x55667788u, 0xfu);
    require(test.memory[store_miss_line + 4] == 0x55667788u,
            "store miss did not reach lower memory");
    require(test.misses == misses_before_store_miss + 1,
            "store miss was not counted");
    const std::uint64_t reads_before_store_miss_load = test.lower_reads;
    require(test.read(store_miss_line + 4) == 0x55667788u,
            "store miss incorrectly allocated stale cache data");
    require(test.lower_reads == reads_before_store_miss_load + 4,
            "load after no-write-allocate store did not refill a line");

    const std::uint64_t accesses_before_bypass = test.accesses;
    const std::uint64_t misses_before_bypass = test.misses;
    require(test.read(bypass_address, false) == 0xcafebabeu,
            "uncached bypass read returned incorrect data");
    require(test.lower_reads == reads_before_store_miss_load + 5,
            "uncached read did not issue exactly one lower transaction");
    require(test.accesses == accesses_before_bypass &&
                test.misses == misses_before_bypass,
            "uncached access polluted cache event counters");

    std::cout << "PASS: L1 cache refill, hit, byte-write, eviction and bypass tests\n";
    return EXIT_SUCCESS;
}
