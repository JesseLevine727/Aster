#include "Vaster_shared_fabric.h"
#include "verilated.h"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#endif
#ifndef ASTER_MEMORY_WAIT
#define ASTER_MEMORY_WAIT 0
#endif

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
static uint32_t merge(uint32_t old, uint32_t data, unsigned mask) {
    for (unsigned b = 0; b != 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}

class Bench {
public:
    Vaster_shared_fabric d;
    std::array<uint32_t, 2> response{};
    std::array<uint32_t, 16384> ram{}, rom{};
    std::string serial;
    unsigned transfers = 0, dual_pending = 0, denied = 0;
    unsigned stalled_cycles = 0;

    Bench() {
        d.rst_n = 0;
        d.s_valid = d.s_instr = d.hart_trap = d.retired = 0;
        d.memory_transaction = d.cache_access = d.cache_miss = 0;
        d.uart_tx_ready = 1;
        d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = 0;
        for (unsigned h = 0; h != 2; ++h) d.s_addr[h] = d.s_wdata[h] = d.s_wstrb[h] = 0;
        for (unsigned i = 0; i != 4; ++i) tick();
        program(0, 0x12345678, 15);
        program(0xfffc, 0xabcdef01, 15);
        program(0, 0xaabbccdd, 5);
        d.rst_n = 1;
        tick();
        require(d.hart_run == 1, "only primary may run after reset");
    }
    void program(unsigned addr, uint32_t data, unsigned mask) {
        d.boot_we = 1; d.boot_addr = addr; d.boot_wdata = data; d.boot_wstrb = mask;
        tick();
        if (!d.rst_n) rom[addr/4] = merge(rom[addr/4], data, mask);
        d.boot_we = 0;
    }
    unsigned tick() {
        d.clk = 0; d.eval();
        const unsigned ready = d.s_ready;
        require(ready != 3 && (ready & ~d.s_valid) == 0, "duplicate/spurious ready");
        if (d.rst_n && d.s_valid && !ready)
            require(++stalled_cycles <= 4096, "fabric deadlocked");
        else stalled_cycles = 0;
        if (d.rst_n && d.uart_tx_valid && d.uart_tx_ready) serial += char(d.uart_tx_data);
        for (unsigned h = 0; h != 2; ++h) response[h] = d.s_rdata[h];
        if (d.s_valid == 3 && ready) ++dual_pending;
        if (ready) ++transfers;
        if (!d.rst_n) require(ready == 0, "reset accepted a transfer");
        d.clk = 1; d.eval();
        return ready;
    }
    void issue(unsigned h, uint32_t addr, uint32_t data = 0, unsigned mask = 0, bool instr = false) {
        require(!(d.s_valid & (1u << h)), "test tried to replace an outstanding request");
        d.s_valid |= 1u << h;
        d.s_instr = (d.s_instr & ~(1u << h)) | (unsigned(instr) << h);
        d.s_addr[h] = addr; d.s_wdata[h] = data; d.s_wstrb[h] = mask;
    }
    uint32_t access(unsigned h, uint32_t addr, uint32_t data = 0, unsigned mask = 0, bool instr = false) {
        require(d.s_valid == 0, "access requires an idle fixture");
        issue(h, addr, data, mask, instr);
        for (unsigned t = 0; t != 2048; ++t) if (tick() & (1u << h)) {
            d.s_valid = 0;
            return response[h];
        }
        throw std::runtime_error("transfer timed out at " + std::to_string(addr));
    }
    void expect(unsigned h, uint32_t addr, uint32_t value, bool instr = false) {
        const uint32_t got = access(h, addr, 0, 0, instr);
        if (got != value) throw std::runtime_error("read mismatch hart=" + std::to_string(h) +
            " address=" + std::to_string(addr) + " got=" + std::to_string(got) +
            " expected=" + std::to_string(value));
    }
    static bool allowed_ram(unsigned h, uint32_t a) {
        return a >= 0x10000000 && a < 0x10010000 &&
            (a < 0x10008000 || (h == 0 ? a < 0x1000c000 : a >= 0x1000c000));
    }
    void score(unsigned h) {
        const uint32_t a = d.s_addr[h], data = d.s_wdata[h];
        const unsigned mask = d.s_wstrb[h];
        const bool instr = d.s_instr & (1u << h);
        const bool allowed = allowed_ram(h, a);
        const uint32_t expected = a < 0x10000 ? rom[a/4] : allowed ? ram[(a-0x10000000)/4] : 0;
        if (!mask && response[h] != expected)
            throw std::runtime_error("seeded decoder/read scoreboard mismatch at " + std::to_string(a));
        if (allowed && mask && !instr) ram[(a-0x10000000)/4] = merge(expected, data, mask);
        if (!allowed && a >= 0x10000) ++denied;
        d.s_valid &= ~(1u << h);
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const uint32_t seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 1;
    try {
        Bench b;
        b.expect(0, 0x20002000, 0);
        b.expect(0, 0x20002008, ASTER_HART_COUNT);
        b.access(0, 0x20002004, 1, 14);
        b.expect(0, 0x20002004, 0);
        b.access(0, 0x20002004, 1, 1);
        b.expect(0, 0x20002004, ASTER_HART_COUNT == 2);
        if (ASTER_HART_COUNT == 2) {
            b.expect(1, 0x20002000, 1);
            b.access(1, 0x20002004, 0, 15);
            b.expect(0, 0x20002004, 1);
        }
        b.program(0, 0, 15); // live host writes must not change ROM
        b.expect(0, 0, b.rom[0], true);
        b.access(0, 0, 0, 15);
        b.expect(0, 0, b.rom[0]);
        b.expect(0, 0xfffc, b.rom[16383], true);
        b.expect(0, 0x10000, 0, true);
        b.expect(0, 0x20002008, 0, true); // MMIO is never instruction memory

        // Mailbox ownership, each lane, unknown offsets, trap/run status.
        b.access(0, 0x20002010, 0x11223344, 15);
        b.access(0, 0x20002010, 0xaabbccdd, 5);
        b.expect(0, 0x20002010, 0x11bb33dd);
        b.access(0, 0x20002014, 0xffffffff, 15);
        b.expect(0, 0x20002014, 0);
        if (ASTER_HART_COUNT == 2) {
            b.expect(1, 0x20002010, 0x11bb33dd);
            b.access(1, 0x20002010, 0, 15);
            b.expect(0, 0x20002010, 0x11bb33dd);
            b.access(1, 0x20002014, 0x87654321, 15);
            b.access(1, 0x20002014, 0xabcdef00, 10);
            b.expect(0, 0x20002014, 0xab65ef21);
        }
        b.d.hart_trap = 3;
        b.expect(0, 0x2000200c, ASTER_HART_COUNT == 2 ? 0x303 : 0x101);
        b.d.hart_trap = 0;
        b.access(0, 0x20002018, 0xffffffff, 15);
        b.expect(0, 0x20002018, 0);

        // One common interval, distinct real event input lanes, missing-hart zero.
        b.access(0, 0x20003038, 1, 1);
        b.d.retired = b.d.memory_transaction = b.d.cache_access = 3;
        b.d.cache_miss = 2;
        for (unsigned i = 0; i != 8; ++i) b.tick();
        b.d.retired = b.d.memory_transaction = b.d.cache_access = b.d.cache_miss = 0;
        b.access(0, 0x20003038, 2, 1);
        for (unsigned h = 0; h != 2; ++h) {
            const uint32_t base = 0x20003000 + h*256;
            b.expect(0, base, 8);
            b.expect(0, base+8, h < ASTER_HART_COUNT ? 8 : 0);
            b.expect(0, base+16, h < ASTER_HART_COUNT ? 8 : 0);
            b.expect(0, base+24, h < ASTER_HART_COUNT ? 8 : 0);
            b.expect(0, base+32, h == 1 && ASTER_HART_COUNT == 2 ? 8 : 0);
            b.expect(0, base+0x40, 0);
            b.expect(0, base+0x48, 3);
            b.expect(0, base+0x50, ASTER_SYNC_MEMORY*2 + ASTER_ENABLE_L1);
            b.expect(0, base+0x54, ASTER_LINE_WORDS);
            b.expect(0, base+0x58, ASTER_LINE_COUNT);
            b.expect(0, base+0x5c, ASTER_MEMORY_WAIT);
        }
        b.access(0, 0x20003138, 1, 1); // bank 1 cannot control either bank
        if (ASTER_HART_COUNT == 2) b.access(1, 0x20003038, 1, 1);
        b.expect(0, 0x20003000, 8);
        b.expect(0, 0x20003100, 8);

        // Console ownership and a locked grant behind real UART backpressure.
        b.d.uart_tx_ready = 0;
        b.access(0, 0x20000000, 'A', 1);
        if (ASTER_HART_COUNT == 2) b.access(1, 0x20000000, 'X', 15);
        b.access(0, 0x20000000, 'X', 14);
        require(b.d.uart_tx_valid && b.d.uart_tx_data == 'A', "UART ownership/strobes");
        b.issue(0, 0x20000000, 'B', 1);
        require(b.tick() == 0, "full UART accepted a second write");
        if (ASTER_HART_COUNT == 2) b.issue(1, 0x20002000);
        for (unsigned i = 0; i != 20; ++i) require(b.tick() == 0, "stalled UART grant stolen");
        b.d.uart_tx_ready = 1;
        require(b.tick() == 1, "UART did not unblock owner");
        b.d.s_valid &= ~1u;
        if (ASTER_HART_COUNT == 2) {
            require(b.tick() == 2 && b.response[1] == 1, "peer response corrupted");
            b.d.s_valid = 0;
        }
        b.tick();
        require(b.serial == "AB", "lost/duplicate/unauthorized UART bytes");

        // Permission edges and masks: every actual RAM word starts at zero.
        const std::array<uint32_t, 12> edges = {0x0ffffffc, 0x10000000, 0x10007ffc,
            0x10008000, 0x1000bffc, 0x1000c000, 0x1000fffc, 0x10010000,
            0x1001fffc, 0x20001000, 0x30000000, 0x40000000};
        for (unsigned h = 0; h < ASTER_HART_COUNT; ++h) for (auto a : edges) {
            for (unsigned mask = 0; mask != 16; ++mask) {
                b.issue(h, a, 0x1357ace0 ^ mask, mask);
                while (!(b.tick() & (1u << h))) {}
                b.score(h);
                b.issue(h, a);
                while (!(b.tick() & (1u << h))) {}
                b.score(h);
            }
        }
        // Correlated seeded requests contend on shared words and each private
        // region. Include denied fetch/store and unmapped aliases, not just RAM.
        std::mt19937 rng(seed);
        for (unsigned cycle = 0; cycle != 60000; ++cycle) {
            for (unsigned h = 0; h < ASTER_HART_COUNT; ++h) if (!(b.d.s_valid & (1u << h)) && rng()%4) {
                uint32_t a;
                switch (rng()%6) {
                    case 0: a = 0x10000000 + (rng()%128)*4; break;
                    case 1: a = 0x10008000 + (rng()%128)*4; break;
                    case 2: a = 0x1000c000 + (rng()%128)*4; break;
                    case 3: a = (rng()%128)*4; break;
                    default: a = edges[rng()%edges.size()]; break;
                }
                b.issue(h, a, rng(), rng()%3 ? rng()%16 : 0, rng()%5 == 0);
            }
            const unsigned ready = b.tick();
            for (unsigned h = 0; h != 2; ++h) if (ready & (1u << h)) b.score(h);
        }
        while (b.d.s_valid) {
            const unsigned ready = b.tick();
            for (unsigned h = 0; h != 2; ++h) if (ready & (1u << h)) b.score(h);
        }
        // Exhaustive final RAM readback catches accepted stores never re-read
        // during random traffic, including corruption of another hart's words.
        for (unsigned word = 0; word != b.ram.size(); ++word) {
            const unsigned h = word >= 12288 ? 1 : 0;
            if (h < ASTER_HART_COUNT) b.expect(h, 0x10000000 + word*4, b.ram[word]);
        }

        if (ASTER_HART_COUNT == 2 && ASTER_MEMORY_WAIT > 0) {
            // Primary reset must queue behind an already-granted worker store.
            b.issue(1, 0x10000000, 0x76543210, 15);
            require(b.tick() == 0, "memory wait ignored");
            b.issue(0, 0x20002004, 0, 1);
            unsigned ready = 0;
            while (!(ready = b.tick())) {}
            require(ready == 2, "reset stole worker's in-flight memory transfer");
            b.d.s_valid &= ~2u;
            require(b.tick() == 1, "secondary reset not accepted");
            b.d.s_valid = 0;
            b.expect(0, 0x10000000, 0x76543210);
        } else b.access(0, 0x20002004, 0, 1);
        b.expect(0, 0x20002010, 0);
        b.expect(0, 0x20002014, 0);
        b.expect(0, 0x2000200c, 1);
        b.expect(0, 0x20003000, 8); // worker reset does not erase measurements
        b.access(0, 0x20002004, 1, 1);
        if (ASTER_HART_COUNT == 2) b.expect(1, 0x20002000, 1);
        // Global reset during a UART stall cancels the unaccepted write and
        // clears the accepted elastic byte. No stale traffic appears on release.
        b.d.uart_tx_ready = 0;
        b.access(0, 0x20000000, 'C', 1);
        b.issue(0, 0x20000000, 'D', 1);
        require(b.tick() == 0, "UART abort setup");
        b.d.rst_n = 0;
        b.tick(); b.tick();
        b.d.s_valid = 0;
        b.d.uart_tx_ready = 1;
        b.d.rst_n = 1;
        b.tick();
        require(b.serial == "AB" && b.d.hart_run == 1, "stale UART/run after reset");
        b.expect(0, 0x20003000, 0);
        b.expect(0, 0x20003100, 0);
        b.expect(0, 0, b.rom[0], true);
        b.expect(0, 0x10007ffc, b.ram[8191]);
        require(b.denied > 100 && (ASTER_HART_COUNT == 1 || b.dual_pending > 100), "coverage too weak");
        std::cout << "PASS: shared fabric harts=" << ASTER_HART_COUNT << " seed=" << seed
                  << " transfers=" << b.transfers << " dual_pending=" << b.dual_pending
                  << " denied=" << b.denied << " memory_wait=" << ASTER_MEMORY_WAIT << '\n';
        return EXIT_SUCCESS;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: shared fabric seed=" << seed << ": " << e.what() << '\n';
        return EXIT_FAILURE;
    }
}
