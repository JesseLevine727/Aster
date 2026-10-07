// The Phase 20 fabric's reference (docs/soc.md §4), independent of the RTL:
// main memory as bytes, the AMO operations, and reservations with Spike's
// rule. The fabric shell (tb_fabric.cpp, 20.0) checks every answer against
// it; the SoC's memory checker (soc.md §10.2, 20.2) applies writes to it as
// the fabric accepts them.
#pragma once

#include <cstdint>
#include <vector>

namespace fabric {

// aster_core_pkg's d_req_op (cpu.md §5).
enum Op : int { LOAD = 0, STORE = 1, LR = 2, SC = 3, SWAP = 4, ADD = 5, XOR = 6, AND = 7, OR = 8, MIN = 9, MAX = 10,
                MINU = 11, MAXU = 12 };
inline bool is_amo(int op) { return op >= SWAP && op <= MAXU; }

// An AMO's new value from its word's old value and its operand.
inline std::uint32_t amo_value(int op, std::uint32_t old, std::uint32_t operand) {
    const auto s_old = std::int32_t(old), s_op = std::int32_t(operand);
    switch (op) {
    case ADD: return old + operand;
    case XOR: return old ^ operand;
    case AND: return old & operand;
    case OR: return old | operand;
    case MIN: return s_old < s_op ? old : operand;
    case MAX: return s_old > s_op ? old : operand;
    case MINU: return old < operand ? old : operand;
    case MAXU: return old > operand ? old : operand;
    default: return operand;                                    // SWAP
    }
}

// A write as it takes effect: an 8-byte unit's address, its byte enables and data.
enum WriteKind : int { PLAIN = 0, BY_SC = 1, BY_AMO = 2 };   // a store, NPU or DMA write; an sc's; an AMO's
struct Write {
    int writer = 0;
    std::uint32_t unit_addr = 0;                                // byte address, 8-byte aligned
    std::uint8_t be8 = 0;
    std::uint64_t data = 0;
    int kind = PLAIN;
};

// Whether a write touches any byte of a word (word = byte address >> 2).
inline bool touches(const Write& w, std::uint32_t word) {
    return (w.unit_addr >> 3) == (word >> 1) && ((word & 1u) ? (w.be8 & 0xF0u) : (w.be8 & 0x0Fu));
}

class Memory {
public:
    Memory(std::uint32_t base_address, std::uint32_t size) : base(base_address), bytes(size, 0) {}
    bool contains(std::uint32_t address) const { return address - base < bytes.size(); }
    std::uint64_t unit(std::uint32_t address) const {
        const std::uint32_t at = (address & ~7u) - base;
        std::uint64_t value = 0;
        for (int b = 7; b >= 0; --b) value = (value << 8) | bytes.at(at + b);
        return value;
    }
    std::uint32_t word(std::uint32_t address) const {
        return std::uint32_t(unit(address) >> ((address & 4u) ? 32 : 0));
    }
    void write(const Write& w) {
        const std::uint32_t at = w.unit_addr - base;
        for (int b = 0; b < 8; ++b)
            if (w.be8 & (1u << b)) bytes.at(at + b) = std::uint8_t(w.data >> (8 * b));
    }
    std::uint32_t base;
    std::vector<std::uint8_t> bytes;
};

// One hart's reservation (soc.md §4.5): an lr sets it; another requester's
// write to any byte of its word, every sc of the hart, its exception and its
// reset end it. A write ends only the reservation on the word it touches (an
// lr in its cycle reserves another word: the unit rule); an exception or a
// reset in an lr's cycle wins over it.
struct Reservation {
    bool valid = false;
    std::uint32_t word = 0;
};

}  // namespace fabric
