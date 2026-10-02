// Randomized unit test of the Aster core fetch unit
// (rtl/aster_core/aster_core_fetch.sv) against the two-port shell's memory
// model (shell_ports.h), with the fetch unit's own assertions enabled.
//
// The harness plays Decode and Execute: it takes offered instructions when its
// Decode slot is free or advancing, fires Decode redirects from the
// instruction in its Decode slot (once per instruction) and Execute redirects
// from the instruction in its Execute slot, killing both slots as the core's
// squash does (an instruction taken in the cycle an Execute redirect resolves
// is wrong-path and not checked), and trap redirects — a trap or interrupt at
// the commit point, which the core sends through the same registered path —
// in any cycle but one with an Execute redirect, including the cycle an
// Execute redirect's target is presented (milestone 18.3). Checked in every run:
// - Decode receives exactly the program-order stream: each instruction taken
//   (outside the cycle an Execute redirect resolves) is at the address after
//   the previous one, or at the latest redirect's target (an Execute or trap
//   redirect winning over a Decode redirect in the same cycle), with that address's
//   word and the fault flag of an address outside memory; nothing is offered
//   in a Decode redirect's cycle or in the cycle after an Execute redirect;
// - the request protocol (shell::StableCheck, excused only while
//   `redirecting`), and never more fetches in flight than the memory allows
//   (limits of 2, 3, 4 and 16, the last above the unit's own maximum of twelve).
// A deterministic run reaches the unit's deepest state, twelve fetches in
// flight (nine discarded), and checks the stream after it. Deterministic runs then
// check the rates and penalties of docs/cpu.md §4:
// one instruction per cycle with no stalls (at both memory latencies), a
// Decode redirect's target offered two cycles after the redirect cycle and an
// Execute redirect's four — the same with a one-cycle memory as with a
// two-cycle one.
#include "Vaster_core_fetch.h"
#include "verilated.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <random>
#include <string>
#include <utility>

#include "shell_ports.h"

namespace {
constexpr std::uint32_t kBase = 0x80000000u, kBytes = 0x10000u;
int failures = 0;

// Events the random runs must reach (summed over all runs).
struct Coverage {
    long d_redirect_while_waiting = 0, e_redirect_while_waiting = 0, both_redirects = 0, trap_while_presented = 0;
    long fault_taken = 0, decode_stalled_with_offer = 0, three_in_flight = 0, four_or_more_in_flight = 0;
    long most_in_flight = 0;
} coverage;

bool mapped(std::uint32_t addr) { return addr >= kBase && addr - kBase < kBytes; }
std::uint32_t word(std::uint32_t addr) { return (addr * 2654435761u) ^ 0x5a5a1234u; }

struct Config {
    int latency;
    std::size_t max_inflight;
    int stall_percent;        // chance per cycle that ready is low, and extra response delay 0-2
    int take_percent;         // chance per cycle that an occupied Decode slot advances
    int d_redirect_percent;   // chance per cycle of a Decode redirect from an eligible instruction
    int e_redirect_percent;   // chance per cycle of an Execute redirect from the Execute slot
    int unmapped_percent;     // chance that a redirect target lies outside memory
    std::uint32_t seed;
    int cycles;
    int trap_percent = 0;     // chance per cycle of a trap redirect (not in an Execute redirect's cycle)
};

struct Slot {
    bool valid = false;
    std::uint32_t pc = 0;
    bool redirected = false;
};

void fail(const Config& c, int cycle, const std::string& what) {
    if (failures < 20)
        std::printf("FAIL: %s (cycle %d, latency %d, max_inflight %zu, seed %u)\n", what.c_str(), cycle,
                    c.latency, c.max_inflight, c.seed);
    ++failures;
}

struct Harness {
    Vaster_core_fetch f;
    shell::Port port;
    shell::StableCheck stable;
    std::mt19937 rng;
    std::mt19937 garbage{0x9e3779b9u};
    Config c;
    Slot d, e;
    std::uint32_t expected = kBase;
    bool e_presented = false;                 // an Execute redirect resolved in the last cycle
    int cycle = 0;
    long taken = 0;

    explicit Harness(const Config& config) : rng(config.seed), c(config) {
        f.clk = 0; f.rst_n = 0;
        f.i_req_ready = 0; f.i_rsp_valid = 0; f.i_rsp_data = 0; f.i_rsp_error = 0;
        f.d_redirect = 0; f.d_target = 0; f.e_flush = 0; f.e_target = 0; f.d_take = 0;
        for (int i = 0; i < 4; ++i) { f.clk = 0; f.eval(); f.clk = 1; f.eval(); }
        f.clk = 0; f.eval();
        f.rst_n = 1;
    }

    int percent() { return int(rng() % 100); }
    std::uint32_t target() {
        if (percent() < c.unmapped_percent) return kBase + kBytes + 4 * (rng() % 64);   // past the end of memory
        return kBase + 4 * (rng() % (kBytes / 4));
    }

    // One cycle. Forced events (for the deterministic runs) override the random
    // ones; `hold` freezes the memory's answers and stalls Decode.
    // Returns the address offered in this cycle, or ~0u if none.
    std::uint32_t step(bool trap = false, int force_d = -1, int force_e = -1, std::uint32_t forced_target = 0,
                       bool hold = false) {
        // Low phase: memory answers and readiness, then this cycle's decisions.
        f.i_rsp_valid = port.responding() && !hold;
        f.i_rsp_data = port.responding() ? port.owed.front().data : std::uint32_t(garbage());
        f.i_rsp_error = port.responding() ? port.owed.front().error : garbage() & 1u;
        f.i_req_ready = port.remaining() < c.max_inflight && !(percent() < c.stall_percent);
        const bool d_fire = force_d >= 0 ? force_d == 1 && d.valid && !d.redirected
                                         : d.valid && !d.redirected && percent() < c.d_redirect_percent;
        // A trap (from M1, any cycle) and an Execute redirect never coincide.
        const bool t_fire = trap || (force_e < 0 && percent() < c.trap_percent);
        const bool e_fire = !t_fire && (force_e >= 0 ? force_e == 1 && e.valid
                                                     : e.valid && percent() < c.e_redirect_percent);
        const std::uint32_t d_to = force_d == 1 ? forced_target : target();
        const std::uint32_t e_to = force_e == 1 || trap ? forced_target : target();
        const bool advance = hold ? !d.valid
                                  : !d.valid || (force_d >= 0 || force_e >= 0 ? true : percent() < c.take_percent);
        f.d_redirect = d_fire; f.d_target = d_to >> 2;
        f.e_flush = e_fire || t_fire; f.e_target = e_to >> 2;
        f.d_take = advance;
        f.eval();

        // Checks on the settled request and offer.
        const std::uint32_t addr = std::uint32_t(f.i_req_addr) << 2;
        const std::string violation =
            stable.cycle({bool(f.i_req_valid), std::uint32_t(f.i_req_addr), 0, 0, 0}, f.i_req_ready, f.redirecting);
        if (!violation.empty()) fail(c, cycle, "instruction request " + violation);
        if ((d_fire || e_presented) && f.f_valid) fail(c, cycle, "an instruction offered in a redirect cycle");
        if (d_fire && stable.waiting) ++coverage.d_redirect_while_waiting;
        if (e_fire && stable.waiting) ++coverage.e_redirect_while_waiting;
        if (d_fire && e_fire) ++coverage.both_redirects;
        if (t_fire && e_presented) ++coverage.trap_while_presented;
        if (f.f_valid && !advance) ++coverage.decode_stalled_with_offer;
        const bool take = f.f_valid && advance;
        const std::uint32_t offered = f.f_valid ? std::uint32_t(f.f_pc) << 2 : ~0u;
        if (take && !e_fire && !t_fire) {
            const std::uint32_t pc = std::uint32_t(f.f_pc) << 2;
            if (pc != expected) {
                char text[96];
                std::snprintf(text, sizeof text, "Decode took 0x%08x, expected 0x%08x", pc, expected);
                fail(c, cycle, text);
            }
            if (bool(f.f_error) != !mapped(pc)) fail(c, cycle, "fault flag differs from the address map");
            if (!mapped(pc)) ++coverage.fault_taken;
            if (mapped(pc) && f.f_insn != word(pc)) fail(c, cycle, "instruction word differs from memory");
            expected = pc + 4;
            ++taken;
        }
        if (e_fire || t_fire) expected = e_to;  // an Execute or trap redirect wins over a Decode redirect
        else if (d_fire) expected = d_to;

        // Edge.
        const bool accept = f.i_req_valid && f.i_req_ready;
        f.clk = 1; f.eval();
        if (!hold) port.advance();
        if (accept) {
            if (port.owed.size() >= c.max_inflight) fail(c, cycle, "more fetches in flight than the memory allows");
            if (port.owed.size() == 2) ++coverage.three_in_flight;
            if (port.owed.size() >= 3) ++coverage.four_or_more_in_flight;
            coverage.most_in_flight = std::max(coverage.most_in_flight, long(port.owed.size()) + 1);
            port.accept(c.latency, percent() < c.stall_percent ? int(rng() % 3) : 0,
                        mapped(addr) ? word(addr) : std::uint32_t(garbage()), !mapped(addr));
        }
        // The harness's Decode and Execute slots, killed by an Execute or trap redirect.
        if (e_fire || t_fire) {
            d = Slot{}; e = Slot{};
        } else {
            if (d_fire) d.redirected = true;
            if (advance) {
                e = Slot{d.valid, d.pc, false};
                d = take ? Slot{true, std::uint32_t(f.f_pc) << 2, false} : Slot{};
            }
        }
        e_presented = e_fire || t_fire;
        f.clk = 0; f.eval();
        ++cycle;
        return offered;
    }
};

void random_run(const Config& c) {
    Harness h(c);
    for (int i = 0; i < c.cycles && failures < 20; ++i) h.step();
    if (h.taken < c.cycles / 20) fail(c, h.cycle, "Decode starved (" + std::to_string(h.taken) + " taken)");
}

// Deterministic: no stalls, Decode always advancing; returns the offers per cycle.
void rates_and_penalties(int latency) {
    const Config c{latency, 2, 0, 100, 0, 0, 0, 1, 0};
    Harness h(c);
    for (int i = 0; i < 20; ++i) h.step(false, 0, 0);
    long before = h.taken;
    for (int i = 0; i < 100; ++i) h.step(false, 0, 0);
    if (h.taken - before != 100) fail(c, h.cycle, "not one instruction per cycle without stalls");

    // Decode redirect in cycle r: nothing offered in r and r+1, the target in r+2.
    const std::uint32_t t1 = kBase + 0x4000;
    h.step(false, 1, 0, t1);
    const std::uint32_t a = h.step(false, 0, 0), b = h.step(false, 0, 0);
    if (a != ~0u || b != t1) fail(c, h.cycle, "a Decode redirect does not cost exactly two cycles");
    for (int i = 0; i < 10; ++i) h.step(false, 0, 0);

    // Execute redirect in cycle r: nothing (right-path) offered in r+1 and r+2, the target in r+3.
    const std::uint32_t t2 = kBase + 0x8000;
    h.step(false, 0, 1, t2);
    const std::uint32_t p = h.step(false, 0, 0), q = h.step(false, 0, 0), r = h.step(false, 0, 0);
    if (p != ~0u || q != ~0u || r != t2) fail(c, h.cycle, "an Execute redirect does not cost exactly four cycles");
    for (int i = 0; i < 10; ++i) h.step(false, 0, 0);
}

// Deterministic: the most fetches the unit can have in flight. With answers
// frozen and Decode stalled, three live fetches fill the room; a Decode
// redirect discards them while its target stream adds three more; an older
// instruction's Execute redirect then discards all six and its stream adds
// three; that instruction's trap (it waits in M1) discards those nine and its
// stream adds three: twelve in flight, nine of them discarded. Released,
// Decode must receive exactly the trap target's stream.
void deepest_redirects() {
    const Config c{2, 16, 0, 100, 0, 0, 0, 3, 0};
    Harness h(c);
    for (int i = 0; i < 12; ++i) h.step(false, 0, 0);
    for (int i = 0; i < 3; ++i) h.step(false, 0, 0, 0, true);                 // three live in flight
    h.step(false, 1, 0, kBase + 0x2000, true);                               // Decode redirect: 3 discarded
    for (int i = 0; i < 3; ++i) h.step(false, 0, 0, 0, true);                 // its stream: 3 more
    h.step(false, 0, 1, kBase + 0x6000, true);                               // Execute redirect
    for (int i = 0; i < 4; ++i) h.step(false, 0, 0, 0, true);                 // 6 discarded, its stream: 3 more
    h.step(true, 0, 0, kBase + 0xa000, true);                                // its trap
    for (int i = 0; i < 4; ++i) h.step(false, 0, 0, 0, true);                 // 9 discarded, its stream: 3 more
    const long in_flight = long(h.port.owed.size());
    if (in_flight != 12) fail(c, h.cycle, "the deepest redirect sequence did not reach twelve fetches in flight (" +
                                          std::to_string(in_flight) + ")");
    const long before = h.taken;
    for (int i = 0; i < 40; ++i) h.step(false, 0, 0);
    if (h.taken - before < 20) fail(c, h.cycle, "Decode starved after the deepest redirect sequence");
}
}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    int runs = 0;
    for (int latency : {1, 2}) {
        for (std::size_t inflight : {std::size_t(2), std::size_t(3), std::size_t(4), std::size_t(16)}) {
            for (std::uint32_t seed = 1; seed <= 12; ++seed) {
                const int stall = seed % 3 == 0 ? 0 : seed % 3 == 1 ? 25 : 60;
                const int take = seed % 4 == 0 ? 100 : 30 + int(seed * 7 % 60);
                random_run({latency, inflight, stall, take, 8, 5, 10, seed * 7919u + latency, 20000, 2});
                ++runs;
            }
        }
    }
    const std::pair<const char*, long> reached[] = {
        {"Decode redirect while a fetch waits", coverage.d_redirect_while_waiting},
        {"Execute redirect while a fetch waits", coverage.e_redirect_while_waiting},
        {"both redirects in one cycle", coverage.both_redirects},
        {"a trap redirect while an Execute redirect's target is presented", coverage.trap_while_presented},
        {"a faulting fetch taken by Decode", coverage.fault_taken},
        {"Decode stalled with an instruction offered", coverage.decode_stalled_with_offer},
        {"three fetches in flight", coverage.three_in_flight},
        {"four or more fetches in flight (discarded ones included)", coverage.four_or_more_in_flight}};
    for (const auto& [event, count] : reached) {
        if (count == 0) { std::printf("FAIL: the random runs never reached: %s\n", event); ++failures; }
    }
    rates_and_penalties(1);
    rates_and_penalties(2);
    deepest_redirects();
    std::printf("%s: aster fetch unit — %d random runs of 20,000 cycles with Decode, Execute and trap redirects (at "
                "most %ld fetches in flight); one fetch per cycle and the 2/4-cycle redirect penalties at both memory "
                "latencies\n",
                failures ? "FAIL" : "PASS", runs, coverage.most_in_flight);
    return failures ? 1 : 0;
}
