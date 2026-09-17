#include "Vaster_npu_regs.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

static void require(bool condition, const char* why) {
    if (!condition) throw std::runtime_error(why);
}

constexpr uint32_t base = 0x10000000u;
constexpr uint32_t limit = 0x10008000u;

class Rig {
public:
    Vaster_npu_regs d;
    std::array<uint8_t, limit - base> memory{};
    std::mt19937 random{0xa57e9};
    unsigned memory_transactions = 0;

    Rig() {
        for (auto& value : memory) value = static_cast<uint8_t>(random());
        d.resetn = d.global_stop = d.req_valid = d.req_write = d.req_addr = 0;
        d.req_wdata = d.req_wstrb = d.m_ready = d.m_rdata = 0;
        d.clk = 0; d.eval();
        d.clk = 1; d.eval();
        d.resetn = 1;
        d.eval();
    }

    uint32_t word(uint32_t address) const {
        require(address >= base && address <= limit - 4 && !(address & 3),
                "register-boundary memory request escaped shared RAM");
        uint32_t value = 0;
        for (unsigned lane = 0; lane < 4; ++lane)
            value |= uint32_t(memory[address - base + lane]) << (8 * lane);
        return value;
    }

    void clock(bool ready = true, unsigned stall = 0) {
        d.clk = 0;
        d.m_ready = 0;
        d.eval();
        if (d.m_valid) {
            d.m_rdata = word(d.m_addr);
            if (ready && stall == 0) d.m_ready = 1;
            if (d.m_write) {
                for (unsigned lane = 0; lane < 4; ++lane)
                    if (d.m_wstrb & (1u << lane))
                        memory[d.m_addr - base + lane] = d.m_wdata >> (8 * lane);
            }
        }
        d.eval();
        if (d.m_valid && d.m_ready) ++memory_transactions;
        d.clk = 1;
        d.eval();
    }

    void write(uint32_t offset, uint32_t value, uint8_t mask = 0xf) {
        d.req_valid = d.req_write = 1;
        d.req_addr = offset; d.req_wdata = value; d.req_wstrb = mask;
        d.eval();
        require(d.req_ready, "register write was not accepted");
        clock();
        d.req_valid = d.req_write = 0;
        d.req_wstrb = 0;
        d.eval();
    }

    uint32_t read(uint32_t offset) {
        d.req_valid = 1; d.req_write = 0; d.req_addr = offset; d.req_wdata = d.req_wstrb = 0;
        d.eval();
        require(d.req_ready, "register read was not accepted");
        const uint32_t value = d.req_rdata;
        clock();
        d.req_valid = 0;
        d.eval();
        return value;
    }

    void configure(uint32_t a, uint32_t b, uint32_t c,
                   uint32_t as, uint32_t bs, uint32_t cs,
                   uint32_t m, uint32_t n, uint32_t k) {
        write(0x10, a); write(0x14, b); write(0x18, c);
        write(0x1c, as); write(0x20, bs); write(0x24, cs);
        write(0x28, m); write(0x2c, n); write(0x30, k);
    }
};

static void run_gemm(Rig& rig) {
    const uint32_t a = base + 1, b = base + 257, c = base + 1025;
    const uint32_t m = 3, n = 5, k = 7, as = 9, bs = 7, cs = 24;
    std::array<uint8_t, limit - base> before = rig.memory;
    std::array<uint32_t, 15> expected{};
    for (uint32_t i = 0; i < m; ++i)
        for (uint32_t j = 0; j < n; ++j) {
            int64_t total = 0;
            for (uint32_t x = 0; x < k; ++x)
                total += int64_t(static_cast<int8_t>(before[a - base + i * as + x])) *
                         int64_t(static_cast<int8_t>(before[b - base + x * bs + j]));
            expected[i * n + j] = static_cast<uint32_t>(total);
        }

    rig.configure(a, b, c, as, bs, cs, m, n, k);
    require(rig.read(0x08) == 1 && rig.read(0x0c) == 1, "NPU ABI readback is wrong");
    rig.write(0x00, 1, 1);
    require(rig.d.busy && !rig.d.done, "START did not launch engine");

    const unsigned transactions_before_reject = rig.memory_transactions;
    rig.write(0x10, base + 3000);
    rig.write(0x00, 1, 1);
    require(rig.d.busy && rig.read(0x10) == a && rig.read(0x34) == 0,
            "busy configuration/START changed the active descriptor");

    for (unsigned guard = 0; guard < 1000000 && rig.d.busy; ++guard)
        rig.clock(true, guard % 13 == 2 ? 3 : 0);
    require(!rig.d.busy && rig.d.done && !rig.d.error && !rig.d.aborted,
            "register-controlled GEMM did not complete");
    require(rig.memory_transactions > transactions_before_reject,
            "register-controlled GEMM did not reach the RAM master");
    for (uint32_t i = 0; i < m; ++i)
        for (uint32_t j = 0; j < n; ++j)
            for (uint32_t byte = 0; byte < 4; ++byte)
                require(rig.memory[c - base + i * cs + 4 * j + byte] ==
                            static_cast<uint8_t>(expected[i * n + j] >> (8 * byte)),
                        "register-controlled GEMM output differs from scalar oracle");
    require(rig.read(0x38) != 0 && rig.read(0x3c) == m * n * 4,
            "NPU byte accounting register readback is wrong");
    const uint32_t old_read = rig.read(0x38), old_cycles = rig.read(0x40);
    rig.write(0x00, 4, 1);
    require(rig.read(0x04) == 0 && rig.read(0x38) == old_read && rig.read(0x40) == old_cycles,
            "ACK did not clear status while retaining accounting");

    // PicoRV32 presents an SB as replicated data with only the selected lane
    // strobed. Lane-0 CONTROL must accept that legal encoding.
    rig.write(0x00, 0x01010101u, 1);
    for (unsigned guard = 0; guard < 1000000 && rig.d.busy; ++guard) rig.clock();
    require(rig.d.done && !rig.d.error && !rig.d.aborted,
            "replicated PicoRV32 SB command was rejected");
}

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Rig rig;
        require(rig.read(0x08) == 1 && rig.read(0x0c) == 1, "reset ABI values are wrong");
        require((rig.read(0x54) & 0x1f) == 0x1f, "4x4 INT8 feature bits are wrong");
        run_gemm(rig);
        rig.configure(base, base + 512, base + 1024, 1, 1, 4, 1, 1, 1025);
    rig.write(0x00, 1, 1);
    for (unsigned guard = 0; guard < 1000000 && rig.d.busy; ++guard) rig.clock();
    require(rig.read(0x04) == 0x6 && rig.read(0x34) == 1 && !rig.d.m_valid,
            "malformed descriptor did not stop at register boundary");
        rig.write(0x00, 4, 1);
        rig.write(0x00, 1, 0x2);
        require(rig.read(0x04) == 0x4 && rig.read(0x34) == 7,
                "upper control byte lane was not rejected");
        std::cout << "PASS: NPU ABI/control registers, busy rejection, ACK accounting, "
                     "RAM-backed GEMM, feature/descriptor/error behavior\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
