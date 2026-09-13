#include "Vaster_atomic_fabric.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <vector>

static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
struct Request {
    bool atomic = false, instr = false;
    std::uint32_t addr = 0x10000000, data = 0;
    unsigned mask = 0, op = 0;
};
static Request a(unsigned op, std::uint32_t addr = 0x10000000, std::uint32_t data = 0) {
    return {true, false, addr, data, 15, op};
}
static Request store(std::uint32_t data, std::uint32_t addr = 0x10000000, unsigned mask = 15) {
    return {false, false, addr, data, mask, 0};
}
static std::uint32_t merge(std::uint32_t old, std::uint32_t data, unsigned mask) {
    for (unsigned b = 0; b < 4; ++b)
        if (mask & (1u << b)) old = (old & ~(255u << (8*b))) | (data & (255u << (8*b)));
    return old;
}
static std::int64_t signed32(std::uint32_t x) {
    return x & 0x80000000u ? std::int64_t(x) - 0x100000000ll : x;
}
static std::uint32_t amo(unsigned op, std::uint32_t old, std::uint32_t operand) {
    switch (op) {
        case 0: return old + operand;
        case 1: return operand;
        case 4: return old ^ operand;
        case 8: return old | operand;
        case 12: return old & operand;
        case 16: return signed32(old) < signed32(operand) ? old : operand;
        case 20: return signed32(old) > signed32(operand) ? old : operand;
        case 24: return old < operand ? old : operand;
        case 28: return old > operand ? old : operand;
        default: throw std::runtime_error("invalid oracle AMO");
    }
}
static bool legal(unsigned op) {
    return op == 0 || op == 1 || op == 2 || op == 3 || op == 4 || op == 8 ||
           op == 12 || op == 16 || op == 20 || op == 24 || op == 28;
}
struct Transfer {
    unsigned owner;
    bool atomic, instr;
    std::uint32_t addr, data;
    unsigned mask;
};

class Bench {
public:
    Vaster_atomic_fabric d;
    std::array<std::uint32_t, 16384> actual{}, reference{};
    std::array<bool, 2> reservation{};
    std::array<std::uint32_t, 2> reserved_addr{};
    std::array<unsigned, 2> bypassed{};
    std::array<Request, 2> current{};
    std::array<bool, 2> valid{};
    std::mt19937 rng;
    int latency;
    unsigned runs = 0, stores = 0, successes = 0, failures = 0, atomic_ops = 0;
    unsigned commands = 0, waiting = 0, stalled = 0;
    bool lower_pending = false;
    Transfer held{};
    std::vector<Transfer> transfers;
    unsigned commits = 0;

    Bench(int delay, unsigned seed) : rng(seed), latency(delay) {
        for (unsigned i = 0; i < actual.size(); ++i) actual[i] = reference[i] = rng();
        d.resetn = d.s_valid = d.reservation_clear = d.m_ready = 0;
        tick(); tick(); d.resetn = 1;
    }
    void tick() { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    static bool ram(std::uint32_t address) {
        return address >= 0x10000000 && address < 0x10010000;
    }
    static bool permit(unsigned h, std::uint32_t address) {
        return address >= 0x10000000 && address < 0x10008000 ||
               address >= (h ? 0x1000c000u : 0x10008000u) &&
               address < (h ? 0x10010000u : 0x1000c000u);
    }
    static std::uint32_t index(std::uint32_t address) { return (address - 0x10000000) / 4; }
    std::uint32_t backing_read(std::uint32_t address, bool oracle) {
        return ram(address) ? (oracle ? reference : actual)[index(address)] : 0xcafe0000u ^ address;
    }
    void clear(unsigned mask) {
        require(!d.busy && !valid[0] && !valid[1], "reservation-clear fixture not quiescent");
        d.s_valid = 0; d.reservation_clear = mask; tick(); d.reservation_clear = 0;
        for (unsigned h = 0; h < 2; ++h) if (mask & (1u << h)) reservation[h] = false;
        check_reservations();
    }
    void check_reservations() {
        for (unsigned h = 0; h < 2; ++h) {
            require(bool(d.reserved & (1u << h)) == reservation[h], "reservation-valid mismatch");
            if (reservation[h]) require(d.reservation_addr[h] == reserved_addr[h], "reservation address mismatch");
        }
    }
    void complete(unsigned h) {
        const auto& r = current[h];
        unsigned fault = 0;
        bool write = false, read = false;
        std::uint32_t result = 0, new_data = r.data;
        unsigned mask = r.mask;
        if (r.atomic) {
            const bool match = reservation[h] && reserved_addr[h] == r.addr;
            if (r.op == 3) reservation[h] = false;
            if (!legal(r.op)) fault = 2;
            else if (r.addr & 3) fault = r.op == 2 ? 4 : 6;
            else if (!permit(h, r.addr)) fault = r.op == 2 ? 5 : 7;
            else if (r.op == 3) {
                result = match ? 0 : 1; write = match;
                if (match) ++successes; else ++failures;
            } else {
                read = true;
                result = backing_read(r.addr, true);
                if (r.op == 2) { reservation[h] = true; reserved_addr[h] = r.addr; }
                else { write = true; new_data = amo(r.op, result, r.data); }
            }
            mask = 15;
            if (!fault) ++atomic_ops;
        } else {
            const auto page = r.addr >> 12;
            const bool permitted = permit(h, r.addr) || (r.addr < 0x10000 && !r.mask) ||
                (!r.instr && (page == 0x20000 && (h == 0 || !r.mask) || page == 0x20002 || page == 0x20003));
            if (permitted) {
                write = r.mask && !r.instr; read = !write;
                if (read) result = backing_read(r.addr, true);
            }
        }
        require(d.s_fault[h] == fault && d.s_rdata[h] == result, "oracle result/fault mismatch");
        require(d.s_fault[!h] == 0 && d.s_rdata[!h] == 0, "response leaked to nonowner");
        require(bool(d.atomic_complete) == (r.atomic && !fault), "atomic completion event incorrect");
        require(bool(d.sc_success) == (r.atomic && r.op == 3 && !fault && result == 0) &&
                bool(d.sc_failure) == (r.atomic && r.op == 3 && !fault && result != 0), "SC events incorrect");
        require(transfers.size() == unsigned(read) + unsigned(write), "extra/missing backing side effects");
        unsigned t = 0;
        for (bool is_write : {false, true}) {
            if (!(is_write ? write : read)) continue;
            const auto& observed = transfers.at(t++);
            require(observed.owner == h && observed.addr == r.addr && observed.atomic == r.atomic &&
                    observed.instr == (r.instr && !r.atomic), "transfer routed to wrong owner/address/kind");
            require(observed.mask == (is_write ? mask : 0), "incorrect backing byte mask");
            if (is_write) require(observed.data == new_data, "AMO/native write data incorrect");
        }
        const bool ram_write = write && permit(h, r.addr);
        require(commits == unsigned(ram_write), "architectural store committed multiple times / without write");
        if (ram_write) {
            reference[index(r.addr)] = merge(reference[index(r.addr)], new_data, mask); ++stores;
            require(actual[index(r.addr)] == reference[index(r.addr)], "RAM side effect incorrect");
            for (unsigned other = 0; other < 2; ++other)
                if (reservation[other] && (reserved_addr[other] >> 2) == (r.addr >> 2)) reservation[other] = false;
        }
        for (unsigned other = 0; other < 2; ++other) {
            if (other == h || !valid[other]) bypassed[other] = 0;
            else require(++bypassed[other] <= 1, "pending hart bypassed more than once");
        }
        ++runs; valid[h] = false; transfers.clear(); commits = 0;
    }
    void step() {
        d.clk = 0; d.m_ready = 0;
        d.s_valid = unsigned(valid[0]) | unsigned(valid[1]) << 1;
        d.s_atomic = unsigned(current[0].atomic) | unsigned(current[1].atomic) << 1;
        d.s_instr = unsigned(current[0].instr) | unsigned(current[1].instr) << 1;
        for (unsigned h = 0; h < 2; ++h) {
            d.s_addr[h] = current[h].addr; d.s_wdata[h] = current[h].data;
            d.s_wstrb[h] = current[h].mask; d.s_op[h] = current[h].op;
        }
        d.eval();
        if (d.m_valid) {
            if (!lower_pending) {
                lower_pending = true;
                held = {d.m_owner, bool(d.m_atomic), bool(d.m_instr), d.m_addr, d.m_wdata, d.m_wstrb};
                waiting = latency < 0 ? rng() % 97 : unsigned(latency);
            } else require(d.m_owner == held.owner && bool(d.m_atomic) == held.atomic &&
                           bool(d.m_instr) == held.instr && d.m_addr == held.addr &&
                           d.m_wdata == held.data && d.m_wstrb == held.mask, "stalled backing payload changed");
            if (waiting) { --waiting; ++stalled; }
            else {
                d.m_ready = 1; d.m_rdata = backing_read(d.m_addr, false);
                if (d.m_wstrb && ram(d.m_addr))
                    actual[index(d.m_addr)] = merge(actual[index(d.m_addr)], d.m_wdata, d.m_wstrb);
                transfers.push_back(held); ++commands; lower_pending = false;
            }
        } else require(!lower_pending, "stalled backing request withdrawn");
        d.eval();
        if (d.store_commit) ++commits;
        const auto ready = d.s_ready;
        require(ready != 3 && !(ready & ~d.s_valid), "invalid/duplicate response routing");
        if (ready) complete(ready == 2 ? 1 : 0);
        d.clk = 1; d.eval();
        if (ready) check_reservations();
    }
    void queues(const std::array<std::vector<Request>, 2>& requests) {
        std::array<unsigned, 2> next{};
        unsigned cycles = 0;
        while (next[0] < requests[0].size() || next[1] < requests[1].size() || valid[0] || valid[1]) {
            for (unsigned h = 0; h < 2; ++h) if (!valid[h] && next[h] < requests[h].size()) {
                valid[h] = true; current[h] = requests[h][next[h]++];
            }
            step(); require(++cycles < 10000000, "finite-latency transaction deadlock");
        }
        require(actual == reference, "final full RAM differs from independent history");
    }
    void one(unsigned h, const Request& r) {
        std::array<std::vector<Request>, 2> q; q[h].push_back(r); queues(q);
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const unsigned seed = argc > 1 ? std::stoul(argv[1], nullptr, 0) : 0xa57e6;
        unsigned runs = 0, stores = 0, success = 0, fail = 0, commands = 0, stalls = 0;
        const std::array<unsigned, 9> ops = {0, 1, 4, 8, 12, 16, 20, 24, 28};
        const std::array<std::uint32_t, 8> values = {0, 1, 0xffffffff, 0x7fffffff, 0x80000000, 0x80000001, 0xaaaa5555, 0x12345678};
        for (int latency : {0, 1, 31, -1}) {
            Bench b(latency, seed);
            for (unsigned h = 0; h < 2; ++h) for (auto op : ops)
            for (auto old : values) for (auto operand : values) {
                b.one(h, store(old)); b.one(h, a(op, 0x10000000, operand));
            }
            for (unsigned h = 0; h < 2; ++h) {
                b.one(h, a(3)); // No LR.
                b.one(h, a(2)); b.one(h, a(3, 0x10000000, 123)); b.one(h, a(3));
                b.one(h, a(2)); b.one(h, a(2, 0x10000004)); b.one(h, a(3));
                b.one(h, a(3, 0x10000004)); // Prior failed SC consumed latest LR.
                for (unsigned mask = 1; mask < 16; ++mask) {
                    b.one(h, a(2)); b.one(!h, store(b.reference[0], 0x10000000, mask));
                    b.one(h, a(3)); // Same-value byte stores still invalidate (ABA).
                    b.one(h, a(2)); b.one(h, store(5, 0x10000004, mask));
                    b.one(h, a(3)); // Adjacent word/false sharing preserves reservation.
                }
                b.one(h, a(2)); b.clear(1u << h); b.one(h, a(3));
                b.one(h, a(2)); b.clear(1u << !h); b.one(h, a(3));
                b.one(h, a(2)); b.one(!h, a(2)); b.one(h, a(3)); b.one(!h, a(3));
                b.one(h, a(2));
                for (unsigned i = 0; i < 100; ++i) b.one(!h, a(2));
                b.one(h, a(3)); // LR-only traffic must not prevent progress.
                for (unsigned op = 0; op < 32; ++op)
                for (auto address : {0u, 0x20000000u, 0x10007ffcu, 0x10008000u, 0x1000bffcu,
                                     0x1000c000u, 0x1000fffcu, 0x10010000u, 0xfffffffcu}) {
                    b.one(h, a(op, address, 7));
                    for (unsigned offset = 1; offset < 4; ++offset) b.one(h, a(op, address + offset, 7));
                }
                for (auto address : {0u, 0x20000000u, 0x20002000u, 0x20003000u, 0x10008000u,
                                     0x1000c000u, 0x10010000u, 0xfffffffcu}) {
                    b.one(h, {false, false, address, 0, 0, 0});
                    b.one(h, store(7, address));
                    b.one(h, {false, true, address, 0, 0, 0});
                }
            }
            std::array<std::vector<Request>, 2> q;
            for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 3000; ++i) {
                const auto address = 0x10000000u + (b.rng() % 16) * 4;
                const unsigned type = b.rng() % 5;
                if (type == 0) q[h].push_back(store(b.rng(), address, 1 + b.rng() % 15));
                else if (type == 1) q[h].push_back({false, bool(b.rng() & 1), address, 0, 0, 0});
                else if (type == 2) { q[h].push_back(a(2, address)); q[h].push_back(a(3, address, b.rng())); }
                else q[h].push_back(a(ops[b.rng() % ops.size()], address, b.rng()));
            }
            b.queues(q);
            // Highly contended atomic increment has an exact independent sum.
            b.one(0, store(0)); q = {};
            for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 1000; ++i) q[h].push_back(a(0, 0x10000000, 1));
            b.queues(q); require(b.actual[0] == 2000, "contended AMO counter lost an update");
            require(b.successes && b.failures, "SC outcome coverage missing");
            runs += b.runs; stores += b.stores; success += b.successes; fail += b.failures;
            commands += b.commands; stalls += b.stalled;
        }
        std::cout << "PASS: RV32A uncached fabric seed=" << seed << ": " << runs << " operations, "
                  << commands << " backing transfers, " << stores << " committed stores, SC="
                  << success << "/" << fail << " success/failure, " << stalls << " stalled cycles\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << '\n'; return 1;
    }
}
