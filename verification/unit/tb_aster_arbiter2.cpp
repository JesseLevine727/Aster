#include "Vaster_arbiter2.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <stdexcept>

struct Request {
    bool valid = false, instr = false;
    uint32_t addr = 0, data = 0;
    uint8_t mask = 0;
};

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

static uint32_t merge(uint32_t old, uint32_t data, uint8_t mask) {
    for (unsigned b = 0; b != 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}

class Bench {
public:
    Vaster_arbiter2 dut;
    std::array<Request, 2> req{};
    std::array<uint32_t, 256> actual{}, expected{};
    std::array<unsigned, 2> accepted{}, passed_over{};
    unsigned steps = 0, completions = 0, aborted = 0, stores = 0;
    unsigned simultaneous = 0, stalled = 0, identical = 0;
    std::array<bool, 16> masks{};
    bool preferred = false;
    int held = -1;

    Bench() {
        for (unsigned i = 0; i != actual.size(); ++i) actual[i] = expected[i] = 0xa57e0000u + i;
        step(false, true);
    }

    // Independent software selection: retain the pending transaction's owner;
    // otherwise search preferred then nonpreferred requester. The target is a
    // reference RAM with a counted write side effect; real MMIO is tested at SoC.
    int step(bool ready, bool reset = false) {
        ++steps;
        int selected = held;
        if (selected < 0) {
            if (req[preferred].valid) selected = preferred;
            else if (req[!preferred].valid) selected = !preferred;
        }
        if (reset) selected = -1;
        dut.clk = 0;
        dut.rst_n = !reset;
        dut.s_valid = unsigned(req[0].valid) | (unsigned(req[1].valid) << 1);
        dut.s_instr = unsigned(req[0].instr) | (unsigned(req[1].instr) << 1);
        for (unsigned h = 0; h != 2; ++h) {
            dut.s_addr[h] = req[h].addr;
            dut.s_wdata[h] = req[h].data;
            dut.s_wstrb[h] = req[h].mask;
        }
        dut.m_ready = ready;
        dut.m_rdata = 0xbad0da7a;
        dut.eval();
        require(bool(dut.m_valid) == (selected >= 0), "valid/reset mismatch");
        if (selected >= 0) {
            const auto& r = req[selected];
            require(dut.m_owner == selected, "wrong owner / stolen stalled grant / fairness");
            require(dut.m_instr == r.instr && dut.m_addr == r.addr &&
                    dut.m_wdata == r.data && dut.m_wstrb == r.mask, "request fields corrupted");
            dut.m_rdata = actual[(dut.m_addr >> 2) & 255];
            dut.eval();
            require(dut.s_rdata[1-selected] == 0, "response leaked to nonowner");
            require(dut.s_rdata[selected] == expected[(r.addr >> 2) & 255], "read result mismatch");
        }
        const unsigned expected_ready = selected >= 0 && ready ? 1u << selected : 0;
        require(dut.s_ready == expected_ready, "ready leaked, duplicated or lost");
        int completed = -1;
        if (selected >= 0 && ready) {
            completed = selected;
            ++completions;
            ++accepted[selected];
            if (req[0].valid && req[1].valid) ++simultaneous;
            const auto& r = req[selected];
            masks[r.mask] = true;
            if (r.mask) {
                expected[(r.addr >> 2) & 255] = merge(expected[(r.addr >> 2) & 255], r.data, r.mask);
                ++stores;
            }
            for (unsigned h = 0; h != 2; ++h) {
                if (h == unsigned(selected) || !req[h].valid) passed_over[h] = 0;
                else require(++passed_over[h] <= 1, "bounded fairness exceeded");
            }
            preferred = !selected;
            held = -1;
        } else if (selected >= 0) {
            ++stalled;
            held = selected;
        }
        // Model the actual target from DUT outputs, not selected/req variables.
        if (dut.m_valid && dut.m_ready && dut.m_wstrb)
            actual[(dut.m_addr >> 2) & 255] = merge(actual[(dut.m_addr >> 2) & 255],
                                                    dut.m_wdata, dut.m_wstrb);
        dut.clk = 1;
        dut.eval();
        require(actual == expected, "target side effects differ from accepted source requests");
        if (reset) {
            if (held >= 0) ++aborted;
            held = -1;
            preferred = false;
            passed_over = {};
            req = {};
        }
        return completed;
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const uint32_t seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
    try {
        Bench b;
        // Preferred hart arrives after another hart was granted a stalled port.
        b.req[1] = {true, true, 0x20, 0x11111111, 0};
        b.step(false);
        b.req[0] = {true, false, 0x24, 0x22222222, 15};
        for (unsigned i = 0; i != 17; ++i) b.step(false);
        require(b.step(true) == 1, "late arrival stole grant");
        b.req[1].valid = false;
        require(b.step(true) == 0, "pending peer lost");
        b.req = {};
        // Continuous identical back-to-back traffic: never wait for valid to fall.
        for (unsigned mask = 0; mask != 16; ++mask) {
            b.req[0] = b.req[1] = {true, false, 0x1fc, 0x99887766u ^ mask, uint8_t(mask)};
            for (unsigned i = 0; i != 8; ++i) { b.step(true); ++b.identical; }
        }
        b.req = {};
        b.step(true);
        // Abort a selected store with target-ready high: reset must suppress it.
        b.req[1] = {true, false, 0x14, 0xbadcafe, 15};
        b.step(false);
        b.step(true, true);
        b.step(true);

        std::mt19937 rng(seed);
        unsigned wait = 0;
        for (unsigned cycle = 0; cycle != 100000; ++cycle) {
            if (cycle % 997 == 0) { b.step(true, true); wait = 0; }
            for (unsigned h = 0; h != 2; ++h) if (!b.req[h].valid && rng()%4 != 0) {
                const bool instr = rng()%5 == 0;
                b.req[h] = {true, instr, uint32_t((rng()%256)*4), uint32_t(rng()),
                            uint8_t(instr ? 0 : rng()%16)};
            }
            const int done = b.step(wait == 0);
            if (done >= 0) {
                b.req[done].valid = false;
                wait = rng()%18;
            } else if (wait) --wait;
        }
        // Drain outstanding sources, then idle; no duplicate completion allowed.
        for (unsigned i = 0; i != 2; ++i) {
            const int done = b.step(true);
            if (done >= 0) b.req[done].valid = false;
        }
        for (unsigned i = 0; i != 16; ++i) require(b.step(true) == -1, "ghost completion");
        require(b.accepted[0] > 4000 && b.accepted[1] > 4000 && b.aborted > 20 &&
                b.stores > 1000 && b.simultaneous > 1000 && b.stalled > 10000,
                "insufficient random coverage");
        for (bool mask : b.masks) require(mask, "missing byte strobe coverage");
        std::cout << "PASS: arbiter seed=" << seed << " completions=" << b.completions
                  << " hart0=" << b.accepted[0] << " hart1=" << b.accepted[1]
                  << " stores=" << b.stores << " aborted=" << b.aborted
                  << " simultaneous=" << b.simultaneous << " stalled=" << b.stalled
                  << " identical=" << b.identical << '\n';
        return EXIT_SUCCESS;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: seed=" << seed << ": " << e.what() << '\n';
        return EXIT_FAILURE;
    }
}
