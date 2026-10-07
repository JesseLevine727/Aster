// The fabric shell (milestone 20.0; docs/soc.md §10.1): a Phase 20 fabric
// (shell_fabric.sv: ref_fabric in 20.0, aster_fabric from 20.1) driven by
// seven random requesters — the instruction caches I0 and I1, the data
// caches D0 and D1, the NPU's port N, the DMA's ports R and W — each obeying
// its port's protocol (cpu.md §5, npu.md §5.1): a request held unchanged
// until accepted, at most two answers owed (a new request may come in the
// cycle an answer returns); while a hart is held in reset its requesters keep
// presenting requests, which the DUT must ignore. Every outcome is checked against fabric_ref.h's
// reference, cycle by cycle:
// - every answer two plus WAIT cycles after its acceptance, in order, and
//   none else (ANSWER_TIMING); its data — a word or unit as the reference
//   held it at acceptance, an AMO's old value, an I/O load's device word
//   (DATA_MISMATCH), an sc's outcome under soc.md §4.5's reservation rule
//   (SC_MISMATCH); the error bit in the cycle after each acceptance, set only
//   for an access outside main memory (ERROR_MISMATCH);
// - every write's line on every other data cache's snoop port for its writer
//   in the cycle after the write takes effect (an AMO's two edges after its
//   acceptance), and no other snoop (SNOOP_MISMATCH);
// - a data cache's access outside main memory on the I/O bus in its cycle,
//   one a cycle (IO_MISMATCH);
// - ev_resv_end (for the fabric counters, soc.md §8): a hart's reservation
//   ended by another requester's write, in the cycle after (EVENT_MISMATCH);
// - never a read and a write, or two writes, of one 8-byte unit in a cycle
//   (SAME_UNIT); with a banked DUT (chk_banks > 0) at most two accesses and
//   one write a bank a cycle (BANK_RULE); until an AMO's write, no other
//   write or AMO in its bank and nothing from its data cache (AMO_RULE);
// - no request waits more than 256 cycles (STARVED); while a request waits,
//   no other requester is accepted ahead of it, in its bank, more than
//   +overtake_limit times (default 16, the reference fabric's limit; UNFAIR:
//   soc.md §4.4's rotation); in
//   the solo modes (one hart requests) none waits at all, but for its AMO's
//   hold and the unit exception (SOLO_SLOWED, soc.md §4.3);
// - a writer makes at most one write a cycle, an AMO's write included
//   (TWO_WRITES: each snoop port carries one line a cycle);
// - at the end the whole memory is read back through R (the reference
//   compared unit by unit) and nothing is owed.
//
// Stimulus: +mode= mix (default: random addresses over main memory, a mix of
// every operation), hot (most accesses to eight shared lines: conflicts,
// reservations ended by others, AMOs on one word), stream (N, R and W
// sequential), dense or sparse (issue rates), errors (some I, N, R and W
// accesses outside main memory), reset (hart 1 held in reset at random,
// exceptions on both harts; a held hart's requesters keep presenting
// requests, which the DUT must ignore), solo and solo1 (only hart 0's, or
// hart 1's, caches request, half their accesses on four lines both share),
// hammer (every requester at nearly every cycle on one bank, which changes
// every 1,000 cycles, and on four of its units: fairness and starvation),
// twin (only the data caches, loading the same lines word by word in step, as
// two harts' refills of shared data: where the DUT can take both, the
// reference or a fabric run with +second_chance=1, both must be taken whenever
// they load one bank, TWIN_SERIAL; the banked fabric has had no second chance
// since 20.2, and serves them in turn),
// and edges: directed scenarios, each request at a set cycle (all seven
// requesters on one bank; four banks written at once; one unit read and
// written together; a read of a unit two writers keep writing; both harts' AMOs on one word a cycle apart, with writes
// and reads of their bank meanwhile; a reservation ended by each kind of
// writer, by an exception in the lr's cycle and by a reset; sc without a
// reservation or on another word; hart 1 reset with an AMO in flight; both
// harts' I/O at once, streaming, and behind an AMO; errors from I, N, R and W at once; the
// NPU streaming beside a hart's stores), each checked like any request.
// +banks=<n> declares the DUT's banks (0: unbanked), which must equal its
// chk_banks (BANKS_MISMATCH), so a DUT cannot turn the bank checks off.
// +cycles=<n> (default 100,000) +seed=<n>. With +require_coverage a run
// fails unless it hits every coverage bin that applies to its DUT and mode.
//
// The error bit is checked in the cycle after each acceptance only: cpu.md
// §5 gives it no meaning in other cycles.
//
// +selftest=N plants a fault the shell must report: 1-4 are the DUT
// wrapper's (shell_fabric.sv); 5 a D0 load's data misread (DATA_MISMATCH); 6
// a snoop to D1 from the NPU missed (SNOOP_MISMATCH); 7 an N answer seen a
// cycle late (ANSWER_TIMING); 8 an sc's outcome misread (SC_MISMATCH); 9 the
// reference keeps every reservation another requester's write ends (the
// DUT's reservation-ended event is then unexpected: EVENT_MISMATCH); 10
// the reference computes every AMO wrongly (DATA_MISMATCH);
// 11 a reference byte corrupted before the read-back (DATA_MISMATCH); 12 is the wrapper's (the
// DUT reported with other banks than it has: BANK_RULE). The
// rules no self-test reaches (SAME_UNIT, AMO_RULE, TWO_WRITES, IO_MISMATCH,
// UNFAIR) are proven by planted bugs in the DUT (scripts/fabric_mutants.py).
//
//   fabric_shell +banks=<n> +seed=<n> [+cycles=<n>] [+mode=<m>] [+overtake_limit=<n>] [+require_coverage]
//                [+selftest=<n>]
// Prints one line: FABRIC <PASS|status> dut=... wait=... mode=... cycles=... coverage=...
#include "Vfabric_shell.h"
#include "verilated.h"

#include "fabric_ref.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string plusarg(const char* name) {
    const char* value = Verilated::commandArgsPlusMatch(name);
    const std::string text = value ? value : "";
    const std::string prefix = std::string("+") + name + "=";
    return text.rfind(prefix, 0) == 0 ? text.substr(prefix.size()) : "";
}
bool plusflag(const char* name) { return Verilated::commandArgsPlusMatch(name)[0] != '\0'; }
std::string hex(std::uint64_t value) {
    std::ostringstream out;
    out << "0x" << std::hex << value;
    return out.str();
}

constexpr std::uint32_t MEM_BASE = 0x80000000u, MEM_BYTES = 96u * 1024u;
constexpr std::uint32_t IO_BASE = 0x20000000u, IO_BYTES = 4096u;
constexpr unsigned MAX_WAIT = 256;

enum Req : int { I0, D0, I1, D1, N, R, W, NREQ };
const char* const kName[NREQ] = {"I0", "D0", "I1", "D1", "N", "R", "W"};
int hart_of(int k) { return k == I0 || k == D0 ? 0 : k == I1 || k == D1 ? 1 : -1; }
bool is_d(int k) { return k == D0 || k == D1; }
int d_of(int h) { return h == 0 ? D0 : D1; }
int i_of(int h) { return h == 0 ? I0 : I1; }
int snoop_port(int writer) { return is_d(writer) ? 0 : writer == N ? 1 : 2; }

struct Pres {                                   // the request a requester presents
    bool valid = false;
    std::uint32_t addr = 0;                     // byte address
    int op = fabric::LOAD;                      // D's op; N and W writes are STORE
    std::uint64_t wdata = 0;
    std::uint8_t be = 0;                        // D: 4 bits; N and W: 8
    std::uint64_t since = 0;
    bool ghost = false;                         // presented by a hart held in reset: the DUT must ignore it
};
enum Check { NONE, DATA, SC_RESULT, IO_DATA };
struct Owed {
    std::uint64_t accept = 0;
    bool error = false;
    Check check = NONE;
    std::uint64_t data = 0;
    bool load = false;                          // a D load or lr (self-test 5)
};
struct Amo {
    std::uint64_t accept;
    int hart, op;
    std::uint32_t addr, operand;
};

class Shell {
public:
    Shell(Vfabric_shell& dut, unsigned seed)
        : d(dut), rng(seed), mem(MEM_BASE, MEM_BYTES), io(IO_BASE, IO_BYTES) {
        mode = plusarg("mode").empty() ? "mix" : plusarg("mode");
        cycles = plusarg("cycles").empty() ? 100000 : std::stoull(plusarg("cycles"));
        selftest = plusarg("selftest").empty() ? 0 : std::stoi(plusarg("selftest"));
        solo_hart = mode == "solo" ? 0 : mode == "solo1" ? 1 : -1;
        solo = solo_hart >= 0;
        overtake_limit = plusarg("overtake_limit").empty() ? 16 : std::stoul(plusarg("overtake_limit"));
        declared_banks = plusarg("banks").empty() ? -1 : std::stoi(plusarg("banks"));
        second_chance = !plusarg("second_chance").empty() && plusarg("second_chance") != "0";
        p_issue = mode == "dense" || mode == "hammer" || mode == "twin" ? 0.95 : mode == "sparse" ? 0.12 : 0.5;
        for (int i = 0; i < 8; ++i) hot_lines.push_back(MEM_BASE + 16u * std::uint32_t(rng() % (MEM_BYTES / 16)));
        for (int i = 0; i < 4; ++i) solo_lines.push_back(MEM_BASE + 16u * std::uint32_t(rng() % (MEM_BYTES / 16)));
        for (int k = 0; k < NREQ; ++k) stream_at[k] = MEM_BASE + 8u * std::uint32_t(rng() % (MEM_BYTES / 8));
    }

    int run();

private:
    // ---- stimulus ----
    double uni() { return std::uniform_real_distribution<double>(0.0, 1.0)(rng); }
    std::uint32_t main_addr(int k, std::uint32_t align) {
        std::uint32_t a;
        if (mode == "hammer") {                     // two lines (four units) of this epoch's bank
            const std::uint32_t b = std::uint32_t(cycle / 1000) % 4, line = 40 + std::uint32_t(rng() % 2);
            a = MEM_BASE + 16u * (4u * line + b) + std::uint32_t(rng() % 16);
        } else if (mode == "twin" && is_d(k)) {    // the next word of the lines both data caches walk
            a = MEM_BASE + 4u * twin_at[hart_of(k)]++;
            if (twin_at[hart_of(k)] >= MEM_BYTES / 4) twin_at[hart_of(k)] = 0;
        } else if (mode == "hot" && uni() < 0.7) a = hot_lines[rng() % hot_lines.size()] + std::uint32_t(rng() % 16);
        else if (solo && uni() < 0.5) a = solo_lines[rng() % solo_lines.size()] + std::uint32_t(rng() % 16);
        else if (mode == "stream" && (k == N || k == R || k == W)) {
            a = stream_at[k];
            stream_at[k] = stream_at[k] + 8 >= MEM_BASE + MEM_BYTES ? MEM_BASE : stream_at[k] + 8;
        } else a = MEM_BASE + std::uint32_t(rng() % MEM_BYTES);
        return a & ~(align - 1);
    }
    std::uint32_t outside() { return 0x90000000u + 8u * std::uint32_t(rng() % 4096); }
    bool erroneous() { return mode == "errors" && uni() < 0.03; }
    bool hart_requests(int k) const {
        const int h = hart_of(k);
        if (solo && !sweeping && h != solo_hart) return false;
        if (mode == "twin" && !sweeping && !is_d(k)) return false;
        return h < 0 || hart_up[h];
    }
    void new_request(int k);
    void build_edges();
    struct Step { std::uint64_t at; Pres p; };
    std::deque<Step> script[NREQ];              // +mode=edges: each requester's requests, each from its cycle
    std::map<std::uint64_t, unsigned> script_reset;      // cycle -> cycles hart 1 is held
    std::map<std::uint64_t, unsigned> script_exception;  // cycle -> harts (bit h)
    std::uint64_t script_end = 0;

    // ---- per cycle ----
    void drive();
    bool check_outputs();
    bool check_acceptance();
    void take_effect();
    bool fail(const std::string& status, const std::string& detail) {
        if (failure.empty()) { failure = status; failure_detail = detail; }
        return false;
    }
    void hit(const std::string& bin) { bins.insert(bin); }
    unsigned bank(std::uint32_t a) const { return banks ? (a >> 4) & (banks - 1) : 0; }

    // DUT views
    bool rsp_valid(int k) const;
    std::uint64_t rsp_data(int k) const;
    bool rsp_error(int k) const;
    bool req_ready(int k) const;

    Vfabric_shell& d;
    std::mt19937_64 rng;
    fabric::Memory mem, io;
    fabric::Reservation resv[2];
    std::string mode, failure, failure_detail;
    std::uint64_t cycles = 0, cycle = 0;
    int selftest = 0;
    bool solo = false, draining = false, sweeping = false;
    // whether both data caches' loads to one bank, nothing else asking, are taken together: the
    // reference's (no banks), or a banked fabric declared with a second chance (+second_chance=1;
    // 20.1's, removed in 20.2)
    bool second_chance = false, both_d_together = true;
    int solo_hart = -1, declared_banks = -1;
    unsigned overtake_limit = 16;
    std::vector<std::uint32_t> solo_lines;
    std::uint64_t overtaken[NREQ][NREQ] = {}, max_overtake = 0;
    std::uint64_t dropped_due = 0;              // the latest cycle an answer dropped at hart 1's reset was due
    double p_issue = 0.5;
    unsigned banks = 0, wait = 0, latency = 2;
    std::vector<std::uint32_t> hot_lines;
    std::uint32_t stream_at[NREQ] = {};
    std::uint32_t twin_at[2] = {};
    std::uint32_t sweep_at = MEM_BASE;
    std::uint32_t last_lr[2] = {MEM_BASE, MEM_BASE};

    Pres pres[NREQ];
    std::deque<Owed> owed[NREQ];
    bool acc[NREQ] = {};
    bool hart_up[2] = {true, true}, hart_exc[2] = {false, false};
    unsigned reset_left = 0;
    std::vector<Amo> amos;                     // accepted, write not yet taken effect
    bool exp_snoop[2][3] = {}, next_snoop[2][3] = {};
    bool exp_ev[2] = {}, next_ev[2] = {};
    std::uint32_t exp_line[2][3] = {}, next_line[2][3] = {};
    std::uint32_t io_rdata = 0, io_rdata_next = 0;
    std::vector<fabric::Write> writes;         // the writes taking effect at this cycle's edge
    std::uint64_t accepted_count[NREQ] = {}, max_wait = 0;
    std::set<std::string> bins;
    int selftest_left = 1;                      // one-shot self-tests
};

void Shell::new_request(int k) {
    Pres& p = pres[k];
    p = Pres{};
    p.valid = true;
    p.since = cycle;
    const int h = hart_of(k);
    if (sweeping) {                             // the final read-back through R
        p.addr = sweep_at;
        sweep_at += 8;
        return;
    }
    switch (k) {
    case I0: case I1:
        p.addr = erroneous() ? outside() : main_addr(k, 4);
        break;
    case D0: case D1: {
        p.wdata = std::uint32_t(rng());
        if (uni() < 0.12) {                     // the I/O bus
            p.addr = IO_BASE + 4u * std::uint32_t(rng() % (IO_BYTES / 4));
            p.op = uni() < 0.5 ? fabric::LOAD : fabric::STORE;
            p.be = p.op == fabric::LOAD ? 0xF : std::uint8_t(1 + rng() % 15);
            break;
        }
        if (mode == "twin") { p.op = fabric::LOAD; p.addr = main_addr(k, 4); p.be = 0xF; break; }
        const double r = uni();
        p.op = r < 0.38 ? fabric::LOAD : r < 0.66 ? fabric::STORE : r < 0.73 ? fabric::LR : r < 0.82 ? fabric::SC
             : int(fabric::SWAP + rng() % 9);
        p.addr = main_addr(k, 4);
        p.be = 0xF;
        if (p.op == fabric::STORE) {
            const unsigned size = rng() % 3;
            if (size == 0) { p.addr |= std::uint32_t(rng() % 4); p.be = std::uint8_t(1u << (p.addr & 3u)); }
            else if (size == 1) { p.addr |= 2u * std::uint32_t(rng() % 2); p.be = std::uint8_t(3u << (p.addr & 2u)); }
        }
        if (p.op == fabric::SC && uni() < 0.75) p.addr = last_lr[h];
        if (p.op == fabric::LR) last_lr[h] = p.addr;
        break;
    }
    case N:
        p.addr = erroneous() ? outside() : main_addr(k, 8);
        if (uni() < 0.3) {
            p.op = fabric::STORE;
            p.wdata = rng();
            p.be = uni() < 0.75 ? (uni() < 0.5 ? 0x0F : 0xF0) : std::uint8_t(1 + rng() % 255);
        }
        break;
    case R:
        p.addr = erroneous() ? outside() : main_addr(k, 8);
        break;
    case W:
        p.addr = erroneous() ? outside() : main_addr(k, 8);
        p.op = fabric::STORE;
        p.wdata = rng();
        p.be = std::uint8_t(1 + rng() % 255);
        break;
    default: break;
    }
}

// +mode=edges: the directed scenarios, one every 64 cycles.
void Shell::build_edges() {
    auto at_bank = [](unsigned bank, unsigned line, unsigned word) {   // word w of the line-th line of a bank
        return MEM_BASE + 16u * (4u * line + bank) + 4u * word;
    };
    std::uint64_t t = 8;
    auto req = [&](int k, std::uint64_t when, std::uint32_t addr, int op = fabric::LOAD, std::uint64_t wdata = 0,
                   std::uint8_t be = 0) {
        Pres p;
        p.addr = addr; p.op = op; p.wdata = wdata;
        p.be = be ? be : (is_d(k) ? 0xF : (op == fabric::STORE ? 0xFF : 0));
        script[k].push_back({when, p});
    };
    auto next = [&]() { t += 64; };
    std::uint64_t seed = 0x5eed;
    auto val = [&]() { seed = seed * 6364136223846793005ull + 1442695040888963407ull; return seed >> 17; };
    // 1. all seven requesters on one bank in one cycle, then all writing it; first a lone request
    // moves the bank's round-robin pointer, so each bank's round starts from another requester
    for (unsigned b = 0; b < 4; ++b) {
        req(int(b), t, at_bank(b, 20, 0));
        t += 4;
        req(I0, t, at_bank(b, 1, 0)); req(I1, t, at_bank(b, 2, 0)); req(D0, t, at_bank(b, 3, 0));
        req(D1, t, at_bank(b, 4, 0)); req(N, t, at_bank(b, 5, 0)); req(R, t, at_bank(b, 6, 0));
        req(W, t, at_bank(b, 7, 0), fabric::STORE, val(), 0xFF);
        next();
        req(D0, t, at_bank(b, 3, 1), fabric::STORE, val()); req(D1, t, at_bank(b, 4, 1), fabric::STORE, val());
        req(N, t, at_bank(b, 5, 0), fabric::STORE, val(), 0xF0); req(W, t, at_bank(b, 7, 0), fabric::STORE, val(), 0x3C);
        req(I0, t, at_bank(b, 1, 0)); req(I1, t, at_bank(b, 2, 0)); req(R, t, at_bank(b, 6, 0));
        next();
    }
    // 2. four banks written in one cycle (every snoop port at once)
    for (int i = 0; i < 4; ++i) {
        req(D0, t, at_bank(i, 8, 0), fabric::STORE, val()); req(D1, t, at_bank((i + 1) % 4, 8, 1), fabric::STORE, val());
        req(N, t, at_bank((i + 2) % 4, 8, 2), fabric::STORE, val(), 0xFF); req(W, t, at_bank((i + 3) % 4, 8, 3), fabric::STORE, val(), 0xFF);
        next();
    }
    // 3. one unit read and written in one cycle, by every pair of kinds
    req(I0, t, at_bank(1, 9, 0)); req(D0, t, at_bank(1, 9, 1), fabric::STORE, val()); next();
    req(R, t, at_bank(2, 9, 0)); req(W, t, at_bank(2, 9, 0), fabric::STORE, val(), 0x01); next();
    req(N, t, at_bank(3, 9, 0)); req(D1, t, at_bank(3, 9, 0), fabric::SC, val()); next();
    req(D1, t, at_bank(0, 9, 0)); req(N, t, at_bank(0, 9, 0), fabric::STORE, val(), 0x80); next();
    // 3b. a read of a unit two writers (and the DMA) keep writing: it must not wait forever
    for (unsigned i = 0; i < 24; ++i) {
        req(D0, t + i, at_bank(2, 17, 0), fabric::STORE, val()); req(D1, t + i, at_bank(2, 17, 0), fabric::STORE, val());
        req(W, t + i, at_bank(2, 17, 0), fabric::STORE, val(), 0x0F);
    }
    req(I1, t + 1, at_bank(2, 17, 1)); req(R, t + 2, at_bank(2, 17, 0)); req(N, t + 3, at_bank(2, 17, 0));
    t += 96;
    // 4. both harts' AMOs on one word a cycle apart; meanwhile a write held, reads of the word
    for (int op : {fabric::ADD, fabric::SWAP, fabric::MAX, fabric::MINU}) {
        const std::uint32_t w = at_bank(2, 10, 1);
        req(D0, t, w, op, val()); req(D1, t + 1, w, op, val());
        req(N, t + 1, at_bank(2, 11, 0), fabric::STORE, val(), 0xFF); req(I0, t + 1, w); req(R, t + 2, w);
        req(D0, t + 1, at_bank(0, 11, 0));       // its data cache waits for the AMO's write
        next();
    }
    // 5. a reservation ended by each kind of writer (and kept by a write beside it)
    struct Ender { int k; int op; std::uint8_t be; bool ends; };
    const Ender enders[] = {{D1, fabric::STORE, 0x1, true}, {D1, fabric::SC, 0xF, true}, {D1, fabric::ADD, 0xF, true},
                            {N, fabric::STORE, 0xF0, false}, {N, fabric::STORE, 0x0F, true}, {W, fabric::STORE, 0x01, true},
                            {W, fabric::STORE, 0x10, false}};
    for (const Ender& e : enders) {
        const std::uint32_t w = at_bank(3, 12, 0);
        req(D0, t, w, fabric::LR);
        if (e.op == fabric::SC) { req(D1, t, w, fabric::LR); req(D1, t + 3, w, fabric::SC, val()); }
        else req(e.k, t + 3, e.k == D1 ? w : (w & ~7u), e.op, val(), e.be);
        req(D0, t + 8, w, fabric::SC, val());
        next();
    }
    // 6. an lr in an exception's cycle; an lr before hart 1's reset
    req(D0, t, at_bank(1, 13, 0), fabric::LR); script_exception[t] = 1;
    req(D0, t + 4, at_bank(1, 13, 0), fabric::SC, val()); next();
    req(D1, t, at_bank(1, 13, 1), fabric::LR); script_reset[t + 3] = 2;
    req(D1, t + 8, at_bank(1, 13, 1), fabric::SC, val()); next();
    req(D0, t, at_bank(1, 13, 2), fabric::LR); script_exception[t + 2] = 1;
    req(D0, t + 5, at_bank(1, 13, 2), fabric::SC, val()); next();
    // 7. sc without a reservation, on another word, twice
    req(D1, t, at_bank(0, 14, 0), fabric::SC, val()); req(D1, t + 3, at_bank(0, 14, 1), fabric::LR);
    req(D1, t + 6, at_bank(0, 14, 2), fabric::SC, val()); req(D1, t + 9, at_bank(0, 14, 1), fabric::SC, val());
    next();
    // 8. hart 1 reset with an AMO in flight and answers owed; released at once
    req(D1, t, at_bank(2, 15, 0), fabric::ADD, val()); req(I1, t, at_bank(1, 15, 0)); script_reset[t + 1] = 1;
    req(D1, t + 6, at_bank(2, 15, 0)); next();
    // 9. both harts' I/O at once; an I/O access behind its hart's AMO
    req(D0, t, IO_BASE + 16, fabric::LOAD); req(D1, t, IO_BASE + 20, fabric::STORE, val());
    req(D0, t + 4, at_bank(3, 16, 0), fabric::XOR, val()); req(D0, t + 5, IO_BASE + 20); next();
    // 9b. both harts streaming I/O loads at once: the bus alternates
    for (unsigned i = 0; i < 32; ++i) { req(D0, t + i, IO_BASE + 4u * i); req(D1, t + i, IO_BASE + 0x200u + 4u * i); }
    t += 128;
    // 10. errors from I, N, R and W at once
    req(I0, t, 0x90000000u); req(N, t, 0x90000008u); req(R, t, 0x90000010u);
    req(W, t, 0x90000018u, fabric::STORE, val(), 0xFF); next();
    // 11. the NPU streaming across the banks beside hart 0's stores to them
    for (unsigned i = 0; i < 32; ++i) {
        req(N, t + i, MEM_BASE + 0x4000u + 8u * i);
        req(D0, t + i, MEM_BASE + 0x4000u + 16u * (i / 2) + 4u * (i % 2) + 8u, fabric::STORE, val());
    }
    t += 96;
    script_end = t;
}

bool Shell::rsp_valid(int k) const {
    switch (k) {
    case I0: return d.i0_rsp_valid; case I1: return d.i1_rsp_valid;
    case D0: return d.d0_rsp_valid; case D1: return d.d1_rsp_valid;
    case N: return d.n_rsp_valid; case R: return d.r_rsp_valid; default: return d.w_rsp_valid;
    }
}
std::uint64_t Shell::rsp_data(int k) const {
    switch (k) {
    case I0: return d.i0_rsp_data; case I1: return d.i1_rsp_data;
    case D0: return d.d0_rsp_rdata; case D1: return d.d1_rsp_rdata;
    case N: return d.n_rsp_rdata; case R: return d.r_rsp_rdata; default: return 0;
    }
}
bool Shell::rsp_error(int k) const {
    switch (k) {
    case I0: return d.i0_rsp_error; case I1: return d.i1_rsp_error;
    case D0: return d.d0_rsp_error; case D1: return d.d1_rsp_error;
    case N: return d.n_rsp_error; case R: return d.r_rsp_error; default: return d.w_rsp_error;
    }
}
bool Shell::req_ready(int k) const {
    switch (k) {
    case I0: return d.i0_req_ready; case I1: return d.i1_req_ready;
    case D0: return d.d0_req_ready; case D1: return d.d1_req_ready;
    case N: return d.n_req_ready; case R: return d.r_req_ready; default: return d.w_req_ready;
    }
}

void Shell::drive() {
    // hart 1's reset and exceptions (+mode=reset, or scripted)
    if (mode == "edges") {
        if (reset_left) --reset_left;
        if (script_reset.count(cycle)) reset_left = script_reset[cycle];
        const unsigned exc = script_exception.count(cycle) ? script_exception[cycle] : 0;
        hart_exc[0] = exc & 1u; hart_exc[1] = exc & 2u;
    } else if (mode == "reset" && !draining) {
        if (reset_left) --reset_left;
        else if (uni() < 0.002) reset_left = 1 + unsigned(rng() % 8);
        for (int h = 0; h < 2; ++h) hart_exc[h] = uni() < 0.003;
    } else {
        reset_left = 0;
        hart_exc[0] = hart_exc[1] = false;
    }
    const bool was_up = hart_up[1];
    hart_up[1] = reset_left == 0;
    if (was_up && !hart_up[1]) {
        if (!owed[I1].empty() || !owed[D1].empty()) hit("reset:owed");
        for (const Amo& a : amos) if (a.hart == 1) hit("reset:amo");
        for (int k : {I1, D1}) for (const Owed& o : owed[k]) dropped_due = std::max(dropped_due, o.accept + latency);
    }
    if (!was_up && hart_up[1] && dropped_due >= cycle) hit("reset:quick");
    for (int k = 0; k < NREQ; ++k) {
        const int h = hart_of(k);
        if (h >= 0 && !hart_up[h]) {           // a held hart's requesters present garbage, which the DUT ignores
            if (!sweeping && !draining && uni() < 0.5) { new_request(k); pres[k].ghost = true; }
            else pres[k].valid = false;
            continue;
        }
        if (pres[k].ghost) pres[k].valid = false;
        if (!hart_requests(k)) { pres[k].valid = false; continue; }
        // at most two answers owed, counting one due this cycle as returned (a new request may come with it)
        const std::size_t owing = owed[k].size() - (!owed[k].empty() && owed[k].front().accept + latency == cycle);
        if (pres[k].valid || owing >= 2) continue;
        if (sweeping) { if (k == R && sweep_at < MEM_BASE + MEM_BYTES) new_request(k); continue; }
        if (mode == "edges") {
            if (!script[k].empty() && script[k].front().at <= cycle) {
                pres[k] = script[k].front().p;
                pres[k].valid = true;
                pres[k].since = cycle;
                script[k].pop_front();
            }
            continue;
        }
        if (!draining && uni() < p_issue) new_request(k);
    }
    const Pres* p = pres;
    d.hart_rst_n = (hart_up[1] ? 2u : 0u) | 1u;
    d.hart_exception = (hart_exc[1] ? 2u : 0u) | (hart_exc[0] ? 1u : 0u);
    d.i0_req_valid = p[I0].valid; d.i0_req_addr = p[I0].addr >> 2;
    d.i1_req_valid = p[I1].valid; d.i1_req_addr = p[I1].addr >> 2;
    d.d0_req_valid = p[D0].valid; d.d0_req_op = p[D0].op; d.d0_req_addr = p[D0].addr;
    d.d0_req_wdata = std::uint32_t(p[D0].wdata); d.d0_req_be = p[D0].be;
    d.d1_req_valid = p[D1].valid; d.d1_req_op = p[D1].op; d.d1_req_addr = p[D1].addr;
    d.d1_req_wdata = std::uint32_t(p[D1].wdata); d.d1_req_be = p[D1].be;
    d.n_req_valid = p[N].valid; d.n_req_addr = p[N].addr >> 2; d.n_req_we = p[N].op == fabric::STORE;
    d.n_req_wdata = p[N].wdata; d.n_req_be = p[N].be;
    d.r_req_valid = p[R].valid; d.r_req_addr = p[R].addr >> 3;
    d.w_req_valid = p[W].valid; d.w_req_addr = p[W].addr >> 3; d.w_req_wdata = p[W].wdata; d.w_req_be = p[W].be;
    d.io_rsp_rdata = io_rdata;
}

bool Shell::check_outputs() {
    // answers, and the error bit in the cycle after each acceptance
    for (int k = 0; k < NREQ; ++k) {
        const int h = hart_of(k);
        if (h >= 0 && !hart_up[h]) continue;   // a hart in reset ignores its answers
        bool valid = rsp_valid(k);
        if (selftest == 7 && k == N && valid && selftest_left) { valid = false; selftest_left = 0; }
        const bool due = !owed[k].empty() && owed[k].front().accept + latency == cycle;
        if (valid != due)
            return fail("ANSWER_TIMING", std::string(kName[k]) + (valid ? " answered with nothing due" : " answer missing")
                        + " (accepted " + (owed[k].empty() ? std::string("-") : std::to_string(owed[k].front().accept)) + ")");
        if (due) {
            const Owed o = owed[k].front();
            owed[k].pop_front();
            std::uint64_t got = rsp_data(k);
            if (selftest == 5 && k == D0 && o.load && selftest_left) { got ^= 1; selftest_left = 0; }
            if (selftest == 8 && o.check == SC_RESULT && selftest_left) { got ^= 1; selftest_left = 0; }
            const std::uint64_t mask = (k == N || k == R) ? ~0ull : 0xFFFFFFFFull;
            if (o.check == SC_RESULT && (got & mask) != o.data)
                return fail("SC_MISMATCH", std::string(kName[k]) + " sc answered " + hex(got & mask) + ", expected "
                            + hex(o.data) + " (accepted " + std::to_string(o.accept) + ")");
            if ((o.check == DATA || o.check == IO_DATA) && (got & mask) != o.data)
                return fail("DATA_MISMATCH", std::string(kName[k]) + " answered " + hex(got & mask) + ", expected "
                            + hex(o.data) + " (accepted " + std::to_string(o.accept) + ")");
        }
        for (const Owed& o : owed[k])
            if (o.accept + 1 == cycle && rsp_error(k) != o.error)
                return fail("ERROR_MISMATCH", std::string(kName[k]) + " error " + std::to_string(rsp_error(k))
                            + " in the cycle after acceptance, expected " + std::to_string(o.error));
    }
    for (int h = 0; h < 2; ++h)
        if (bool((d.ev_resv_end >> h) & 1u) != exp_ev[h])
            return fail("EVENT_MISMATCH", "hart " + std::to_string(h) + "'s reservation-ended event "
                        + std::to_string((d.ev_resv_end >> h) & 1u) + ", expected " + std::to_string(exp_ev[h]));
    // snoops
    const std::uint8_t sv[2] = {d.s0_valid, d.s1_valid};
    const std::uint32_t sl[2][3] = {{d.s0_line0, d.s0_line1, d.s0_line2}, {d.s1_line0, d.s1_line1, d.s1_line2}};
    for (int c = 0; c < 2; ++c)
        for (int p = 0; p < 3; ++p) {
            bool valid = (sv[c] >> p) & 1u;
            if (selftest == 6 && c == 1 && p == 1 && valid && selftest_left) { valid = false; selftest_left = 0; }
            if (valid) hit("snoop:" + std::to_string(c) + ":" + std::to_string(p));
            if (valid != exp_snoop[c][p] || (valid && sl[c][p] != exp_line[c][p]))
                return fail("SNOOP_MISMATCH", "D" + std::to_string(c) + " port " + std::to_string(p) + ": "
                            + (valid ? "line " + hex(std::uint64_t(sl[c][p]) << 4) : std::string("none")) + ", expected "
                            + (exp_snoop[c][p] ? "line " + hex(std::uint64_t(exp_line[c][p]) << 4) : std::string("none")));
        }
    return true;
}

bool Shell::check_acceptance() {
    for (int k = 0; k < NREQ; ++k) {
        acc[k] = pres[k].valid && !pres[k].ghost && req_ready(k);
        if (pres[k].valid && !pres[k].ghost && !acc[k]) {
            const std::uint64_t waited = cycle - pres[k].since + 1;
            max_wait = std::max(max_wait, waited);
            if (waited > MAX_WAIT) return fail("STARVED", std::string(kName[k]) + " waited " + std::to_string(waited) + " cycles");
        }
    }
    // the I/O bus: the accepted access of a data cache outside main memory, one a cycle
    int io_k = -1, io_count = 0;
    for (int h = 0; h < 2; ++h) {
        const int k = d_of(h);
        if (pres[k].valid && !mem.contains(pres[k].addr) && pres[D0 + D1 - k].valid && !mem.contains(pres[D0 + D1 - k].addr)) hit("io_both");
        if (acc[k] && !mem.contains(pres[k].addr)) { io_k = k; ++io_count; }
    }
    if (io_count > 1) return fail("IO_MISMATCH", "two I/O accesses accepted in one cycle");
    if (bool(d.io_req_valid) != (io_count == 1))
        return fail("IO_MISMATCH", d.io_req_valid ? "an I/O access with none accepted" : "an accepted I/O access not on the bus");
    if (io_k >= 0) {
        const Pres& p = pres[io_k];
        if (d.io_req_hart != unsigned(hart_of(io_k)) || d.io_req_op != unsigned(p.op) || d.io_req_addr != p.addr
            || d.io_req_wdata != std::uint32_t(p.wdata) || d.io_req_be != p.be)
            return fail("IO_MISMATCH", std::string(kName[io_k]) + "'s I/O access presented wrongly");
        hit(p.op == fabric::LOAD ? "io_load" : "io_store");
    }
    return true;
}

void Shell::take_effect() {
    struct Access { int k; std::uint32_t addr; bool write; };
    std::vector<Access> reads;                 // main-memory reads accepted this cycle (lr, AMOs included)
    std::vector<int> mem_accepted;
    writes.clear();
    fabric::Reservation lr_set[2];
    bool sc_end[2] = {false, false};

    io_rdata_next = std::uint32_t(rng());     // the device's answer is garbage outside its cycle
    bool held[2] = {false, false};             // a data cache waiting for its AMO's write
    for (const Amo& a : amos) if (a.accept + 1 == cycle || a.accept + 2 == cycle) held[a.hart] = true;
    // AMO writes due at this edge (accepted two cycles ago)
    std::vector<Amo> due;
    for (const Amo& a : amos) if (a.accept + 2 == cycle) due.push_back(a);
    amos.erase(std::remove_if(amos.begin(), amos.end(), [&](const Amo& a) { return a.accept + 2 == cycle; }), amos.end());
    for (const Amo& a : due) {
        const std::uint32_t old = mem.word(a.addr);
        std::uint32_t value = fabric::amo_value(a.op, old, a.operand);
        if (selftest == 10) value += 1;
        writes.push_back({d_of(a.hart), a.addr & ~7u, std::uint8_t((a.addr & 4u) ? 0xF0 : 0x0F),
                          std::uint64_t(value) << ((a.addr & 4u) ? 32 : 0), fabric::BY_AMO});
    }

    for (int k = 0; k < NREQ; ++k) {
        if (!acc[k]) continue;
        ++accepted_count[k];
        const Pres& p = pres[k];
        Owed o;
        o.accept = cycle;
        const int h = hart_of(k);
        if (!mem.contains(p.addr) && !is_d(k)) {   // outside main memory: an error, no effect
            o.error = true;
            hit(std::string("error:") + (k == I0 || k == I1 ? "I" : kName[k]));
        } else if (is_d(k) && !mem.contains(p.addr)) {
            if (p.op == fabric::LOAD) o.check = IO_DATA;
            const std::uint32_t old = io.word(p.addr);
            if (p.op == fabric::STORE) {
                const std::uint64_t data = std::uint64_t(std::uint32_t(p.wdata)) << ((p.addr & 4u) ? 32 : 0);
                io.write({k, p.addr & ~7u, std::uint8_t(std::uint32_t(p.be) << ((p.addr & 4u) ? 4 : 0)), data});
            }
            o.data = old;
            io_rdata_next = old;
        } else {
            mem_accepted.push_back(k);
            switch (k) {
            case I0: case I1: case R:
                o.check = DATA;
                o.data = k == R ? mem.unit(p.addr) : mem.word(p.addr);
                reads.push_back({k, p.addr, false});
                break;
            case N:
                if (p.op == fabric::STORE) writes.push_back({k, p.addr & ~7u, p.be, p.wdata});
                else { o.check = DATA; o.data = mem.unit(p.addr); reads.push_back({k, p.addr, false}); }
                break;
            case W:
                writes.push_back({k, p.addr & ~7u, p.be, p.wdata});
                break;
            default: {                          // D0, D1
                const unsigned shift = (p.addr & 4u) ? 32 : 0;
                const fabric::Write store{k, p.addr & ~7u, std::uint8_t(std::uint32_t(p.be) << (shift / 8)),
                                          std::uint64_t(std::uint32_t(p.wdata)) << shift};
                if (p.op == fabric::LOAD || p.op == fabric::LR) {
                    o.check = DATA; o.load = true; o.data = mem.word(p.addr);
                    reads.push_back({k, p.addr, false});
                    if (p.op == fabric::LR) lr_set[h] = {true, p.addr >> 2};
                } else if (p.op == fabric::STORE) {
                    writes.push_back(store);
                } else if (p.op == fabric::SC) {
                    const bool ok = resv[h].valid && resv[h].word == (p.addr >> 2);
                    o.check = SC_RESULT; o.data = ok ? 0 : 1;
                    hit(ok ? "sc_success" : "sc_fail");
                    if (ok) { writes.push_back(store); writes.back().kind = fabric::BY_SC; }
                    sc_end[h] = true;
                } else {                        // an AMO: its old value now, its write two edges on
                    o.check = DATA; o.data = mem.word(p.addr);
                    reads.push_back({k, p.addr, false});
                    amos.push_back({cycle, h, p.op, p.addr, std::uint32_t(p.wdata)});
                }
            }
            }
        }
        owed[k].push_back(o);
        pres[k].valid = false;
    }

    // ---- the rules on what was accepted ----
    auto unit_of = [](std::uint32_t a) { return a >> 3; };
    for (std::size_t i = 0; i < writes.size(); ++i) {
        for (const Access& r : reads)
            if (unit_of(r.addr) == unit_of(writes[i].unit_addr)) {
                fail("SAME_UNIT", std::string(kName[r.k]) + "'s read and " + kName[writes[i].writer] + "'s write of unit "
                     + hex(writes[i].unit_addr) + " in one cycle");
                return;
            }
        for (std::size_t j = i + 1; j < writes.size(); ++j)
            if (unit_of(writes[i].unit_addr) == unit_of(writes[j].unit_addr)) {
                fail("SAME_UNIT", "two writes of unit " + hex(writes[i].unit_addr) + " in one cycle");
                return;
            }
    }
    if (banks) {
        std::map<unsigned, unsigned> n_access, n_write;
        for (int k : mem_accepted) ++n_access[bank(pres[k].addr)];
        for (const Amo& a : due) ++n_access[bank(a.addr)];
        for (const fabric::Write& w : writes) ++n_write[bank(w.unit_addr)];
        for (auto [b, n] : n_access) {
            if (n > 2) { fail("BANK_RULE", std::to_string(n) + " accesses in bank " + std::to_string(b)); return; }
            if (n == 2) hit(n_write[b] ? "read_beside_write" : "two_in_bank");
        }
        unsigned written_banks = 0;
        for (auto [b, n] : n_write) {
            if (n > 1) { fail("BANK_RULE", std::to_string(n) + " writes in bank " + std::to_string(b)); return; }
            ++written_banks;
        }
        if (written_banks == 4) hit("four_bank_writes");
    }
    // until an AMO's write: no other write or AMO in its bank, nothing from its data cache
    for (const Amo& a : amos) {
        if (a.accept == cycle) continue;
        for (const fabric::Write& w : writes)
            if (bank(w.unit_addr) == bank(a.addr)) {
                fail("AMO_RULE", std::string(kName[w.writer]) + " wrote in the bank of an AMO waiting for its write");
                return;
            }
        for (int k : mem_accepted)
            if (is_d(k) && fabric::is_amo(pres[k].op) && bank(pres[k].addr) == bank(a.addr)) {
                fail("AMO_RULE", std::string(kName[k]) + "'s AMO accepted before another AMO's write");
                return;
            }
        if (acc[d_of(a.hart)]) { fail("AMO_RULE", std::string(kName[d_of(a.hart)]) + " accepted before its AMO's write"); return; }
    }
    for (const Amo& a : due) {
        for (const fabric::Write& w : writes)
            if (w.kind != fabric::BY_AMO && bank(w.unit_addr) == bank(a.addr)) {
                fail("AMO_RULE", std::string(kName[w.writer]) + " wrote in an AMO's bank in its write's cycle");
                return;
            }
        if (acc[d_of(a.hart)]) { fail("AMO_RULE", std::string(kName[d_of(a.hart)]) + " accepted in its AMO's write cycle"); return; }
        for (int k : mem_accepted)
            if (is_d(k) && fabric::is_amo(pres[k].op) && bank(pres[k].addr) == bank(a.addr)) {
                fail("AMO_RULE", std::string(kName[k]) + "'s AMO accepted in another AMO's write cycle");
                return;
            }
    }
    for (std::size_t i = 0; i < writes.size(); ++i)
        for (std::size_t j = i + 1; j < writes.size(); ++j)
            if (writes[i].writer == writes[j].writer) {
                fail("TWO_WRITES", std::string(kName[writes[i].writer]) + " wrote twice in one cycle");
                return;
            }

    // ---- what waited: coverage, fairness, and the solo rule ----
    auto is_write = [&](int k) {
        return (k == N && pres[k].op == fabric::STORE) || k == W || (is_d(k) && pres[k].op != fabric::LOAD && pres[k].op != fabric::LR);
    };
    auto amo_bank_hold = [&](int k) {                 // a write held back by an AMO waiting for its write
        if (!is_write(k)) return false;
        for (const Amo& a : amos) if (a.accept != cycle && bank(a.addr) == bank(pres[k].addr)) return true;
        for (const Amo& a : due) if (bank(a.addr) == bank(pres[k].addr)) return true;
        return false;
    };
    for (int k : {D0, D1}) {                       // the I/O bus: the other data cache taken ahead of a waiting one
        const int j = D0 + D1 - k;
        if (pres[k].valid && !pres[k].ghost && !acc[k] && !mem.contains(pres[k].addr) && !held[hart_of(k)]
            && acc[j] && !mem.contains(pres[j].addr)) {
            max_overtake = std::max(max_overtake, ++overtaken[k][j]);
            if (overtaken[k][j] > overtake_limit) {
                fail("UNFAIR", std::string(kName[j]) + " taken on the I/O bus ahead of waiting " + kName[k] + " "
                     + std::to_string(overtaken[k][j]) + " times");
                return;
            }
        }
    }
    for (int k = 0; k < NREQ; ++k) {
        const bool waiting = pres[k].valid && !pres[k].ghost && !acc[k] && mem.contains(pres[k].addr);
        if (!waiting) {
            if (!(pres[k].valid && !pres[k].ghost && !acc[k])) for (int j = 0; j < NREQ; ++j) overtaken[k][j] = 0;
            continue;
        }
        const int h = hart_of(k);
        const bool own_hold = is_d(k) && held[h];
        for (const fabric::Write& w : writes)
            if (!is_write(k) && unit_of(w.unit_addr) == unit_of(pres[k].addr)) hit("read_held_by_write");
        if (own_hold) hit("d_held_by_own_amo");
        if (amo_bank_hold(k)) {
            hit("amo_held_write");
            if (is_d(k) && fabric::is_amo(pres[k].op)) {
                hit("amo_held_amo");
                for (const Amo& a : amos)
                    if (a.hart != h && (pres[k].addr >> 2) == (a.addr >> 2) && a.accept + 1 == cycle) hit("amo_pair");
            }
        }
        if (own_hold || amo_bank_hold(k)) continue;   // held by an AMO, not by the requesters accepted
        for (int j = 0; j < NREQ; ++j) {
            if (j == k || !acc[j] || !mem.contains(pres[j].addr)) continue;
            const bool competes = banks ? bank(pres[j].addr) == bank(pres[k].addr)
                                        : (is_write(k) && is_write(j))
                                              || (unit_of(pres[j].addr) == unit_of(pres[k].addr) && (is_write(k) || is_write(j)));
            if (!competes) continue;
            hit("conflict:" + std::string(kName[std::min(j, k)]) + "-" + kName[std::max(j, k)]);
            max_overtake = std::max(max_overtake, ++overtaken[k][j]);
            if (overtaken[k][j] > overtake_limit) {
                fail("UNFAIR", std::string(kName[j]) + " accepted ahead of waiting " + kName[k] + " "
                     + std::to_string(overtaken[k][j]) + " times");
                return;
            }
        }
    }
    if (acc[D0] && acc[D1] && mem.contains(pres[D0].addr) && mem.contains(pres[D1].addr)
        && (!banks || bank(pres[D0].addr) == bank(pres[D1].addr))) hit("twin_together");
    // twin: both data caches' loads to one bank, nothing else asking, are both taken (the banked
    // fabric's second chance on port B; a performance rule of this design, not of the contract)
    auto presented = [&](int k) { return acc[k] || (pres[k].valid && !pres[k].ghost); };   // (taken ones are cleared)
    if (mode == "twin" && both_d_together && !sweeping && presented(D0) && presented(D1)
        && mem.contains(pres[D0].addr) && mem.contains(pres[D1].addr) && bank(pres[D0].addr) == bank(pres[D1].addr)
        && acc[D0] != acc[D1]) {
        fail("TWIN_SERIAL", "D0's and D1's loads to one bank, nothing else asking, not both taken");
        return;
    }
    // the solo modes: the hart alone is never slowed but for its AMO's hold and the unit exception
    if (solo) {
        const int ik = i_of(solo_hart), dk = d_of(solo_hart);
        if (acc[ik] && acc[dk] && mem.contains(pres[dk].addr) && (!banks || bank(pres[ik].addr) == bank(pres[dk].addr)))
            hit("solo_together");
        for (int k : {ik, dk}) {
            if (!pres[k].valid || acc[k]) continue;
            const bool write = k == dk && is_write(k);
            bool excused = k == dk && held[solo_hart];
            if (!write) {
                for (const fabric::Write& w : writes)
                    if (unit_of(w.unit_addr) == unit_of(pres[k].addr)) {
                        excused = true;
                        hit(w.kind == fabric::BY_AMO ? "solo_unit:amo_write" : w.kind == fabric::BY_SC ? "solo_unit:sc"
                                                                                          : "solo_unit:store");
                    }
                if (acc[dk] && mem.contains(pres[dk].addr) && is_write(dk) && unit_of(pres[dk].addr) == unit_of(pres[k].addr)) {
                    excused = true;
                    hit(fabric::is_amo(pres[dk].op) ? "solo_unit:amo_accept" : pres[dk].op == fabric::SC ? "solo_unit:sc"
                                                                                                     : "solo_unit:store");
                }
            } else {
                for (const Access& r : reads) if (unit_of(r.addr) == unit_of(pres[k].addr)) excused = true;
            }
            if (!excused) { fail("SOLO_SLOWED", std::string(kName[k]) + " waited with its hart alone"); return; }
        }
    }

    // ---- the writes take effect; reservations; next cycle's snoops ----
    for (const fabric::Write& w : writes) mem.write(w);
    for (int h = 0; h < 2; ++h) {
        next_ev[h] = false;
        if (lr_set[h].valid) resv[h] = lr_set[h];
        if (sc_end[h]) resv[h].valid = false;
        for (const fabric::Write& w : writes)
            if (w.writer != d_of(h) && resv[h].valid && fabric::touches(w, resv[h].word)) {
                if (selftest == 9) continue;
                resv[h].valid = false;
                next_ev[h] = true;
                hit(std::string("resv_end:") + (!is_d(w.writer) ? (w.writer == N ? "npu" : "dma")
                                                : w.kind == fabric::BY_AMO ? "other_d_amo" : w.kind == fabric::BY_SC ? "other_d_sc"
                                                                                                  : "other_d_store"));
            }
        if (hart_exc[h] && resv[h].valid) { resv[h].valid = false; hit("resv_end:exception"); }
        if (!hart_up[h]) {
            if (resv[h].valid) hit("resv_end:reset");
            resv[h].valid = false;
            owed[i_of(h)].clear();
            owed[d_of(h)].clear();
        }
    }
    for (int c = 0; c < 2; ++c)
        for (int p = 0; p < 3; ++p) next_snoop[c][p] = false;
    for (const fabric::Write& w : writes)
        for (int c = 0; c < 2; ++c)
            if (w.writer != d_of(c)) {
                const int p = snoop_port(w.writer);
                next_snoop[c][p] = true;
                next_line[c][p] = w.unit_addr >> 4;
            }

}

int Shell::run() {
    d.selftest = selftest <= 4 || selftest == 12 ? selftest : 0;   // the wrapper's self-tests
    d.rst_n = 0;
    d.hart_rst_n = 3;
    d.clk = 0;
    d.eval();
    for (int i = 0; i < 4; ++i) { d.clk = 1; d.eval(); d.clk = 0; d.eval(); }
    d.rst_n = 1;
    banks = d.chk_banks;
    both_d_together = banks == 0 || second_chance;
    wait = d.chk_wait;
    latency = 2 + wait;
    if (mode == "edges") build_edges();
    if (declared_banks < 0) fail("BANKS_MISMATCH", "+banks=<n> is required (the DUT's banks, 0 for none)");
    else if (unsigned(declared_banks) != banks)
        fail("BANKS_MISMATCH", "the DUT reports " + std::to_string(banks) + " banks, declared " + std::to_string(declared_banks));
    std::uint64_t limit = cycles + 200000;
    for (cycle = 0; cycle < limit && failure.empty(); ++cycle) {
        if (cycle == cycles || (mode == "edges" && cycle == script_end)) draining = true;
        if (draining && !sweeping) {
            bool idle = amos.empty();
            for (int k = 0; k < NREQ; ++k) idle = idle && !pres[k].valid && owed[k].empty();
            if (idle) {
                sweeping = true;
                if (selftest == 11) mem.bytes[rng() % mem.bytes.size()] ^= 0x40;
            }
        }
        if (sweeping && sweep_at >= MEM_BASE + MEM_BYTES && !pres[R].valid && owed[R].empty()) break;
        drive();
        d.clk = 0;
        d.eval();
        if (!check_outputs()) break;
        if (!check_acceptance()) break;
        take_effect();
        if (!failure.empty()) break;
        for (int c = 0; c < 2; ++c)
            for (int p = 0; p < 3; ++p) { exp_snoop[c][p] = next_snoop[c][p]; exp_line[c][p] = next_line[c][p]; }
        io_rdata = io_rdata_next;
        exp_ev[0] = next_ev[0]; exp_ev[1] = next_ev[1];
        d.clk = 1;
        d.eval();
    }
    if (failure.empty() && cycle >= limit) fail("ANSWER_TIMING", "the run did not drain");

    // coverage that applies to this DUT and mode
    std::vector<std::string> required = {"sc_success", "sc_fail", "io_load", "io_store", "d_held_by_own_amo",
                                         "amo_held_write"};
    for (int c = 0; c < 2; ++c)
        for (int p = 0; p < 3; ++p) required.push_back("snoop:" + std::to_string(c) + ":" + std::to_string(p));
    if (mode == "hot") for (const char* b : {"resv_end:other_d_store", "resv_end:other_d_sc", "resv_end:other_d_amo",
                                             "resv_end:npu", "resv_end:dma", "read_held_by_write", "amo_held_amo",
                                             "amo_pair"}) required.push_back(b);
    if (mode == "reset") for (const char* b : {"resv_end:exception", "resv_end:reset", "reset:owed", "reset:amo",
                                               "reset:quick"}) required.push_back(b);
    if (mode == "errors") for (const char* b : {"error:I", "error:N", "error:R", "error:W"}) required.push_back(b);
    const int writers[] = {D0, D1, N, W};
    for (int a : writers)
        for (int b : writers)
            if (a < b) required.push_back("conflict:" + std::string(kName[a]) + "-" + kName[b]);
    if (banks) {
        for (int a = 0; a < NREQ; ++a)
            for (int b = a + 1; b < NREQ; ++b) required.push_back("conflict:" + std::string(kName[a]) + "-" + kName[b]);
        for (const char* b : {"two_in_bank", "read_beside_write"}) required.push_back(b);
        if (mode != "sparse") required.push_back("four_bank_writes");   // four writers at once: rare at sparse rates
    }
    if (solo) required = {"d_held_by_own_amo", "solo_together", "solo_unit:store", "solo_unit:sc", "solo_unit:amo_accept",
                          "solo_unit:amo_write"};
    // twin: both loads taken together where the DUT can (the reference; a fabric with a second chance),
    // else the two data caches' conflicts in one bank exercised
    if (mode == "twin") required = {both_d_together ? "twin_together" : "conflict:D0-D1"};
    if (mode == "hammer" && banks) {
        required = {"two_in_bank", "read_beside_write", "read_held_by_write"};
        for (int a = 0; a < NREQ; ++a)
            for (int b = a + 1; b < NREQ; ++b) required.push_back("conflict:" + std::string(kName[a]) + "-" + kName[b]);
    }
    if (mode == "edges") {
        for (const char* b : {"resv_end:other_d_store", "resv_end:other_d_sc", "resv_end:other_d_amo", "resv_end:npu",
                              "resv_end:dma", "resv_end:exception", "resv_end:reset", "read_held_by_write", "amo_held_amo",
                              "amo_pair", "reset:owed", "reset:amo", "reset:quick", "io_both", "error:I", "error:N", "error:R",
                              "error:W"}) required.push_back(b);
        if (banks) required.push_back("four_bank_writes");
    }
    std::sort(required.begin(), required.end());
    required.erase(std::unique(required.begin(), required.end()), required.end());
    std::vector<std::string> missing;
    for (const std::string& b : required) if (!bins.count(b)) missing.push_back(b);
    if (failure.empty() && plusflag("require_coverage") && !missing.empty()) {
        std::string list;
        for (const std::string& b : missing) list += (list.empty() ? "" : ",") + b;
        fail("COVERAGE", "missed " + list);
    }

    std::uint64_t total = 0;
    for (auto n : accepted_count) total += n;
    std::cout << "FABRIC " << (failure.empty() ? "PASS" : failure) << " dut=" << (banks ? "banked" : "ref")
              << " banks=" << banks << " wait=" << wait << " mode=" << mode << " cycles=" << cycle
              << " accepted=" << total << " max_wait=" << max_wait << " max_overtake=" << max_overtake << " coverage=" << (required.size() - missing.size())
              << "/" << required.size();
    if (!failure.empty()) std::cout << " at=" << cycle << " " << failure_detail;
    std::cout << std::endl;
    return failure.empty() ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const unsigned seed = plusarg("seed").empty() ? 1 : unsigned(std::stoul(plusarg("seed")));
    Vfabric_shell dut;
    Shell shell(dut, seed);
    const int status = shell.run();
    dut.final();
    return status;
}
