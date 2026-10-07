// The SoC's memory checker (docs/soc.md §10.2; built in 20.0, wired into the
// two-hart SoC's simulation in 20.2). The testbench reports, cycle by cycle:
// - each write as it takes effect (the fabric's acceptance of a store, a
//   successful sc, an NPU or DMA write; an AMO's write two edges later);
// - each load as it is performed (soc.md §4.7: a hit at its data cache's
//   lookup; a miss at the fabric's acceptance of the refill read of its word;
//   an lr at its acceptance; an AMO at its write), before that cycle's
//   writes;
// - each load as its hart retires it (RVFI: its word address, byte mask and
//   data in riscv-formal's aligned layout).
// A hart retires its loads in the order it performed them, so each
// retirement is matched with that hart's oldest performed load; its value
// must be the reference's word at that load's perform point, on the bytes
// its mask names. This checks coherence and the memory model end to end,
// whatever the two harts' interleaving: a stale value (a snoop arriving
// late), a value never written, or a load answered out of turn all fail.
//
// A device load is performed with the value the device answered
// (performed_value). A hart reset drops its performed loads (drop). What 20.2's
// testbench must still supply, not here: which loads are performed at all
// (a load the data cache faults never is), a perform point for each (the
// cache's chk_lookup and the fabric's refill acceptances), writes made by the
// ARM side while the harts are held, and the sc, reservation and AMO checks,
// which the fabric shell's reference (fabric_ref.h) makes at the fabric.
#pragma once

#include "fabric_ref.h"

#include <cstdint>
#include <deque>
#include <sstream>
#include <string>

namespace fabric {

class MemoryChecker {
public:
    MemoryChecker(std::uint32_t base, std::uint32_t bytes) : memory(base, bytes) {}

    // A write takes effect at this cycle's edge (call after the cycle's performs).
    void write(const Write& w) { memory.write(w); }

    // A load of `hart` is performed now, on the word at `address`.
    void performed(int hart, std::uint32_t address, std::uint64_t cycle) {
        pending[hart].push_back({address & ~3u, memory.word(address), cycle});
    }

    // A load of a device word, performed with the value the device answered.
    void performed_value(int hart, std::uint32_t address, std::uint32_t value, std::uint64_t cycle) {
        pending[hart].push_back({address & ~3u, value, cycle});
    }

    // `hart` was reset: the loads it performed and never retired are gone.
    void drop(int hart) { pending[hart].clear(); }

    // `hart` retires a load: word address, aligned byte mask, aligned data.
    // Returns "" when it agrees, else what is wrong.
    std::string retired(int hart, std::uint32_t word_address, std::uint8_t mask, std::uint32_t data) {
        if (pending[hart].empty()) return describe(hart, word_address, "retired a load that was never performed");
        const Performed p = pending[hart].front();
        pending[hart].pop_front();
        if (p.address != (word_address & ~3u))
            return describe(hart, word_address, "retired a load of another word than its oldest performed one ("
                            + hex(p.address) + ", cycle " + std::to_string(p.cycle) + ")");
        std::uint32_t bytes = 0;
        for (int b = 0; b < 4; ++b) if (mask & (1u << b)) bytes |= 0xFFu << (8 * b);
        if ((data & bytes) != (p.value & bytes))
            return describe(hart, word_address, "read " + hex(data & bytes) + ", but the word held "
                            + hex(p.value & bytes) + " when the load was performed (cycle " + std::to_string(p.cycle) + ")");
        ++checked;
        return "";
    }

    bool idle() const { return pending[0].empty() && pending[1].empty(); }
    std::uint64_t checked = 0;
    Memory memory;

private:
    struct Performed { std::uint32_t address, value; std::uint64_t cycle; };
    static std::string hex(std::uint64_t v) { std::ostringstream o; o << "0x" << std::hex << v; return o.str(); }
    static std::string describe(int hart, std::uint32_t address, const std::string& what) {
        return "hart " + std::to_string(hart) + " at " + hex(address) + ": " + what;
    }
    std::deque<Performed> pending[2];
};

}  // namespace fabric
