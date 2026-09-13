#include "Vaster_int8_array.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

static uint32_t mac(uint32_t acc, uint8_t a, uint8_t b) {
    return acc + static_cast<uint32_t>(static_cast<int32_t>(static_cast<int8_t>(a)) *
                                       static_cast<int32_t>(static_cast<int8_t>(b)));
}

class Rig {
public:
    Vaster_int8_array d;

    void eval() { d.eval(); }

    void tick() {
        d.clk = 0;
        eval();
        d.clk = 1;
        eval();
    }

    void reset() {
        d.resetn = 0;
        d.start_tile = d.step_valid = d.finish_tile = 0;
        d.a_row[0] = d.a_row[1] = d.a_row[2] = d.a_row[3] = 0;
        d.b_col[0] = d.b_col[1] = d.b_col[2] = d.b_col[3] = 0;
        d.row_mask = d.col_mask = 0;
        tick();
        require(!d.start_ready && !d.step_ready && !d.finish_ready && !d.result_valid,
                "array was active during reset");
        d.resetn = 1;
        eval();
        require(d.start_ready && !d.step_ready && !d.finish_ready,
                "array did not return idle after reset");
    }

    void start() {
        d.start_tile = 1;
        eval();
        require(d.start_ready && d.start_accept, "tile start was not accepted");
        tick();
        d.start_tile = 0;
        eval();
        require(!d.start_accept && d.step_ready && !d.result_valid,
                "array did not enter active tile state");
    }

    void step(const std::array<uint8_t, 4>& a, const std::array<uint8_t, 4>& b,
              uint8_t rows = 0xf, uint8_t cols = 0xf) {
        for (unsigned i = 0; i < 4; ++i) {
            d.a_row[i] = a[i];
            d.b_col[i] = b[i];
        }
        d.row_mask = rows;
        d.col_mask = cols;
        d.step_valid = 1;
        eval();
        require(d.step_ready && d.step_accept, "tile step was not accepted");
        tick();
        d.step_valid = 0;
        eval();
        require(d.step_ready && !d.step_accept && !d.result_valid,
                "tile step did not return to ready state");
    }

    std::array<uint32_t, 16> finish() {
        d.finish_tile = 1;
        eval();
        require(d.finish_ready && d.finish_accept, "tile finish was not accepted");
        tick();
        d.finish_tile = 0;
        eval();
        require(!d.step_ready && !d.finish_ready && d.result_valid,
                "finished tile did not expose stable results");
        std::array<uint32_t, 16> result{};
        for (unsigned i = 0; i < result.size(); ++i) result[i] = d.results[i];
        return result;
    }
};

static void check_tile(Rig& rig, std::mt19937& random, unsigned steps,
                       uint8_t rows, uint8_t cols, unsigned& tile_count) {
    std::array<uint32_t, 16> expected{};
    rig.start();
    for (unsigned k = 0; k < steps; ++k) {
        std::array<uint8_t, 4> a{}, b{};
        for (unsigned i = 0; i < 4; ++i) {
            a[i] = static_cast<uint8_t>(random());
            b[i] = static_cast<uint8_t>(random());
        }
        rig.step(a, b, rows, cols);
        for (unsigned r = 0; r < 4; ++r)
            for (unsigned c = 0; c < 4; ++c)
                if ((rows & (1u << r)) && (cols & (1u << c)))
                    expected[4*r+c] = mac(expected[4*r+c], a[r], b[c]);
    }
    const auto actual = rig.finish();
    for (unsigned i = 0; i < actual.size(); ++i)
        require(actual[i] == expected[i], "array result differs from scalar tile oracle");
    rig.d.step_valid = 1;
    rig.d.finish_tile = 1;
    rig.d.a_row[0] = 0x7f;
    rig.d.b_col[0] = 0x7f;
    rig.eval();
    require(!rig.d.step_ready && !rig.d.finish_ready && !rig.d.step_accept &&
            !rig.d.finish_accept && rig.d.result_valid,
            "finished tile accepted a post-completion operation");
    std::array<uint32_t, 16> held{};
    for (unsigned i = 0; i < held.size(); ++i) held[i] = rig.d.results[i];
    rig.d.step_valid = rig.d.finish_tile = 0;
    for (unsigned i = 0; i < actual.size(); ++i)
        require(held[i] == actual[i], "finished tile result was not stable");
    ++tile_count;
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const uint32_t seed = argc > 1 ? uint32_t(std::stoul(argv[1], nullptr, 0)) : 1;
        std::mt19937 random(seed);
        Rig rig;
        rig.reset();
        unsigned tiles = 0;
        check_tile(rig, random, 3, 0xf, 0xf, tiles);
        rig.start();
        for (unsigned i = 0; i < 16; ++i)
            require(rig.d.results[i] == 0, "new tile did not clear prior results");
        const auto zero_k = rig.finish();
        for (auto value : zero_k) require(value == 0, "K=0 tile was not zero");
        check_tile(rig, random, 7, 0x5, 0xb, tiles);
        rig.reset();
        for (unsigned i = 0; i < 250; ++i) {
            const uint8_t rows = static_cast<uint8_t>(random() & 0xf);
            const uint8_t cols = static_cast<uint8_t>(random() & 0xf);
            check_tile(rig, random, random() % 18, rows, cols, tiles);
        }
        std::cout << "PASS: INT8 4x4 array tiles=" << tiles
                  << " randomized masks/K, K=0, reset, and stable finished results\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
