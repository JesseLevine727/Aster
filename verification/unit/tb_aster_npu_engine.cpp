#include "Vaster_npu_engine.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

#ifndef ASTER_NPU_ROWS
#define ASTER_NPU_ROWS 4
#endif
#ifndef ASTER_NPU_COLS
#define ASTER_NPU_COLS 4
#endif

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

constexpr uint32_t base = 0x10000000u;
constexpr uint32_t limit = 0x10008000u;

static int32_t signed_byte(uint8_t value) { return static_cast<int8_t>(value); }

class Bench {
public:
    Vaster_npu_engine d;
    std::array<uint8_t, limit - base> memory{};
    std::mt19937 random;
    unsigned reads = 0, writes = 0, cycles = 0;

    explicit Bench(uint32_t seed) : random(seed) {
        for (auto& value : memory) value = static_cast<uint8_t>(random());
        d.resetn = 0;
        d.start = d.pause = d.abort_request = d.m_ready = 0;
        d.m_rdata = 0;
        reset_clock();
        d.resetn = 1;
        d.m_ready = 0;
        d.eval();
    }

    void reset_clock() {
        d.clk = 0; d.eval();
        d.clk = 1; d.eval();
    }

    uint32_t word(uint32_t address) const {
        require(address >= base && address <= limit - 4 && !(address & 3),
                "memory oracle received an invalid word address");
        uint32_t value = 0;
        for (unsigned lane = 0; lane < 4; ++lane)
            value |= uint32_t(memory[address - base + lane]) << (8 * lane);
        return value;
    }

    void tick(bool ready = true, unsigned stalls = 0) {
        d.clk = 0;
        d.m_ready = 0;
        d.eval();
        if (d.m_valid) {
            const uint32_t address = d.m_addr;
            d.m_rdata = word(address);
            if (ready && stalls == 0) d.m_ready = 1;
            if (d.m_write) {
                for (unsigned lane = 0; lane < 4; ++lane)
                    if (d.m_wstrb & (1u << lane))
                        memory[address - base + lane] = d.m_wdata >> (8 * lane);
            }
        }
        d.eval();
        if (d.m_valid && d.m_ready) {
            if (d.m_write) ++writes;
            else ++reads;
        }
        d.clk = 1;
        d.eval();
        ++cycles;
    }

    void idle_start() {
        d.start = 1;
        d.eval();
        require(!d.busy, "descriptor start was issued while engine was busy");
        tick();
        d.start = 0;
    }

    void run(uint32_t a, uint32_t b, uint32_t c, uint32_t as, uint32_t bs, uint32_t cs,
             uint32_t m, uint32_t n, uint32_t k) {
        const auto before = memory;
        std::array<uint32_t, 1024> expected{};
        for (unsigned i = 0; i < m; ++i) {
            for (unsigned j = 0; j < n; ++j) {
                int64_t sum = 0;
                for (unsigned x = 0; x < k; ++x) {
                    sum += int64_t(signed_byte(before[a - base + i * as + x])) *
                           int64_t(signed_byte(before[b - base + x * bs + j]));
                }
                expected[i * n + j] = static_cast<uint32_t>(sum);
            }
        }
        const uint32_t sentinel = 0xc35a91e7u;
        for (unsigned i = 0; i < m; ++i)
            for (unsigned j = 0; j < n; ++j)
                for (unsigned byte = 0; byte < 4; ++byte)
                    memory[c - base + i * cs + 4 * j + byte] = sentinel >> (8 * byte);
        auto expected_memory = memory;
        for (unsigned i = 0; i < m; ++i)
            for (unsigned j = 0; j < n; ++j)
                for (unsigned byte = 0; byte < 4; ++byte)
                    expected_memory[c - base + i * cs + 4 * j + byte] =
                        expected[i * n + j] >> (8 * byte);

        d.start_a_base = a; d.start_b_base = b; d.start_c_base = c;
        d.start_a_stride = as; d.start_b_stride = bs; d.start_c_stride = cs;
        d.start_m = m; d.start_n = n; d.start_k = k;
        idle_start();
        for (unsigned guard = 0; guard < 3000000 && d.busy; ++guard) {
            // Deterministic backpressure proves offered read/write fields hold.
            tick(true, (guard % 11 == 3) ? 2 : 0);
        }
        require(!d.busy && d.done && !d.error && !d.aborted, "GEMM did not complete successfully");
        const uint32_t expected_reads = ((n + ASTER_NPU_COLS - 1) / ASTER_NPU_COLS) * m * k +
                                        ((m + ASTER_NPU_ROWS - 1) / ASTER_NPU_ROWS) * n * k;
        require(d.bytes_read == expected_reads && d.bytes_written == m * n * 4,
                "logical NPU byte counters are wrong");
        for (unsigned i = 0; i < m; ++i)
            for (unsigned j = 0; j < n; ++j) {
                const uint32_t address = c + i * cs + 4 * j;
                for (unsigned byte = 0; byte < 4; ++byte) {
                    require(memory[address - base + byte] ==
                                static_cast<uint8_t>(expected[i * n + j] >> (8 * byte)),
                            "RAM-backed GEMM output differs from scalar oracle");
                }
            }
        require(memory == expected_memory, "GEMM modified an input, stride gap, or guard byte");
    }

    void abort_after_first_output_byte() {
        const uint32_t m = ASTER_NPU_ROWS, n = ASTER_NPU_COLS, k = 1;
        const uint32_t as = 1, bs = ASTER_NPU_COLS, cs = ASTER_NPU_COLS * 4 + 4;
        const uint32_t a = base + 12000, b = base + 12100, c = base + 12203;
        for (unsigned i = 0; i < m; ++i) memory[a - base + i] = static_cast<uint8_t>(i + 1);
        for (unsigned i = 0; i < n; ++i) memory[b - base + i] = static_cast<uint8_t>(i + 2);
        for (unsigned i = 0; i < m; ++i)
            for (unsigned j = 0; j < n; ++j)
                for (unsigned byte = 0; byte < 4; ++byte)
                    memory[c - base + i * cs + 4 * j + byte] = 0xa5;
        d.start_a_base = a; d.start_b_base = b; d.start_c_base = c;
        d.start_a_stride = as; d.start_b_stride = bs; d.start_c_stride = cs;
        d.start_m = m; d.start_n = n; d.start_k = k;
        idle_start();
        const unsigned writes_before = writes;
        while (d.busy && !(d.m_valid && d.m_write)) tick();
        require(d.m_valid && d.m_write, "abort fixture never reached C-byte emission");
        tick();
        require(writes == writes_before + 1, "abort fixture did not accept exactly one first output byte");
        d.abort_request = 1;
        for (unsigned guard = 0; guard < 1000000 && d.busy; ++guard) tick();
        d.abort_request = 0;
        require(!d.busy && d.done && d.aborted && !d.error,
                "abort did not terminate with an acknowledged aborted job");
        require(writes == writes_before + m * n * 4 && d.bytes_written == m * n * 4,
                "abort left a partially written active output tile");
        for (unsigned i = 0; i < m; ++i)
            for (unsigned j = 0; j < n; ++j) {
                const uint32_t expected = uint32_t(i + 1) * uint32_t(j + 2);
                for (unsigned byte = 0; byte < 4; ++byte)
                    require(memory[c - base + i * cs + 4 * j + byte] ==
                                static_cast<uint8_t>(expected >> (8 * byte)),
                            "tile-safe abort output differs from the completed tile oracle");
            }
    }
};

static void invalid_descriptor(Bench& bench, uint32_t a, uint32_t b, uint32_t c,
                               uint32_t as, uint32_t bs, uint32_t cs,
                               uint32_t m, uint32_t n, uint32_t k, uint32_t error_code) {
    bench.d.start_a_base = a; bench.d.start_b_base = b; bench.d.start_c_base = c;
    bench.d.start_a_stride = as; bench.d.start_b_stride = bs; bench.d.start_c_stride = cs;
    bench.d.start_m = m; bench.d.start_n = n; bench.d.start_k = k;
    bench.idle_start();
    require(!bench.d.busy && bench.d.done && bench.d.error && bench.d.error_code == error_code,
            "invalid descriptor error was not reported without memory traffic");
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const uint32_t seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
        Bench bench(seed);
        bench.run(base + 1, base + 512, base + 2049, 7, 9, 31, 5, 7, 6);
        bench.run(base + 1024, base + 2048, base + 3073, 4, 5, 19, 1, 1, 1);
        bench.run(base + 4096, base + 4609, base + 5003, 8, 6, 17, 7, 3, 0);
        bench.run(base + 8193, base + 9002, base + 10007, 19, 11, 41, 9, 10, 17);
        bench.abort_after_first_output_byte();
        invalid_descriptor(bench, base, base + 512, base + 1024, 1, 1, 4, 1, 1, 1025, 1);
        invalid_descriptor(bench, base, base + 512, base + 1024, 0, 1, 4, 2, 2, 1, 2);
        invalid_descriptor(bench, base - 1, base + 512, base + 1024, 1, 1, 4, 1, 1, 1, 3);
        invalid_descriptor(bench, base, base + 512, limit - 2, 1, 1, 4, 1, 1, 1, 5);
        invalid_descriptor(bench, base + 100, base + 101, base + 1024, 4, 4, 8, 1, 2, 2, 6);
        std::cout << "PASS: RAM-backed INT8 GEMM engine reads=" << bench.reads
                  << " writes=" << bench.writes << " cycles=" << bench.cycles
                  << "; partial tiles, byte offsets, K=0, stalls, bounds/stride/overlap errors\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
