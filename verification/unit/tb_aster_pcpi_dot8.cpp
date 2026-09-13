#include "Vaster_pcpi_dot8.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

// Independent integer arithmetic: no RTL-width arithmetic or packed multiply.
static uint32_t reference(uint32_t a, uint32_t b) {
    int64_t total = 0;
    for (unsigned i = 0; i < 4; ++i) {
        int64_t x = (a >> (8*i)) & 255, y = (b >> (8*i)) & 255;
        if (x >= 128) x -= 256;
        if (y >= 128) y -= 256;
        total += x*y;
    }
    require(total >= -65024 && total <= 65536, "reference range");
    return uint32_t(total);
}

class Rig {
public:
    Vaster_pcpi_dot8 d;
    uint64_t accepted = 0, completed = 0, waits = 0, jobs = 0;
    void evaluate() { d.clk = 0; d.eval(); }
    void tick() {
        evaluate();
        accepted += d.event_accept; completed += d.event_complete; waits += d.event_wait;
        d.clk = 1; d.eval();
    }
    void quiet() {
        require(!d.pcpi_wait && !d.pcpi_ready && !d.pcpi_wr && !d.busy &&
                !d.event_accept && !d.event_complete && !d.event_wait, "reset/idle leaked event or handshake");
    }
    void reset() {
        d.resetn = 0; evaluate(); quiet(); tick();
        require(d.pcpi_rd == 0, "reset did not clear arithmetic result");
        d.pcpi_valid = 0; d.admit = 0; d.resetn = 1; evaluate(); quiet();
    }
    void copy(uint32_t a, uint32_t b, uint32_t registers = 0, unsigned pause = 0, unsigned hold = 0) {
        const auto before_accept = accepted, before_complete = completed, before_wait = waits;
        require(!d.busy, "test issued while busy");
        d.pcpi_insn = 0x0b | (registers & 0x01ff8f80);
        d.pcpi_rs1 = a; d.pcpi_rs2 = b; d.pcpi_valid = 1; d.admit = 0;
        for (unsigned i = 0; i < pause; ++i) {
            evaluate();
            require(d.pcpi_wait && !d.busy && !d.pcpi_ready && !d.pcpi_wr &&
                    !d.event_accept && !d.event_complete && d.event_wait, "unadmitted work was captured or timed out");
            tick();
        }
        d.admit = 1; evaluate();
        require(d.event_accept && d.pcpi_wait && !d.busy && !d.pcpi_ready, "missing immediate exact claim");
        tick();
        require(d.busy && d.pcpi_wait && !d.pcpi_ready && !d.event_accept && d.event_complete,
                "incorrect capture/sum stage");
        // A paused lifecycle and changing source wires cannot corrupt operands
        // already admitted. No memory responder or CPU scalar helper is used.
        d.admit = 0; d.pcpi_rs1 = ~a; d.pcpi_rs2 = b ^ 0x81927f00; d.pcpi_insn = 0xffffffff;
        tick();
        for (unsigned i = 0; i <= hold; ++i) {
            require(d.busy && d.pcpi_ready && d.pcpi_wr && !d.pcpi_wait &&
                    !d.event_accept && !d.event_complete && !d.event_wait && d.pcpi_rd == reference(a,b),
                    "wrong exact sum / held response changed or repeated computation");
            d.pcpi_rs1 ^= 0x55aa817f; d.pcpi_rs2 += 0x1234567; tick();
        }
        d.pcpi_valid = 0; evaluate();
        require(!d.pcpi_ready && !d.pcpi_wr, "response not gated by valid");
        tick(); quiet();
        require(accepted == before_accept+1 && completed == before_complete+1 && waits == before_wait+pause+2,
                "not exactly one capture/completion or wrong wait accounting");
        ++jobs;
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc,argv);
        const uint32_t seed = argc > 1 ? uint32_t(std::stoul(argv[1],nullptr,0)) : 1;
        std::mt19937 random(seed); Rig r; r.reset();
        require(reference(0x80808080,0x80808080) == 65536 &&
                reference(0x80808080,0x7f7f7f7f) == uint32_t(-65024), "signed extrema oracle");
        unsigned decoded = 0;
        for (unsigned opcode = 0; opcode < 128; ++opcode)
        for (unsigned funct3 = 0; funct3 < 8; ++funct3)
        for (unsigned funct7 = 0; funct7 < 128; ++funct7) {
            r.reset(); r.d.pcpi_valid = r.d.admit = 1;
            r.d.pcpi_insn = (funct7 << 25) | (funct3 << 12) | opcode | (random() & 0x01ff8f80);
            r.d.pcpi_rs1 = random(); r.d.pcpi_rs2 = random(); r.evaluate();
            const bool legal = opcode == 0x0b && funct3 == 0 && funct7 == 0;
            require(bool(r.d.pcpi_wait) == legal && bool(r.d.event_accept) == legal &&
                    !r.d.pcpi_ready && !r.d.event_complete && !r.d.busy, "opcode/function decode alias");
            r.tick(); require(bool(r.d.busy) == legal && !r.d.pcpi_ready, "illegal encoding captured");
            ++decoded;
        }
        r.reset();
        for (unsigned lane = 0; lane < 4; ++lane)
        for (unsigned a = 0; a < 256; ++a)
        for (unsigned b = 0; b < 256; ++b)
            r.copy(uint32_t(a) << (8*lane),uint32_t(b) << (8*lane),random());
        const std::array<uint32_t,12> corner = {0,0x01010101,0x7f7f7f7f,0x80808080,0xffffffff,0x01020304,
            0x7f80ff00,0xff007f80,0x0080ff7f,0x80000000,0x00000080,0x00ff0000};
        for (auto a : corner) for (auto b : corner) r.copy(a,b,random(),random()%7,random()%7);
        for (unsigned i = 0; i < 6000; ++i) r.copy(random(),random(),random(),random()%17,random()%17);
        // Every rd/rs1/rs2 field is legal; actual register alias/writeback is
        // additionally checked in the real-core feasibility harness.
        for (unsigned rd = 0; rd < 32; ++rd)
        for (unsigned a = 0; a < 32; ++a)
        for (unsigned b = 0; b < 32; ++b) r.copy(random(),random(),(rd<<7)|(a<<15)|(b<<20));
        r.copy(0x807f01ff,0xff01807f,0,1025,1025);

        for (unsigned stage = 0; stage < 4; ++stage) {
            r.reset(); r.d.pcpi_valid = 1; r.d.pcpi_insn = 0x0b;
            r.d.pcpi_rs1 = r.d.pcpi_rs2 = 0x80808080; r.d.admit = 0;
            r.tick(); // recognized admission pause
            if (stage >= 1) { r.d.admit = 1; r.tick(); }
            if (stage >= 2) r.tick();
            if (stage >= 3) for (unsigned i = 0; i < 37; ++i) r.tick();
            r.reset(); for (unsigned i = 0; i < 20; ++i) { r.tick(); r.quiet(); }
            r.copy(0x01020304,0x05060708);
        }
        std::cout << "PASS: dot8 PCPI seed=" << seed << " decode=" << decoded
                  << " lane_products=262144 register_fields=32768 packed=6144 jobs=" << r.jobs
                  << " reset_stages=4; exact signed sum, admission wait, captured operands, held reply, exactly-once events\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n'; return 1;
    }
}
