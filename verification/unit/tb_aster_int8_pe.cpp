#include "Vaster_int8_pe.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

static uint32_t reference(uint8_t a, uint8_t b, uint32_t acc) {
    const int32_t lhs = static_cast<int8_t>(a);
    const int32_t rhs = static_cast<int8_t>(b);
    return acc + static_cast<uint32_t>(lhs * rhs);
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Vaster_int8_pe pe;

        pe.resetn = 0;
        pe.valid = 1;
        pe.a = 0x7f;
        pe.b = 0x7f;
        pe.acc_in = 0;
        pe.eval();
        require(!pe.ready && !pe.accept, "reset accepted an operation");

        pe.resetn = 1;
        pe.eval();
        require(pe.ready && pe.accept, "valid operation was not accepted");
        require(pe.acc_out == 0x00003f01u, "signed product result is wrong");

        pe.a = 0x80;
        pe.b = 0x80;
        pe.acc_in = 1;
        pe.eval();
        require(pe.accept && pe.acc_out == 0x00004001u,
                "INT8 minimum product or accumulator add is wrong");

        pe.valid = 0;
        pe.eval();
        require(pe.ready && !pe.accept, "invalid operation was accepted");

        std::mt19937 random(0xa57e9);
        for (unsigned a = 0; a < 256; ++a) {
            for (unsigned b = 0; b < 256; ++b) {
                pe.valid = 1;
                pe.a = a;
                pe.b = b;
                pe.acc_in = random();
                pe.eval();
                require(pe.accept && pe.acc_out == reference(a, b, pe.acc_in),
                        "exhaustive signed INT8 product mismatch");
            }
        }
        for (unsigned i = 0; i < 10000; ++i) {
            pe.valid = 1;
            pe.a = random();
            pe.b = random();
            pe.acc_in = random();
            const uint32_t expected = reference(static_cast<uint8_t>(pe.a),
                                                static_cast<uint8_t>(pe.b),
                                                pe.acc_in);
            pe.eval();
            require(pe.accept && pe.acc_out == expected,
                    "random signed INT8 product mismatch");
        }

        std::cout << "PASS: INT8 PE reset, valid/ready acceptance, exhaustive 8-bit signed products, "
                     "random accumulators=10000, modulo-32-bit accumulation\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
