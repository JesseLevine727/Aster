// The Phase 20 DMA's shell (milestone 20.3; docs/soc.md §10.5): aster_dma2 alone, its ports R and W
// answered as the fabric answers them (a read sees memory at its acceptance, a write takes effect at
// its acceptance, each answered exactly 2 + WAIT cycles later; random back-pressure on acceptance), its
// register page driven as the SoC's data caches drive it (one access at a time, the answer the cycle
// after), its counters' window as the devices drive it. Each job is checked against an oracle of the
// whole memory: the destination equals the source, nothing else changed, and an aborted (or stopped,
// or reset) job's written destination is a prefix of the source.
//
//   dma_shell +seed=<n> +mode=<sweep|ontime|stall|errors|abort|reset|stop|mix> [+jobs=<n>] [+stall=<pct>]
//
// sweep: every length 0-LEN_SWEEP at every alignment pair, on time (the exact cycle model) and under
//   back-pressure; ontime: random jobs on time, each request's acceptance cycle and JOB_CYCLES as
//   dma_model.h's schedule; stall: random jobs under back-pressure; errors: range, wrap and overlap
//   errors at their boundaries, LENGTH 0 (also with a bad descriptor), each rejection alone, hart 1's
//   writes, lane merges; abort: ABORT at random points; reset: the engine's reset at random points;
//   stop: the SoC's STOP at random points (the fabric reset, the ports cleared, the engine held), the
//   ARM side's accesses on the ports while held, then a START; mix: all of these, and the counters'
//   window frozen, resumed and restarted between jobs.
// In every mode: no offered request is withdrawn or changed before it is taken (but at a reset or a
// stop); no request is newly offered once an ABORT takes effect; JOB_CYCLES is the job's length as
// the shell sees it (its last write's answer, or its abort's end); BYTES_DONE, read during jobs, is the
// prefix the answered writes cover; the counters (ABI 5) equal the shell's own count of each event
// (the ports' events cycle by cycle; busy cycles, completions, aborts, errors and rejections by job),
// and on some jobs their per-job sums are checked.
// Prints one PASS or FAIL line with the coverage; exits nonzero on a failure.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <random>
#include <string>
#include <vector>

#include "Vaster_dma2.h"
#include "Vaster_dma2___024root.h"
#include "verilated.h"
#include "dma_model.h"

#ifndef WAIT_CYCLES
#define WAIT_CYCLES 0
#endif

namespace {
constexpr std::uint32_t kBase = 0x80000000u, kBytes = 0x18000u, kHist = 2 + WAIT_CYCLES;
constexpr std::uint32_t kArmArea = kBase + 0x17800u;        // the ARM side's scratch (no job touches it)
constexpr unsigned LEN_SWEEP = 80;

Vaster_dma2* top = nullptr;
std::mt19937_64 rng;
std::vector<std::uint8_t> mem(kBytes);
std::uint64_t cycle = 0;
unsigned stall_pct = 0;
bool failed = false, quiet = false, burst = false;  // burst: an event every cycle (the window's edges)
std::string first_failure;

struct Owed { std::uint64_t due; std::uint64_t data; };
std::deque<Owed> r_owed, w_owed;

// the counters, as the shell counts their events: the ports' cycle by cycle (registered twice, counted
// two cycles later if the window adds then), the others by job. The window adds as the devices' counters
// do: while counting, but not in START's, FREEZE's or RESUME's cycle (win_resume: the command's).
std::uint64_t tally[14] = {}, inc_prev[14] = {}, inc_prev2[14] = {};
bool win_resume = false, win_freeze = false;   // (the devices' commands: the DMA sees only win_add)
bool window_open() { return top->win_counting && !top->win_start && !win_freeze && !win_resume; }

// the ports, as the shell sees them
std::uint64_t r_acc_total = 0, w_acc_total = 0, r_ans_total = 0, w_ans_total = 0, new_offers = 0;
bool r_held = false, w_held = false, held_exempt = false;
std::uint32_t held_r_addr = 0, held_w_addr = 0;
std::uint64_t held_w_data = 0;
std::uint8_t held_w_be = 0;
bool last_r_valid = false, last_w_valid = false;           // this cycle's offers (for the end of an abort)
std::vector<std::uint64_t> log_r, log_w;
struct ArmAccess { bool write; std::uint32_t addr; std::uint64_t data; std::uint8_t be; };
std::deque<ArmAccess> arm_expect;                          // the ARM side's accesses, in order, while held
std::uint64_t arm_seen = 0;

// coverage
std::uint64_t longest_job = 0;
std::uint64_t jobs_done = 0, jobs_aborted = 0, jobs_reset = 0, jobs_stopped = 0, errors_seen[4] = {}, rejects_seen = 0,
              stalls = 0, bytes_copied = 0, hart1_writes = 0, cycle_checks = 0, job_audits = 0, abort_checks = 0,
              bytes_reads = 0, job_cycle_checks = 0, holds_checked = 0, window_changes = 0;
bool pairs_seen[8][8] = {};

void fail(const std::string& what) {
    if (!failed) first_failure = "cycle " + std::to_string(cycle) + ": " + what;
    failed = true;
}

std::uint64_t mem_unit(std::uint32_t unit_addr) {
    std::uint64_t v = 0;
    const std::uint32_t off = unit_addr * 8u - kBase;
    for (int b = 7; b >= 0; --b) v = (v << 8) | mem[off + b];
    return v;
}

// One cycle: the ports answered as the fabric answers, the offered requests held, the events counted.
void step() {
    top->r_req_ready = top->job_rst_n || !top->run ? (stall_pct == 0 || (rng() % 100) >= stall_pct) : 0;
    top->w_req_ready = top->job_rst_n || !top->run ? (stall_pct == 0 || (rng() % 100) >= stall_pct) : 0;
    top->r_rsp_valid = !r_owed.empty() && r_owed.front().due == cycle;
    top->r_rsp_rdata = top->r_rsp_valid ? r_owed.front().data : 0;
    top->w_rsp_valid = !w_owed.empty() && w_owed.front().due == cycle;
    top->ev_inval = burst ? 3 : quiet ? 0 : std::uint8_t(rng() % 16 == 0 ? (rng() % 3 == 0 ? 3 : 1) : 0);
    top->clk = 0;
    top->eval();
    const bool run = top->run;
    const bool r_acc = top->r_req_valid && top->r_req_ready, w_acc = top->w_req_valid && top->w_req_ready;
    // an offered request stays, unchanged, until it is taken (a reset or a stop may drop it)
    if (!held_exempt) {
        if (r_held) {
            ++holds_checked;
            if (!top->r_req_valid || std::uint32_t(top->r_req_addr) != held_r_addr) fail("an offered read was withdrawn or changed");
        }
        if (w_held) {
            ++holds_checked;
            if (!top->w_req_valid || std::uint32_t(top->w_req_addr) != held_w_addr || std::uint64_t(top->w_req_wdata) != held_w_data
                || std::uint8_t(top->w_req_be) != held_w_be)
                fail("an offered write was withdrawn or changed");
        }
    }
    if (top->r_req_valid && !r_held) ++new_offers;
    if (top->w_req_valid && !w_held) ++new_offers;
    r_held = top->r_req_valid && !r_acc;
    w_held = top->w_req_valid && !w_acc;
    held_r_addr = std::uint32_t(top->r_req_addr);
    held_w_addr = std::uint32_t(top->w_req_addr); held_w_data = top->w_req_wdata; held_w_be = top->w_req_be;
    held_exempt = top->clear || !top->rst_n || (run && !top->job_rst_n);
    last_r_valid = top->r_req_valid; last_w_valid = top->w_req_valid;
    // acceptance: the effect; the answer 2 + WAIT cycles later
    if (r_acc) {
        log_r.push_back(cycle);
        const std::uint32_t a = std::uint32_t(top->r_req_addr) * 8u;
        if (a < kBase || a >= kBase + kBytes) fail("a read outside main memory");
        else r_owed.push_back({cycle + kHist, mem_unit(std::uint32_t(top->r_req_addr))});
    }
    if (w_acc) {
        log_w.push_back(cycle);
        const std::uint32_t a = std::uint32_t(top->w_req_addr) * 8u;
        if (a < kBase || a >= kBase + kBytes) fail("a write outside main memory");
        else {
            const std::uint64_t data = top->w_req_wdata;
            for (int b = 0; b < 8; ++b) if ((top->w_req_be >> b) & 1) mem[a - kBase + b] = std::uint8_t(data >> (8 * b));
            w_owed.push_back({cycle + kHist, 0});
        }
    }
    if (!run && (r_acc || w_acc)) {
        // while held, the ports carry the ARM side's accesses exactly, and nothing else
        ++arm_seen;
        if (arm_expect.empty()) fail("a request on the ports while held that the ARM side did not make");
        else {
            const ArmAccess want = arm_expect.front();
            arm_expect.pop_front();
            const bool ok = want.write ? w_acc && !r_acc && std::uint32_t(top->w_req_addr) == want.addr / 8
                                          && std::uint64_t(top->w_req_wdata) == want.data && std::uint8_t(top->w_req_be) == want.be
                                       : r_acc && !w_acc && std::uint32_t(top->r_req_addr) == want.addr / 8;
            if (!ok) fail("the ports did not carry the ARM side's access as it was made");
        }
    }
    if (top->r_rsp_valid) { r_owed.pop_front(); if (run) ++r_ans_total; }
    if (top->w_rsp_valid) { w_owed.pop_front(); if (run) ++w_ans_total; }
    if (run && r_acc) ++r_acc_total;
    if (run && w_acc) ++w_acc_total;
    if ((top->r_req_valid && !top->r_req_ready) || (top->w_req_valid && !top->w_req_ready)) ++stalls;
    // the ports' events, as the counters see them
    std::uint64_t inc[14] = {};
    inc[1] = (run && top->r_req_valid && !top->r_req_ready) + (run && top->w_req_valid && !top->w_req_ready);
    inc[2] = run && top->r_rsp_valid;
    inc[3] = run && top->w_rsp_valid;
    inc[4] = run && w_acc ? __builtin_popcount(top->w_req_be) : 0;
    inc[5] = run && r_acc;
    inc[6] = run && w_acc;
    inc[9] = (top->ev_inval & 1) + ((top->ev_inval >> 1) & 1);
    top->win_add = window_open();
    if (!top->job_rst_n) { for (auto& t : tally) t = 0; for (auto& t : inc_prev) t = 0; for (auto& t : inc_prev2) t = 0; }
    else {
        if (top->win_start) for (auto& t : tally) t = 0;
        else if (window_open()) for (int i = 0; i < 14; ++i) tally[i] += inc_prev2[i];
        for (int i = 0; i < 14; ++i) { inc_prev2[i] = inc_prev[i]; inc_prev[i] = inc[i]; }
    }
    top->clk = 1;
    top->eval();
    top->win_start = 0;                       // (a one-cycle pulse, seen at this edge)
    ++cycle;
}

void idle(unsigned n) { for (unsigned i = 0; i < n; ++i) step(); }

// One register access, as a data cache makes it: presented for a cycle, answered in the next.
std::uint32_t io(bool write, std::uint32_t offset, std::uint32_t data = 0, unsigned hart = 0, unsigned be = 0xF) {
    top->io_valid = 1; top->io_hart = hart; top->io_write = write; top->io_addr = offset & 0xFFF;
    top->io_wdata = data; top->io_be = be;
    step();
    top->io_valid = 0;
    top->clk = 0; top->eval();               // the answer cycle: io_sel and io_rdata from the registered request
    if (!top->io_sel) fail("the register port did not answer");
    const std::uint32_t value = top->io_rdata;
    step();
    idle(rng() % 3);
    return value;
}
std::uint32_t rd(std::uint32_t offset) { return io(false, offset); }
void wr(std::uint32_t offset, std::uint32_t value, unsigned hart = 0, unsigned be = 0xF) { io(true, offset, value, hart, be); }
std::uint64_t counter(unsigned i) { const std::uint64_t lo = rd(0x100 + 8 * i); return lo | (std::uint64_t(rd(0x104 + 8 * i)) << 32); }

// the per-job counts, added while the window is open (the shell changes the window only between jobs)
void count_job(unsigned index, std::uint64_t n) { if (window_open()) tally[index] += n; }

void fill_random() { for (auto& b : mem) b = std::uint8_t(rng()); }

// The SoC's STOP: the fabric reset (a request may still be taken in the stop cycle; nothing is answered
// after it), the ports cleared, the harts held at the next edge and the engine a cycle later.
void stop_soc() {
    top->clear = 1;
    step();
    top->clear = 0;
    r_owed.clear(); w_owed.clear();
    top->run = 0;
    step();
    top->job_rst_n = 0;
    step();
}
// The SoC's START: the fabric reset, the harts released at the next edge and the engine a cycle later.
void start_soc() {
    top->clear = 1;
    step();
    top->clear = 0;
    r_owed.clear(); w_owed.clear();
    top->run = 1;
    step();
    top->job_rst_n = 1;
    step();
}
// The ARM side's accesses while held: reads and writes in its scratch area, each through the ports.
void arm_accesses(unsigned n, std::vector<std::uint8_t>& expect) {
    for (unsigned i = 0; i < n && !failed; ++i) {
        const bool write = rng() % 2;
        const std::uint32_t addr = kArmArea + 4u * std::uint32_t(rng() % 256);
        const std::uint32_t data = std::uint32_t(rng());
        const std::uint8_t be = std::uint8_t(1 + rng() % 15);
        const std::uint8_t lanes = (addr & 4) ? std::uint8_t(be << 4) : be;
        arm_expect.push_back({write, addr, (std::uint64_t(data) << 32) | data, lanes});
        top->arm_go = 1; top->arm_write = write; top->arm_addr = addr; top->arm_wdata = data; top->arm_be = be;
        step();
        top->arm_go = 0;
        for (unsigned guard = 0; guard < 1000 && !arm_expect.empty(); ++guard) step();
        if (!arm_expect.empty()) fail("an ARM-side access was never taken");
        if (write) for (int b = 0; b < 4; ++b) if ((be >> b) & 1) expect[addr - kBase + b] = std::uint8_t(data >> (8 * b));
        idle(kHist + 1);
    }
}

struct Job { std::uint32_t src, dst, len; };
struct Plan { bool exact = false; std::uint64_t abort_at = 0, reset_at = 0, stop_at = 0; };

// A copy, checked against the oracle: wait for it, then the whole memory and the registers.
void run_job(const Job& job, const Plan& plan) {
    std::vector<std::uint8_t> before = mem;
    const bool interrupted = plan.reset_at || plan.stop_at;
    const bool audit = !interrupted && window_open() && rng() % 4 == 0;
    static const unsigned kSums[] = {0, 2, 3, 4, 5, 6, 10, 11};
    std::uint64_t base[14] = {};
    if (audit) for (unsigned i : kSums) base[i] = counter(i);
    wr(0x00, job.src); wr(0x04, job.dst); wr(0x08, job.len);
    if (rd(0x00) != job.src || rd(0x04) != job.dst || rd(0x08) != job.len) fail("the descriptor did not read back");
    // START: presented now, it takes effect in the next cycle (cycle 0 of the job)
    top->io_valid = 1; top->io_hart = 0; top->io_write = 1; top->io_addr = 0x0C; top->io_wdata = 1; top->io_be = 0xF;
    step();
    top->io_valid = 0;
    const std::uint64_t start = cycle;
    const std::uint64_t w_ans0 = w_ans_total, r_acc0 = r_acc_total, w_acc0 = w_acc_total, r_ans0 = r_ans_total;
    const std::uint64_t nd = job.len ? (std::uint64_t(job.len) + (job.dst & 7) + 7) / 8 : 0;
    log_r.clear(); log_w.clear();
    std::uint64_t abort_q = 0, offers_at_abort = 0, end = 0;
    bool aborted_sent = false, snapped = false, ended = false, ended_aborted = false, interrupted_done = false;
    for (std::uint64_t guard = 0; guard < 4000000; ++guard) {
        const std::uint64_t now = cycle - start;
        if (plan.reset_at && now >= plan.reset_at) {
            top->job_rst_n = 0;
            r_owed.clear(); w_owed.clear();     // (the fabric resets with it, dropping its answers)
            step(); step();
            top->job_rst_n = 1;
            interrupted_done = true;
            break;
        }
        if (plan.stop_at && now >= plan.stop_at) {
            stop_soc();
            arm_accesses(1 + rng() % 4, before);
            start_soc();
            interrupted_done = true;
            break;
        }
        bool present = false;
        if (plan.abort_at && !aborted_sent && now >= plan.abort_at) {
            top->io_valid = 1; top->io_write = 1; top->io_addr = 0x0C; top->io_wdata = 2; top->io_be = 0xF;
            aborted_sent = true; present = true;
            abort_q = cycle + 1;                 // the ABORT takes effect in the next cycle
        } else if (rng() % 8 == 0) {
            top->io_valid = 1; top->io_write = 0; top->io_addr = 0x14;   // BYTES_DONE, read during the job
            present = true;
        }
        if (!present) top->io_valid = 0;
        const bool reading = present && !top->io_write;
        if (aborted_sent && !snapped && cycle == abort_q + 1) { offers_at_abort = new_offers; snapped = true; }
        const bool was_busy = top->rootp->aster_dma2__DOT__running;
        step();
        top->io_valid = 0;
        if (reading) {
            // the answer cycle: the register as it stood after the presenting cycle's answers
            top->clk = 0; top->eval();
            const std::uint64_t answered = w_ans_total - w_ans0;
            const std::uint64_t upto = answered ? answered * 8 - (job.dst & 7) : 0;
            const std::uint32_t want = std::uint32_t(std::min<std::uint64_t>(upto, job.len));
            if (top->io_rdata != want) fail("BYTES_DONE " + std::to_string(top->io_rdata) + " during the job, the answered prefix "
                                            + std::to_string(want));
            ++bytes_reads;
        }
        // the job's end as the shell sees it: its last write's answer, or (once an ABORT took effect) the
        // first cycle with no request offered and nothing owed
        const std::uint64_t c = cycle - 1 - start;
        if (!ended && job.len) {
            const bool abort_on = aborted_sent && cycle - 1 >= abort_q;
            const std::uint64_t owed = (r_acc_total - r_acc0) - (r_ans_total - r_ans0) + (w_acc_total - w_acc0) - (w_ans_total - w_ans0);
            if (abort_on) {
                if (!last_r_valid && !last_w_valid && owed == 0) { ended = true; ended_aborted = true; end = c; }
            } else if (w_ans_total - w_ans0 == nd) { ended = true; end = c; }
        }
        if (now > 1 && !was_busy && !top->rootp->aster_dma2__DOT__running) break;
    }
    top->io_valid = 0;
    if (aborted_sent && !snapped) offers_at_abort = new_offers;   // (the job ended as the ABORT took effect)
    idle(2 + rng() % 4);
    if (interrupted_done) {
        // after a reset or a stop: the registers cleared, and the destination a written prefix of the source
        if (plan.reset_at) ++jobs_reset; else ++jobs_stopped;
        if (rd(0x10) != 0 || rd(0x00) != 0 || rd(0x14) != 0) fail("the registers were not cleared");
        std::uint32_t p = 0;
        while (p < job.len && mem[job.dst - kBase + p] == before[job.src - kBase + p]) ++p;
        for (std::uint32_t i = 0; i < kBytes; ++i) {
            const std::uint32_t a = kBase + i;
            if (!(a >= job.dst && a < job.dst + p) && mem[i] != before[i]) { fail("an interrupted job wrote outside a prefix of its destination"); break; }
        }
        return;
    }
    const std::uint32_t status = rd(0x10), bytes = rd(0x14), code = rd(0x18);
    const std::uint64_t cycles = rd(0x20) | (std::uint64_t(rd(0x24)) << 32);
    const bool aborted = status & 8;
    if ((status & 1) || !(status & 2) || (status & 4)) fail("status " + std::to_string(status) + " after a copy");
    if (code != 0) fail("an error code after a copy");
    if (job.len && aborted != ended_aborted) fail(std::string("the job ended ") + (aborted ? "aborted" : "done") + ", the shell saw it "
                                                  + (ended_aborted ? "aborted" : "done"));
    if (job.len && cycles != end) fail("JOB_CYCLES " + std::to_string(cycles) + ", the shell saw " + std::to_string(end));
    if (!job.len && cycles != 0) fail("JOB_CYCLES nonzero for LENGTH 0");
    ++job_cycle_checks;
    const std::uint32_t expect_bytes = aborted ? bytes : job.len;
    if (!aborted && bytes != job.len) fail("BYTES_DONE " + std::to_string(bytes) + " for " + std::to_string(job.len));
    if (bytes > job.len) fail("BYTES_DONE beyond the length");
    for (std::uint32_t i = 0; i < kBytes; ++i) {
        const std::uint32_t a = kBase + i;
        const bool in_dst = a >= job.dst && a < job.dst + expect_bytes;
        const std::uint8_t want = in_dst ? before[job.src - kBase + (a - job.dst)] : before[i];
        if (mem[i] != want) {
            fail("memory differs at 0x" + std::to_string(a) + (in_dst ? " (the destination)" : " (outside the copied prefix)"));
            break;
        }
    }
    if (aborted) {
        ++abort_checks;
        if (new_offers != offers_at_abort) fail("a request offered after the ABORT took effect");
    }
    count_job(0, cycles);
    count_job(aborted ? 11 : 10, 1);
    if (audit) {
        const std::uint64_t ns = job.len ? (std::uint64_t(job.len) + (job.src & 7) + 7) / 8 : 0;
        std::uint64_t want[14] = {};
        want[0] = cycles; want[10] = !aborted; want[11] = aborted;
        if (!aborted) { want[2] = ns; want[3] = nd; want[4] = job.len; want[5] = ns; want[6] = nd; }
        idle(3);
        for (unsigned i : kSums) {
            if (aborted && i >= 2 && i <= 6) continue;
            const std::uint64_t got = counter(i) - base[i];
            if (got != want[i]) fail("the job added " + std::to_string(got) + " to counter " + std::to_string(i) + ", not " + std::to_string(want[i]));
        }
        ++job_audits;
    }
    if (aborted) ++jobs_aborted; else { ++jobs_done; bytes_copied += job.len; longest_job = std::max<std::uint64_t>(longest_job, job.len); }
    pairs_seen[job.src & 7][job.dst & 7] = true;
    if (plan.exact && !aborted && job.len != 0) {
        const dma::Schedule want = dma::schedule(job.src, job.dst, job.len, WAIT_CYCLES);
        std::vector<std::uint64_t> reads, writes;
        for (std::uint64_t r : log_r) reads.push_back(r - start);
        for (std::uint64_t w : log_w) writes.push_back(w - start);
        if (cycles != want.end) fail("JOB_CYCLES " + std::to_string(cycles) + ", the model's " + std::to_string(want.end)
                                     + " (src " + std::to_string(job.src & 7) + " dst " + std::to_string(job.dst & 7)
                                     + " len " + std::to_string(job.len) + ")");
        if (reads != want.read_accept) fail("the reads' cycles are not the model's (len " + std::to_string(job.len) + ")");
        if (writes != want.write_accept) fail("the writes' cycles are not the model's (len " + std::to_string(job.len) + ")");
        ++cycle_checks;
    }
    if (rng() % 2) wr(0x0C, 4);                 // ACK now and then
}

// An error, or LENGTH 0: nothing written, the flags and code as v1's ABI.
void run_error(const Job& job, unsigned want_code) {
    const std::vector<std::uint8_t> before = mem;
    wr(0x00, job.src); wr(0x04, job.dst); wr(0x08, job.len);
    wr(0x0C, 1);
    idle(4);
    const std::uint32_t status = rd(0x10), code = rd(0x18), bytes = rd(0x14), cycles = rd(0x20);
    if (want_code == 0) {
        if (status != 2 || code != 0 || bytes != 0 || cycles != 0) fail("LENGTH 0 did not complete at once");
    } else if (status != 6 || code != want_code || bytes != 0) {
        fail("error " + std::to_string(want_code) + ": status " + std::to_string(status) + " code " + std::to_string(code));
    }
    if (mem != before) fail("an error job wrote memory");
    ++errors_seen[want_code];
    count_job(want_code == 0 ? 10 : 12, 1);
    wr(0x0C, 4);
    if (rd(0x10) != 0 || rd(0x18) != 0) fail("ACK did not clear the flags and the code");
}

std::uint32_t pick_addr(std::uint32_t len, std::uint32_t limit = kBytes - 0x800) {   // (below the ARM side's area)
    return kBase + std::uint32_t(rng() % (limit - len + 1));
}
Job random_job(std::uint32_t max_len) {
    for (;;) {
        Job j;
        j.len = std::uint32_t(rng() % 4 == 0 ? rng() % (max_len + 1) : rng() % 64);
        j.src = pick_addr(j.len);
        j.dst = pick_addr(j.len);
        if (j.len && j.src < j.dst + j.len && j.dst < j.src + j.len) continue;   // overlapping: not a copy
        return j;
    }
}

// a large job, 8 KiB up to half the memory below the ARM side's area (about 47 KiB): the engine's unit
// counts near their widest; the source in one half and the destination in the other
Job big_job() {
    const std::uint32_t half = (kBytes - 0x800) / 2;
    Job j;
    j.len = 8192 + std::uint32_t(rng() % (half - 8192 + 1));
    const std::uint32_t a = kBase + std::uint32_t(rng() % (half - j.len + 1));
    const std::uint32_t b = kBase + half + std::uint32_t(rng() % (half - j.len + 1));
    if (rng() % 2) { j.src = a; j.dst = b; } else { j.src = b; j.dst = a; }
    return j;
}

void audit_counters() {
    quiet = true;
    idle(4);
    for (unsigned i = 0; i < 14; ++i) {
        const std::uint64_t got = counter(i);
        if (got != tally[i]) fail("counter " + std::to_string(i) + " is " + std::to_string(got) + ", the events "
                                  + std::to_string(tally[i]));
    }
    quiet = false;
}

// the counters' window between jobs, as the devices' command word moves it: FREEZE, RESUME, START
void move_window() {
    burst = true;                             // (events in every cycle about the change: its edges counted or not)
    idle(4);
    const unsigned kind = rng() % 3;
    if (kind == 0 && top->win_counting) { win_freeze = true; step(); win_freeze = false; top->win_counting = 0; }
    else if (kind == 1) {                     // RESUME: opens a frozen window, or (counting) skips its cycle
        win_resume = true; step(); win_resume = false; top->win_counting = 1;
    }
    else if (kind == 2) { top->win_start = 1; step(); top->win_counting = 1; }
    ++window_changes;
    idle(4);
    burst = false;
    audit_counters();                         // (now: a later reset or stop would clear what it shows)
}

void error_tests(unsigned rounds) {
    for (unsigned i = 0; i < rounds && !failed; ++i) {
        const std::uint32_t len = 1 + rng() % 256;
        // 1: the source out of range (below, above, at the base minus one, a 33-bit end); 2: the
        // destination (above, below, a 33-bit end); 3: overlap
        run_error({kBase - 1 - std::uint32_t(rng() % 64), kBase + 0x100, len}, 1);
        run_error({kBase + kBytes - len + 1 + std::uint32_t(rng() % 8), kBase + 0x100, len}, 1);
        run_error({0xFFFFFFF0u + std::uint32_t(rng() % 8), kBase + 0x100, len + 32}, 1);
        run_error({kBase + 0x10, kBase + 0x100, 0xFFFFFFF8u - std::uint32_t(rng() % 8)}, 1);
        run_error({kBase + 0x100, kBase + kBytes - len + 1, len}, 2);
        run_error({kBase + 0x100, 0x10000000u, len}, 2);
        run_error({kBase + 0x100, kBase - 1, len}, 2);
        run_error({kBase + 0x100, 0xFFFFFFF0u + std::uint32_t(rng() % 8), 32}, 2);
        const std::uint32_t a = kBase + 0x2000 + std::uint32_t(rng() % 512);
        run_error({a, a + std::uint32_t(rng() % len), len}, 3);
        run_error({a + std::uint32_t(rng() % len), a, len}, 3);
        run_error({a, a + len - 1, len}, 3);
        run_error({a + len - 1, a, len}, 3);
        // LENGTH 0 comes first: done even with a bad or overlapping descriptor
        run_error({a, a + 16, 0}, 0);
        run_error({0x10, 0x20, 0}, 0);
        run_error({a, a, 0}, 0);
        // copies at the boundaries: adjacent ranges both ways, main memory's base and its end
        run_job({a, a + len, len}, Plan{});
        run_job({a + len, a, len}, Plan{});
        run_job({kBase, kBase + 0x4000, len}, Plan{});
        run_job({kBase + 0x4000, kBase, len}, Plan{});
        run_job({kBase + 0x40, kBase + kBytes - len, len}, Plan{});           // the destination's end at LIMIT_HI
        run_job({kBase + kBytes - len, kBase + 0x40, len}, Plan{});
        // rejections while busy, each in a job of its own (configuration, START, ACK): REJECTED set, the
        // job and the registers unchanged; then hart 1's writes, ignored (not rejected)
        for (unsigned kind = 0; kind < 4; ++kind) {
            wr(0x00, kBase + 0x3000); wr(0x04, kBase + 0x8000); wr(0x08, 2048); wr(0x0C, 1);
            if (kind == 0) wr(0x00, 0x1234);
            else if (kind == 1) wr(0x0C, 1);
            else if (kind == 2) wr(0x0C, 4);
            else { wr(0x0C, 2, 1); wr(0x08, 7, 1); wr(0x0C, 4, 1); ++hart1_writes; }
            idle(3000);
            const std::uint32_t st = rd(0x10);
            const std::uint32_t want = kind < 3 ? 0x12 : 0x02;
            if (st != want) fail("busy-time rule " + std::to_string(kind) + ": status " + std::to_string(st));
            if (rd(0x00) != kBase + 0x3000 || rd(0x08) != 2048) fail("a write while busy, or hart 1's, changed a register");
            count_job(0, rd(0x20));
            count_job(10, 1);
            if (kind < 3) { ++rejects_seen; count_job(13, 1); }
            wr(0x0C, 4);
            if (rd(0x10) != 0) fail("ACK did not clear the flags");
        }
        wr(0x0C, 0x81);                                       // unknown commands, each alone
        if (rd(0x10) != 0x10) fail("an unknown command was not rejected");
        wr(0x0C, 4);
        wr(0x0C, 3);
        if (rd(0x10) != 0x10) fail("an unknown command was not rejected");
        rejects_seen += 2; count_job(13, 2);
        wr(0x0C, 4);
        if (rd(0x10) != 0) fail("ACK did not clear REJECTED");
        wr(0x0C, 2);                                          // ABORT while idle: nothing
        if (rd(0x10) != 0) fail("ABORT while idle changed the status");
        // lanes merge while idle
        wr(0x00, 0x11223344); wr(0x00, 0xAA, 0, 0x1); wr(0x00, 0xBB000000, 0, 0x8);
        if (rd(0x00) != 0xBB2233AA) fail("lane writes did not merge");
    }
}
}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const std::string seed_arg = Verilated::commandArgsPlusMatch("seed=");
    const std::string mode_arg = Verilated::commandArgsPlusMatch("mode=");
    const std::string jobs_arg = Verilated::commandArgsPlusMatch("jobs=");
    const std::string stall_arg = Verilated::commandArgsPlusMatch("stall=");
    const unsigned seed = seed_arg.empty() ? 1 : std::stoul(seed_arg.substr(6));
    const std::string mode = mode_arg.empty() ? "mix" : mode_arg.substr(6);
    const unsigned jobs = jobs_arg.empty() ? 200 : std::stoul(jobs_arg.substr(6));
    const unsigned stall = stall_arg.empty() ? 30 : std::stoul(stall_arg.substr(7));
    rng.seed(seed);
    top = new Vaster_dma2;
    top->rst_n = 0; top->job_rst_n = 0; top->run = 0; top->clear = 0; top->io_valid = 0; top->arm_go = 0;
    top->win_start = 0; top->win_counting = 0; top->win_add = 0; top->clk = 0;
    idle(3);
    top->rst_n = 1;
    fill_random();
    start_soc();
    // open the counters' window
    top->win_start = 1; step(); top->win_counting = 1;
    for (auto& t : tally) t = 0;

    auto random_plan = [&](const Job& j, unsigned kinds) {
        Plan p;
        const unsigned kind = rng() % kinds;
        const std::uint64_t at = 1 + rng() % (8 + j.len / 4);
        if (kind == 1) p.abort_at = at;
        else if (kind == 2) p.reset_at = at;
        else if (kind == 3) p.stop_at = at;
        else if (kind == 4) p.exact = true;
        return p;
    };
    if (mode == "sweep") {
        for (unsigned pass = 0; pass < 2 && !failed; ++pass) {
            stall_pct = pass == 0 ? 0 : stall;
            for (unsigned len = 0; len <= LEN_SWEEP && !failed; ++len)
                for (unsigned s = 0; s < 8 && !failed; ++s)
                    for (unsigned d = 0; d < 8 && !failed; ++d) {
                        Job j{kBase + 0x1000 + s, kBase + 0x9000 + d, len};
                        if (len == 0) { run_error(j, 0); continue; }
                        Plan p; p.exact = pass == 0;
                        run_job(j, p);
                    }
        }
    } else if (mode == "ontime") {
        stall_pct = 0;
        for (unsigned i = 0; i < jobs && !failed; ++i) { Plan p; p.exact = true; run_job(i % 16 == 15 ? big_job() : random_job(4096), p); }
    } else if (mode == "stall") {
        stall_pct = stall;
        for (unsigned i = 0; i < jobs && !failed; ++i) run_job(i % 16 == 15 ? big_job() : random_job(4096), Plan{});
    } else if (mode == "errors") {
        error_tests(jobs);
    } else if (mode == "abort") {
        stall_pct = stall;
        for (unsigned i = 0; i < jobs && !failed; ++i) { const Job j = random_job(2048); Plan p; p.abort_at = 1 + rng() % (8 + j.len / 4); run_job(j, p); }
    } else if (mode == "reset" || mode == "stop") {
        stall_pct = stall;
        for (unsigned i = 0; i < jobs && !failed; ++i) {
            const Job j = random_job(2048);
            Plan p;
            (mode == "reset" ? p.reset_at : p.stop_at) = 1 + rng() % (8 + j.len / 4);
            run_job(j, p);
            if (rng() % 3 == 0) run_job(random_job(512), Plan{});   // a whole job after it
        }
    } else if (mode == "mix") {
        error_tests(jobs / 8);
        stall_pct = stall;
        for (unsigned i = 0; i < jobs && !failed; ++i) {
            const Job j = random_job(2048);
            Plan p = random_plan(j, 5);
            if (p.exact) stall_pct = 0;
            run_job(j, p);
            stall_pct = stall;
            if (rng() % 8 == 0) move_window();
        }
    } else {
        fail("unknown mode " + mode);
    }
    if (!failed) audit_counters();
    unsigned pairs = 0;
    for (auto& row : pairs_seen) for (bool p : row) pairs += p;
    std::printf("%s: aster_dma2 (WAIT %u) %s seed %u: %llu copies (%llu bytes, the longest %llu), %llu aborted, %llu reset, %llu stopped "
                "(%llu ARM-side accesses), errors 1/2/3 %llu/%llu/%llu, LENGTH 0 %llu, %llu rejections, %llu cycle-exact "
                "against the model, %llu JOB_CYCLES checked, %llu BYTES_DONE reads, %llu holds, %u/64 alignment pairs, "
                "%llu stalled cycles, %llu jobs' counter sums, %llu aborts offering nothing new, %llu window changes, "
                "counters audited%s%s\n",
                failed ? "FAIL" : "PASS", WAIT_CYCLES, mode.c_str(), seed, (unsigned long long)jobs_done,
                (unsigned long long)bytes_copied, (unsigned long long)longest_job, (unsigned long long)jobs_aborted, (unsigned long long)jobs_reset,
                (unsigned long long)jobs_stopped, (unsigned long long)arm_seen,
                (unsigned long long)errors_seen[1], (unsigned long long)errors_seen[2], (unsigned long long)errors_seen[3],
                (unsigned long long)errors_seen[0], (unsigned long long)rejects_seen, (unsigned long long)cycle_checks,
                (unsigned long long)job_cycle_checks, (unsigned long long)bytes_reads, (unsigned long long)holds_checked,
                pairs, (unsigned long long)stalls, (unsigned long long)job_audits, (unsigned long long)abort_checks,
                (unsigned long long)window_changes, failed ? " — " : "", failed ? first_failure.c_str() : "");
    delete top;
    return failed ? 1 : 0;
}
