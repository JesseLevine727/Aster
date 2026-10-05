// The NPU shell (Phase 19, docs/npu.md §6): an NPU DUT (shell_npu_*.sv) on
// the CPU shell's memory model, driven through its register port job by job,
// each job checked against the independent reference (npu_model.h). The DUT
// names its ABI (chk_abi): 1 for v1's NPU (19.0), 2 for the v2 NPU (19.1 on).
//
// Memory: the profile's window, filled with random bytes; requests accepted
// when fewer than +max_inflight (default 2) answers are owed, answered in
// order after +latency (1 or 2, default 2) cycles; +stall_seed adds random
// ready-low cycles and 0-2 extra cycles per answer, +long_stall one answer in
// sixteen 16-63 cycles late — the CPU shell's modes (verification/core). Read
// data and the error signal are garbage outside their cycles; an error comes
// in the cycle after acceptance, and an access answered with one is not
// performed. Checks every cycle: a request not accepted is presented
// unchanged (REQ_UNSTABLE); an access lies in the window (OUT_OF_WINDOW); a
// read's word overlaps A or B and a write's enabled bytes are C's
// (STRAY_READ, STRAY_WRITE); no more requests in flight than the DUT's limit
// (INFLIGHT: v1 one, ABI 2 two — npu.md §5.1's OUTSTANDING); ABI 2 writes whole words (PARTIAL_WRITE); no C
// byte written twice in a job (DOUBLE_WRITE); no access during a job that must
// end in a descriptor error (ACCESS_ON_ERROR); none presented or owed while
// the DUT shows its end (DONE_EARLY) or is not busy (IDLE_ACCESS); `irq` low
// while busy; for ABI 2 no counter counting while not busy (chk_counting). (v1 takes
// one transaction at a time, so +max_inflight above 1 changes only its stall
// pattern.)
//
// Each job: the descriptor written, START, STATUS polled every 1-32 cycles,
// then: STATUS exactly as the reference expects (ABORTED only after an ABORT
// the shell sent during the job), `irq` up, and ERROR_CODE; the whole window
// as the reference leaves it (every byte, not only C) — after an abort or a
// memory error, outside C unchanged and each C element unchanged or final
// (each byte, for v1 after a reset) (MEMORY_MISMATCH); JOB_CYCLES equal to the
// cycles from START's acceptance to the end and to the cycles the shell saw
// the DUT busy; the bytes counters equal to the accesses the shell accepted;
// for a whole job the profile's counters — v1's documented ones, or ABI 2's
// MACs, tiles, array steps and npu.md §4.2's words read; for ABI 2 the
// cumulative counters equal to the sums of the jobs' counters
// (COUNTER_MISMATCH); ACK clearing STATUS and `irq`. Job kinds, at random:
// valid descriptors; each descriptor error; ABORT during the job and after it
// ended (then ignored); a reset during the job; descriptor writes and START
// while busy (ignored); a malformed CONTROL write (v1's error 7) or a sub-word
// register access (ABI 2: answered with an error, no effect); for ABI 2 a
// memory error on a random access of the job (error 6) and CLEAR_TOTALS. With
// +require_coverage a run fails unless it hits every coverage bin.
//
// +cycle_check (ABI 2, on the memories that answer on time): every job that
// ends without an abort or a memory error must take exactly the cycles of
// npu_model.h's cycle model (CYCLE_MISMATCH).
//
// +case=M,N,K runs one job with A, B and C packed from the window's base
// (minimal strides) on the memory as configured and prints its cycles and
// utilization (useful MACs / (16 x JOB_CYCLES)) — the same-shell measurement.
//
// +conv=H,W,C,KH,KW,N (ABI 2) runs a convolution of an H x W image with C
// channels (channels last) by an KH x KW kernel into N output channels, stride
// 1, no padding, both ways — direct (A in two levels over the image, npu.md
// §4.5) and im2col (the shell writes the M x K matrix first, as the CPU
// would, then a plain job) — checks the two results equal, and prints each
// job's cycles and utilization and the bytes im2col writes.
//
// +selftest=N plants a fault the shell must report: 1 a C byte corrupted
// before the comparison (MEMORY_MISMATCH); 3 BYTES_WRITTEN misread by one
// (COUNTER_MISMATCH); 2, 4 and 5 are the DUT wrapper's (shell_npu_v1.sv).
//
//   npu_shell +seed=<n> [+jobs=<n>] [+require_coverage] [+latency=<1|2>]
//             [+stall_seed=<n> [+long_stall]] [+max_inflight=<n>] [+selftest=<n>]
//   npu_shell +case=<M>,<N>,<K> [memory options]
// Prints one line: NPU <PASS|status> profile=... jobs=... cycles=... coverage=...
#include "Vnpu_shell.h"
#include "verilated.h"

#include "../core/shell_ports.h"
#include "npu_model.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <set>
#include <sstream>
#include <string>

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
std::string dec(std::uint64_t value) { return std::to_string(value); }

// The register pages (docs/phase9.md for v1, docs/npu.md §3 for ABI 2).
enum : std::uint32_t {
    CONTROL = 0x000, STATUS = 0x004, ABI = 0x008, GEOMETRY = 0x00C, A_BASE = 0x010, B_BASE = 0x014,
    C_BASE = 0x018, A_STRIDE = 0x01C, B_STRIDE = 0x020, C_STRIDE = 0x024, REG_M = 0x028, REG_N = 0x02C,
    REG_K = 0x030,
    V1_ERROR_CODE = 0x034, V1_BYTES_READ = 0x038, V1_BYTES_WRITTEN = 0x03C, V1_JOB_CYCLES = 0x040,
    V1_COMPUTE_CYCLES = 0x048, V1_TILES = 0x050,
    V2_MODE = 0x034, V2_ERROR_CODE = 0x038, V2_JOB_CYCLES = 0x080, V2_JOB_ACTIVE = 0x088, V2_JOB_MACS = 0x090,
    V2_JOB_READ = 0x098, V2_JOB_WRITTEN = 0x0A0, V2_JOB_TILES = 0x0A8, V2_TOTAL_JOBS = 0x100,
    V2_TOTAL_CYCLES = 0x108, V2_TOTAL_ACTIVE = 0x110, V2_TOTAL_MACS = 0x118, V2_TOTAL_READ = 0x120,
    V2_TOTAL_WRITTEN = 0x128, V2_A_M0 = 0x03C, V2_A_STRIDE_M1 = 0x040, V2_A_K0 = 0x044, V2_A_STRIDE_K1 = 0x048,
};
enum : std::uint32_t { START = 1, ABORT = 2, ACK = 4, CLEAR_TOTALS = 8 };

enum class Kind { Normal, Error, Abort, AbortAfterEnd, Reset, BusyWrites, BadControl, BusError };

// A job's counters as the DUT reports them (ABI 1: compute = array steps).
struct JobCounters { std::uint64_t cycles = 0, active = 0, macs = 0, read = 0, written = 0, tiles = 0; };

class Shell {
public:
    Shell(Vnpu_shell& dut, const npu::Profile& profile, unsigned memory_seed)
        : d(dut), p(profile), memory(profile.mem_base, profile.mem_bytes), written(profile.mem_bytes, 0),
          garbage(0x5eed1234u) {
        std::mt19937 fill(memory_seed);
        for (auto& byte : memory.bytes) byte = std::uint8_t(fill());
        stall = !plusarg("stall_seed").empty();
        if (stall) stall_rng.seed(std::stoul(plusarg("stall_seed")));
        long_stall = plusflag("long_stall");
        latency = plusarg("latency").empty() ? 2 : std::stoi(plusarg("latency"));
        max_inflight = plusarg("max_inflight").empty() ? 2 : std::stoul(plusarg("max_inflight"));
        selftest = plusarg("selftest").empty() ? 0 : std::stoi(plusarg("selftest"));
        cycle_check = plusflag("cycle_check");
        if (cycle_check && stall) { std::cerr << "+cycle_check needs a memory that answers on time\n"; std::exit(2); }
        d.selftest = selftest;
    }

    std::string failure;
    std::uint64_t cycles = 0;
    std::set<std::string> covered;
    JobCounters last;
    // The memory as the CPU would see it (for +conv's im2col and result compare).
    std::uint8_t peek(std::uint32_t addr) const { return memory.at(addr); }
    void poke(std::uint32_t addr, std::uint8_t value) { memory.at(addr) = value; }

    void fail(const std::string& status, const std::string& detail) {
        if (failure.empty()) {
            failure = status;
            std::cerr << status << ": " << detail << " (cycle " << cycles << ")\n";
            if (job)
                std::cerr << "  job (kind " << int(kind_now) << "): A " << hex(job->a_base) << "+" << job->a_stride
                          << " B " << hex(job->b_base) << "+" << job->b_stride << " C " << hex(job->c_base) << "+"
                          << job->c_stride << " M " << job->m << " N " << job->n << " K " << job->k << " MODE "
                          << job->mode << "\n";
        }
    }

    void reset(int length) {
        d.resetn = 0;
        d.r_req_valid = 0;
        for (int i = 0; i < length; ++i) tick();
        d.resetn = 1;
        port.owed.clear();              // answers owed to the reset DUT are dropped
        stable = shell::StableCheck{};
        error_cycle = error_next = false;
        totals = JobCounters{};
        total_jobs = 0;
    }

    // One clock cycle: the memory's answer and readiness, the DUT, the checks,
    // the edge, then the accepted request performed.
    void tick() {
        d.m_rsp_valid = port.responding();
        d.m_rsp_rdata = port.responding() ? port.owed.front().data : std::uint32_t(garbage());
        d.m_rsp_error = error_cycle ? error_next : garbage() & 1u;
        d.m_req_ready = port.remaining() < max_inflight && !(stall && stall_rng() % 4 == 0);
        d.clk = 0;
        d.eval();
        const bool in_reset = !d.resetn;
        const std::uint32_t addr = std::uint32_t(d.m_req_addr) << 2;
        const std::string violation = in_reset ? "" :
            stable.cycle({bool(d.m_req_valid), addr, d.m_req_we ? 1 : 0, std::uint32_t(d.m_req_wdata),
                          std::uint32_t(d.m_req_be)}, d.m_req_ready, false);
        if (!violation.empty()) fail("REQ_UNSTABLE", "memory request " + violation);
        if (!in_reset && d.chk_busy) ++busy_cycles;
        if (!in_reset && d.chk_busy && d.irq) fail("STATUS_MISMATCH", "irq high while busy");
        if (!in_reset && !d.chk_busy && d.chk_counting) fail("COUNTER_MISMATCH", "a job's counters counting while not busy");
        if (!in_reset && d.chk_done && !done_seen) { done_seen = true; done_cycle = cycles; }
        if (!in_reset && d.chk_done && (!port.owed.empty() || d.m_req_valid))
            fail("DONE_EARLY", "the end shown with a memory request presented or an answer owed");
        else if (!in_reset && !d.chk_busy && (!port.owed.empty() || d.m_req_valid))
            fail("IDLE_ACCESS", "a memory request presented or an answer owed while not busy");
        const bool accept = !in_reset && d.m_req_valid && d.m_req_ready;
        if (accept && port.remaining() + 1 > p.max_in_flight)
            fail("INFLIGHT", "a request accepted with " + dec(port.remaining()) + " unanswered (at most " +
                 dec(p.max_in_flight) + " in flight)");
        std::uint32_t rdata = std::uint32_t(garbage());
        bool error = false;
        if (accept) rdata = perform(addr, error);
        reg_accepted = d.r_req_valid && d.r_req_ready;
        if (reg_accepted) { reg_accept_cycle = cycles; reg_accept_busy = !in_reset && d.chk_busy; }
        reg_answered = !in_reset && d.r_rsp_valid;
        reg_rdata = d.r_rsp_rdata;
        reg_error = d.r_rsp_error;
        d.clk = 1;
        d.eval();
        ++cycles;
        port.advance();
        error_cycle = accept;
        error_next = error;
        if (accept) {
            const int extra = stall ? int(stall_rng() % 3) + (long_stall && stall_rng() % 16 == 0 ? 16 + int(stall_rng() % 48) : 0) : 0;
            port.accept(latency, extra, rdata, error);
        }
    }

    // A register access through the register port: presented until accepted,
    // then waited for its answer.
    std::uint32_t reg(bool write, std::uint32_t offset, std::uint32_t data = 0, std::uint32_t be = 0xF) {
        d.r_req_valid = 1;
        d.r_req_write = write;
        d.r_req_addr = offset;
        d.r_req_wdata = data;
        d.r_req_be = be;
        int guard = 0;
        do { tick(); } while (!reg_accepted && ++guard < 1000);
        d.r_req_valid = 0;
        while (!reg_answered && ++guard < 2000) tick();
        if (guard >= 2000) fail("REG_TIMEOUT", "register access at " + hex(offset));
        if (reg_error && be == 0xF) fail("STATUS_MISMATCH", "a word access at " + hex(offset) + " answered with an error");
        return reg_rdata;
    }
    std::uint64_t reg64(std::uint32_t offset) { return reg(false, offset) | (std::uint64_t(reg(false, offset + 4)) << 32); }
    void control(std::uint32_t command) { reg(true, CONTROL, command, p.abi == 1 ? 0x1 : 0xF); }

    void write_descriptor(const npu::Job& j) {
        const std::pair<std::uint32_t, std::uint32_t> fields[] = {
            {A_BASE, j.a_base}, {B_BASE, j.b_base}, {C_BASE, j.c_base}, {A_STRIDE, j.a_stride},
            {B_STRIDE, j.b_stride}, {C_STRIDE, j.c_stride}, {REG_M, j.m}, {REG_N, j.n}, {REG_K, j.k}};
        for (const auto& [offset, value] : fields) reg(true, offset, value);
        if (p.abi == 2) {
            reg(true, V2_MODE, j.mode);
            reg(true, V2_A_M0, j.a_m0);
            reg(true, V2_A_STRIDE_M1, j.a_stride_m1);
            reg(true, V2_A_K0, j.a_k0);
            reg(true, V2_A_STRIDE_K1, j.a_stride_k1);
        }
    }

    bool terminal(std::uint32_t status) const { return status & (p.done_bit | p.error_bit | p.aborted_bit); }

    // ABI 2's register page as out of reset: every offset reads 0 but ABI and
    // GEOMETRY.
    void sweep(const char* when) {
        for (std::uint32_t offset = 0; offset < 0x1000; offset += 4) {
            const std::uint32_t want = offset == ABI ? 2u : offset == GEOMETRY ? 0x10100404u : 0u;
            if (const std::uint32_t got = reg(false, offset); got != want)
                fail("STATUS_MISMATCH", "register " + hex(offset) + " reads " + hex(got) + " " + when);
        }
    }

    // ABI 2's register page, once per run (npu.md §3): as out of reset;
    // writes to read-only and unmapped offsets change nothing; MODE reads back.
    void check_identity() {
        if (p.abi != 2) return;
        sweep("out of reset");
        for (std::uint32_t offset = 0x004; offset < 0x1000; offset += 4)
            if ((offset < A_BASE || offset > V2_MODE) && (offset < V2_A_M0 || offset > V2_A_STRIDE_K1))
                reg(true, offset, 0xFFFFFFFFu);
        sweep("after writes to read-only and unmapped offsets");
        for (const std::uint32_t offset : {V2_MODE, V2_A_M0, V2_A_STRIDE_M1, V2_A_K0, V2_A_STRIDE_K1}) {
            const std::uint32_t value = offset == V2_MODE ? 2u : 0x9E3779B9u ^ offset;
            reg(true, offset, value);
            if (reg(false, offset) != value) fail("STATUS_MISMATCH", "register " + hex(offset) + " does not read back");
            reg(true, offset, 0);
        }
        covered.insert("register_map");
    }

    void clear_totals() {
        control(CLEAR_TOTALS);
        totals = JobCounters{};
        total_jobs = 0;
        check_totals();
        covered.insert("clear_totals");
    }

    // One job of the given kind; returns false once the run has failed.
    bool run_job(const npu::Job& j, Kind kind, std::mt19937& rng) {
        job = &j;
        kind_now = kind;
        const std::uint32_t error = npu::expected_error(p, j);
        job_error = error != 0;
        const npu::Memory before = memory;
        npu::Memory expected = memory;
        if (!error) npu::apply(j, expected);
        cover(j, error, kind);
        if (kind == Kind::BadControl) return bad_control();
        write_descriptor(j);
        const std::uint64_t accesses = p.abi == 1 ? npu::v1_counters(j).bytes_read + npu::v1_counters(j).bytes_written
                                                  : npu::v2_words_read(j) + std::uint64_t(j.m) * j.n;
        // When to abort or reset: within the job — for ABI 2 its length on the
        // memory that answers on time (the cycle model; stalls only lengthen it).
        const std::uint64_t estimate = p.abi == 2 ? npu::v2_job_cycles(p, j, latency) + 4
                                                  : accesses * (latency + 2) + 64;
        busy_cycles = reads_accepted = lanes_written = writes_accepted = accesses_seen = 0;
        std::fill(written.begin(), written.end(), 0);
        inject_at = kind == Kind::BusError && accesses ? 1 + rng() % accesses : 0;
        done_seen = false;
        control(START);
        const std::uint64_t start_cycle = reg_accept_cycle;
        const std::uint64_t started = cycles;
        const std::uint64_t abort_at = started + (rng() % 4 == 0 ? rng() % 12 : rng() % (estimate + 1));
        const std::uint64_t reset_at = started + rng() % (estimate + 1);
        bool aborted_sent = false, was_reset = false;
        // ABI 2 aims each abort or reset at a phase of the job (the engine's
        // state, chk_state: 1 CHECK, 3 LOAD, 5 TILES; 2, 4, 6, 7 between) and
        // fires in it, at random, or else at the drawn cycle.
        const unsigned target = p.abi != 2 ? 0 : kind == Kind::Abort ? rng() % 5 : kind == Kind::Reset ? rng() % 3 : 0;
        auto in_phase = [&](unsigned aim) {
            const unsigned state = d.chk_state;
            switch (aim) {
                case 1: return state == 1u;
                case 2: return state == 3u;
                case 3: return state == 5u;
                case 4: return state == 2u || state == 4u || state == 6u || state == 7u;
                default: return false;
            }
        };
        auto fire_abort = [&]() {
            return job_error ? cycles >= abort_at || in_phase(1) && rng() % 4 == 0
                 : target ? in_phase(target) && rng() % 4 == 0 : cycles >= abort_at;
        };
        auto fire_reset = [&]() {
            return target ? in_phase(target + 1) && rng() % 4 == 0 : cycles >= reset_at;   // 2 LOAD, 3 TILES
        };
        bool busy_during_writes = false;
        if (kind == Kind::BusyWrites) {
            // Descriptor writes and START while busy must be ignored.
            write_descriptor({0x1234u, 0x5678u, 0x9abcu, 1, 1, 4, 3, 3, 3, 1, 7, 9, 11, 13});
            control(START);
            busy_during_writes = d.chk_busy;
        }
        std::uint32_t status = 0;
        const std::uint64_t limit = started + 20 * estimate + 100000;
        while (failure.empty()) {
            const int wait = 1 + int(rng() % 32);
            for (int i = 0; i < wait; ++i) {
                tick();
                if (kind == Kind::Reset && !was_reset && fire_reset()) {
                    reset_while_busy = d.chk_busy;
                    reset_phase = d.chk_state;
                    reset(3);
                    was_reset = true;
                    break;
                }
                if (kind == Kind::Abort && !aborted_sent && fire_abort()) {
                    abort_phase = d.chk_state;
                    control(ABORT);
                    aborted_sent = true;
                    abort_accept_cycle = reg_accept_cycle;
                    abort_accept_busy = reg_accept_busy;
                }
            }
            if (was_reset) break;
            status = reg(false, STATUS);
            if (terminal(status)) break;
            if (cycles > limit) { fail("TIMEOUT", "job did not end"); return false; }
        }
        if (!failure.empty()) return false;
        if (was_reset) return after_reset(j, before, expected);
        if (kind == Kind::AbortAfterEnd) {
            control(ABORT);
            status = reg(false, STATUS);
        }
        const bool aborted = status & p.aborted_bit;
        if (aborted && !(kind == Kind::Abort && aborted_sent))
            fail("STATUS_MISMATCH", "ABORTED without an ABORT during the job");
        if (aborted && error) covered.insert("abort_error_descriptor");
        // An ABORT the NPU took while busy aborts the job, unless the job ended
        // in that same cycle (npu.md §3); one that reached an idle NPU does not.
        if (kind == Kind::Abort && aborted_sent) {
            if (aborted && !abort_accept_busy) fail("STATUS_MISMATCH", "ABORTED though the ABORT reached an idle NPU");
            if (!aborted && abort_accept_busy && done_cycle != abort_accept_cycle + 1)
                fail("STATUS_MISMATCH", "an ABORT taken while busy did not abort the job");
        }
        if (aborted) {
            covered.insert("abort");
            if (p.abi == 2) covered.insert(abort_phase == 1 ? "abort_check" : abort_phase == 3 ? "abort_load"
                                          : abort_phase == 5 ? "abort_tiles" : "abort_between");
        }
        if (kind == Kind::AbortAfterEnd && !aborted) covered.insert("abort_after_end");
        // A memory error ends the job with error 6, unless it ended first.
        const bool bus_error = !error && !aborted && kind == Kind::BusError && injected;
        const std::uint32_t code = aborted ? 0 : bus_error ? 6 : error;   // an ABORT before the check ends wins
        if (bus_error) covered.insert(injected_write ? "bus_error_write" : "bus_error_read");
        // STATUS, irq and ERROR_CODE.
        const std::uint32_t with_done = p.done_with_error ? p.done_bit : 0;
        const std::uint32_t want = code ? p.error_bit | with_done : aborted ? p.aborted_bit | with_done : p.done_bit;
        if (status != want) fail("STATUS_MISMATCH", "STATUS " + hex(status) + ", expected " + hex(want));
        if (!d.irq) fail("STATUS_MISMATCH", "irq low at the job's end");
        const std::uint32_t got_code = reg(false, p.abi == 1 ? V1_ERROR_CODE : V2_ERROR_CODE);
        if (got_code != code) fail("STATUS_MISMATCH", "ERROR_CODE " + dec(got_code) + ", expected " + dec(code));
        // Memory.
        if (selftest == 1 && !npu::region_c(j).empty()) memory.at(j.c_base + 1) ^= 0x10;
        if (error) compare(before, "after a job with a descriptor error (aborted or not): nothing written");
        else if (aborted || bus_error) check_partial(j, before, expected, 4);
        else compare(expected, "after the job");
        // Counters.
        JobCounters c = read_counters();
        for (int i = 0; i < 8; ++i) tick();
        if (const JobCounters again = read_counters(); again.cycles != c.cycles || again.active != c.active ||
            again.macs != c.macs || again.read != c.read || again.written != c.written || again.tiles != c.tiles)
            fail("COUNTER_MISMATCH", "a job's counters changed after its end showed");
        if (selftest == 3) c.written += 1;
        last = c;
        const std::uint64_t measured = done_cycle - start_cycle - 1;
        if (c.cycles != busy_cycles || c.cycles != measured)
            fail("COUNTER_MISMATCH", "JOB_CYCLES " + dec(c.cycles) + ", the shell saw " + dec(busy_cycles) +
                 " busy cycles and " + dec(measured) + " from START to the end");
        const std::uint64_t want_read = p.abi == 1 ? reads_accepted : 4 * reads_accepted;
        const std::uint64_t want_written = p.abi == 1 ? lanes_written : 4 * writes_accepted;
        if (c.read != want_read || c.written != want_written)
            fail("COUNTER_MISMATCH", "bytes read/written " + dec(c.read) + "/" + dec(c.written) + ", the shell accepted " +
                 dec(want_read) + "/" + dec(want_written));
        check_job_counters(j, c, error != 0, aborted || bus_error);
        if (cycle_check && p.abi == 2 && !aborted && !bus_error) {
            const std::uint64_t model = npu::v2_job_cycles(p, j, latency);
            if (c.cycles != model)
                fail("CYCLE_MISMATCH", "JOB_CYCLES " + dec(c.cycles) + ", the cycle model's " + dec(model));
            else covered.insert("cycle_model");
        }
        if (p.abi == 2) {
            totals.cycles += c.cycles; totals.active += c.active; totals.macs += c.macs;
            totals.read += c.read; totals.written += c.written;
            ++total_jobs;
            check_totals();
        }
        if (kind == Kind::BusyWrites) {
            if (reg(false, A_BASE) != j.a_base || reg(false, REG_K) != j.k
                || (p.abi == 2 && (reg(false, V2_MODE) != j.mode || reg(false, V2_A_M0) != j.a_m0
                                   || reg(false, V2_A_STRIDE_M1) != j.a_stride_m1 || reg(false, V2_A_K0) != j.a_k0
                                   || reg(false, V2_A_STRIDE_K1) != j.a_stride_k1)))
                fail("STATUS_MISMATCH", "a descriptor write while busy took effect");
            else if (busy_during_writes) covered.insert("busy_writes");
        }
        // ACK clears STATUS and irq.
        control(ACK);
        if (const std::uint32_t after = reg(false, STATUS); after != 0)
            fail("STATUS_MISMATCH", "STATUS " + hex(after) + " after ACK");
        if (d.irq) fail("STATUS_MISMATCH", "irq high after ACK");
        job = nullptr;
        inject_at = 0;
        injected = false;
        return failure.empty();
    }

private:
    JobCounters read_counters() {
        JobCounters c;
        if (p.abi == 1) {
            c.cycles = reg64(V1_JOB_CYCLES);
            c.active = reg64(V1_COMPUTE_CYCLES);
            c.read = reg(false, V1_BYTES_READ);
            c.written = reg(false, V1_BYTES_WRITTEN);
            c.tiles = reg(false, V1_TILES);
        } else {
            c.cycles = reg64(V2_JOB_CYCLES);
            c.active = reg64(V2_JOB_ACTIVE);
            c.macs = reg64(V2_JOB_MACS);
            c.read = reg64(V2_JOB_READ);
            c.written = reg64(V2_JOB_WRITTEN);
            c.tiles = reg(false, V2_JOB_TILES);
        }
        return c;
    }

    void check_job_counters(const npu::Job& j, const JobCounters& c, bool descriptor_error, bool partial) {
        JobCounters want;
        const std::uint64_t tiles = j.m && j.n ? std::uint64_t((j.m + 3) / 4) * ((j.n + 3) / 4) : 0;
        if (!descriptor_error) {
            if (p.abi == 1) {
                const npu::Counters v1 = npu::v1_counters(j);
                want.read = v1.bytes_read; want.written = v1.bytes_written; want.active = v1.compute_cycles;
                want.tiles = v1.tiles;
            } else if (npu::v2_ksplit(j)) {      // a strip of ceil(K/4) steps, one word a row
                const std::uint64_t strips = j.m && j.n ? (j.m + 3) / 4 : 0;
                want.macs = std::uint64_t(j.m) * j.n * j.k;
                want.tiles = strips;
                want.active = std::uint64_t((j.k + 3) / 4) * strips;
                want.read = 4 * npu::v2_words_read(j);
                want.written = 4ull * j.m * j.n;
            } else {
                want.macs = std::uint64_t(j.m) * j.n * j.k;
                want.tiles = tiles;
                want.active = std::uint64_t(j.k) * tiles;
                want.read = 4 * npu::v2_words_read(j);
                want.written = 4ull * j.m * j.n;
            }
        }
        if (!partial) {
            if (c.read != want.read || c.written != want.written || c.active != want.active || c.tiles != want.tiles
                || c.macs != want.macs)
                fail("COUNTER_MISMATCH", "read/written/active/tiles/macs " + dec(c.read) + "/" + dec(c.written) + "/" +
                     dec(c.active) + "/" + dec(c.tiles) + "/" + dec(c.macs) + ", expected " + dec(want.read) + "/" +
                     dec(want.written) + "/" + dec(want.active) + "/" + dec(want.tiles) + "/" + dec(want.macs));
        } else if (c.written > want.written || c.tiles > want.tiles || c.active > want.active || c.macs > want.macs) {
            fail("COUNTER_MISMATCH", "a stopped job counted more than the whole job");
        }
    }

    void check_totals() {
        if (p.abi != 2) return;
        const std::uint64_t jobs = reg(false, V2_TOTAL_JOBS), cyc = reg64(V2_TOTAL_CYCLES), act = reg64(V2_TOTAL_ACTIVE),
                            macs = reg64(V2_TOTAL_MACS), rd = reg64(V2_TOTAL_READ), wr = reg64(V2_TOTAL_WRITTEN);
        if (jobs != total_jobs || cyc != totals.cycles || act != totals.active || macs != totals.macs
            || rd != totals.read || wr != totals.written)
            fail("COUNTER_MISMATCH", "totals jobs/cycles/active/macs/read/written " + dec(jobs) + "/" + dec(cyc) + "/" +
                 dec(act) + "/" + dec(macs) + "/" + dec(rd) + "/" + dec(wr) + ", the sums of the jobs' " +
                 dec(total_jobs) + "/" + dec(totals.cycles) + "/" + dec(totals.active) + "/" + dec(totals.macs) + "/" +
                 dec(totals.read) + "/" + dec(totals.written));
    }

    std::uint32_t perform(std::uint32_t addr, bool& error) {
        if (!memory.contains(addr, 4)) {
            fail("OUT_OF_WINDOW", "access at " + hex(addr));
            error = true;
            return 0;
        }
        if (job && job_error) fail("ACCESS_ON_ERROR", "access at " + hex(addr) + " in a job that must end in an error");
        if (job && inject_at && ++accesses_seen == inject_at) {
            error = true;              // answered with an error, not performed (but accepted: counted)
            injected = true;
            injected_write = d.m_req_we;
            ++(d.m_req_we ? writes_accepted : reads_accepted);
            return std::uint32_t(garbage());
        }
        if (d.m_req_we) {
            if (p.abi == 2 && d.m_req_be != 0xF) fail("PARTIAL_WRITE", "write to " + hex(addr) + " not a whole word");
            ++writes_accepted;
            for (int lane = 0; lane < 4; ++lane) {
                if (!((d.m_req_be >> lane) & 1u)) continue;
                if (!job || !npu::in_c(*job, addr + lane))
                    fail("STRAY_WRITE", "write to " + hex(addr + lane) + ", not a byte of C");
                std::uint8_t& mark = written[addr + lane - memory.base];
                if (mark) fail("DOUBLE_WRITE", "C byte " + hex(addr + lane) + " written twice");
                mark = 1;
                ++lanes_written;
                memory.at(addr + lane) = std::uint8_t(std::uint32_t(d.m_req_wdata) >> (8 * lane));
            }
            return std::uint32_t(garbage());
        }
        if (!job || !(npu::overlap({addr, addr + 4ull}, npu::region_a(*job))
                      || npu::overlap({addr, addr + 4ull}, npu::region_b(*job))))
            fail("STRAY_READ", "read of " + hex(addr) + ", outside A and B");
        ++reads_accepted;
        std::uint32_t word = 0;
        for (int lane = 0; lane < 4; ++lane) word |= std::uint32_t(memory.at(addr + lane)) << (8 * lane);
        return word;
    }

    void compare(const npu::Memory& expected, const char* when) {
        for (std::size_t i = 0; i < memory.bytes.size(); ++i)
            if (memory.bytes[i] != expected.bytes[i]) {
                fail("MEMORY_MISMATCH", "byte " + hex(memory.base + i) + " is " + hex(memory.bytes[i]) +
                     ", expected " + hex(expected.bytes[i]) + " " + when);
                return;
            }
    }

    // After an abort or a memory error (grain 4: each C element) or a reset
    // (grain 1 for v1, which writes bytes): outside C unchanged; each grain of
    // C unchanged or final.
    void check_partial(const npu::Job& j, const npu::Memory& before, const npu::Memory& expected, int grain) {
        for (std::size_t i = 0; i < memory.bytes.size(); ++i) {
            const std::uint32_t addr = memory.base + std::uint32_t(i);
            if (!npu::in_c(j, addr) && memory.bytes[i] != before.bytes[i]) {
                fail("MEMORY_MISMATCH", "byte " + hex(addr) + " outside C changed");
                return;
            }
        }
        for (std::uint32_t row = 0; row < j.m; ++row)
            for (std::uint32_t col = 0; col < j.n; ++col)
                for (int start = 0; start < 4; start += grain) {
                    const std::uint32_t at = j.c_base + row * j.c_stride + 4 * col + start;
                    if (!memory.contains(at, grain)) {      // (a valid job's C is in the window)
                        fail("MEMORY_MISMATCH", "C element " + hex(at) + " outside the window in a partial check");
                        return;
                    }
                    bool old = true, fresh = true;
                    for (int b = 0; b < grain; ++b) {
                        old = old && memory.at(at + b) == before.at(at + b);
                        fresh = fresh && memory.at(at + b) == expected.at(at + b);
                    }
                    if (!old && !fresh) {
                        fail("MEMORY_MISMATCH", "C(" + dec(row) + "," + dec(col) + ") at " + hex(at) +
                             " neither unchanged nor final");
                        return;
                    }
                }
    }

    bool after_reset(const npu::Job& j, const npu::Memory& before, const npu::Memory& expected) {
        if (reset_while_busy) {
            covered.insert("reset");
            if (p.abi == 2 && reset_phase == 3) covered.insert("reset_load");
            if (p.abi == 2 && reset_phase == 5) covered.insert("reset_tiles");
        }
        if (const std::uint32_t status = reg(false, STATUS); status != 0)
            fail("STATUS_MISMATCH", "STATUS " + hex(status) + " after reset");
        const JobCounters c = read_counters();
        if (reg(false, A_BASE) != 0 || c.cycles != 0 || c.written != 0)
            fail("STATUS_MISMATCH", "registers not cleared by reset");
        if (p.abi == 2) sweep("after a reset");
        if (d.irq) fail("STATUS_MISMATCH", "irq high after reset");
        if (job_error) compare(before, "after a reset of a job with a descriptor error");
        else check_partial(j, before, expected, p.abi == 1 ? 1 : 4);
        check_totals();
        job = nullptr;
        inject_at = 0;
        injected = false;
        return failure.empty();
    }

    bool bad_control() {
        if (p.abi == 1) {
            // v1: a CONTROL write of an unknown command is error 7 (phase9.md).
            control(3);
            const std::uint32_t status = reg(false, STATUS), code = reg(false, V1_ERROR_CODE);
            if (!(status & p.error_bit) || code != 7)
                fail("STATUS_MISMATCH", "malformed CONTROL: STATUS " + hex(status) + ", ERROR_CODE " + dec(code));
            control(ACK);
            if (reg(false, STATUS) != 0) fail("STATUS_MISMATCH", "STATUS after ACK of error 7");
            covered.insert("bad_control");
        } else {
            // ABI 2: a sub-word access is answered with an error and has no effect.
            const std::uint32_t old_k = reg(false, REG_K);
            reg(true, CONTROL, START, 0x1);
            const bool start_error = reg_error;
            reg(true, REG_K, old_k + 7, 0x3);
            const bool write_error = reg_error;
            const std::uint32_t value = reg(false, REG_K, 0, 0x1);
            const bool read_error = reg_error;
            if (!start_error || !write_error || !read_error || value != 0)
                fail("STATUS_MISMATCH", "a sub-word register access was not answered with an error and nothing");
            if (reg(false, STATUS) != 0 || reg(false, REG_K) != old_k)
                fail("STATUS_MISMATCH", "a sub-word register access took effect");
            covered.insert("bad_access");
        }
        job = nullptr;
        return failure.empty();
    }

    void cover(const npu::Job& j, std::uint32_t error, Kind kind) {
        if (kind == Kind::BadControl) return;
        if (error) {
            covered.insert("error" + dec(error));
            const std::uint64_t past = 1ull << 32;
            if (npu::region_a(j).hi > past || npu::region_b(j).hi > past || npu::region_c(j).hi > past)
                covered.insert("extent_past_2^32");
            return;
        }
        const std::uint64_t top = std::uint64_t(p.mem_base) + p.mem_bytes;
        if (npu::region_a(j).hi == top && !npu::region_a(j).empty()) covered.insert("a_at_top");
        if (npu::region_b(j).hi == top && !npu::region_b(j).empty()) covered.insert("b_at_top");
        if (npu::region_c(j).hi == top && !npu::region_c(j).empty()) covered.insert("c_at_top");
        for (const npu::Region& r : {npu::region_a(j), npu::region_b(j), npu::region_c(j)})
            if (!r.empty() && r.lo == p.mem_base) covered.insert("at_base");
        if (j.m == p.max_dim || j.n == p.max_dim || j.k == p.max_dim) covered.insert("dim_max");
        const std::pair<const char*, std::uint32_t> dims[] = {{"m", j.m}, {"n", j.n}, {"k", j.k}};
        for (const auto& [name, value] : dims) {
            if (value) covered.insert(std::string(name) + "%4=" + dec(value % 4));
            if (value <= 1) covered.insert(std::string(name) + "=" + dec(value));
        }
        if (j.m && j.k && j.a_base % 4) covered.insert("a_unaligned");
        if (j.k && j.n && j.b_base % 4) covered.insert("b_unaligned");
        if (j.m && j.n && j.c_base % 4) covered.insert("c_unaligned");
        if (j.m > 1 && j.k && j.a_stride == j.k) covered.insert("a_stride_min");
        if (j.k > 1 && j.n && j.b_stride == j.n) covered.insert("b_stride_min");
        if (j.m > 1 && j.n && j.c_stride == 4 * j.n) covered.insert("c_stride_min");
        if (p.abi == 2) {
            covered.insert("mode" + dec(j.mode));
            if (npu::v2_ksplit(j) && j.m && j.k) {
                covered.insert(j.mode == 2 ? "ksplit_mode2" : "ksplit_auto");
                covered.insert(j.b_stride == 1 ? "ksplit_b_stride1" : "ksplit_b_gathered");
                covered.insert("ksplit_k%4=" + dec(j.k % 4));
            }
            if (!npu::v2_ksplit(j) && j.n == 1 && j.m && j.k) covered.insert("tiles_n1");
            if (j.m && j.k && j.a_m0 && j.a_m0 < j.m) covered.insert("a_two_level_m");
            if (j.m && j.k && j.a_k0 && j.a_k0 < j.k) covered.insert("a_two_level_k");
            if (j.m && j.k && j.a_m0 && j.a_k0 && j.a_k0 < j.k && j.a_m0 < j.m) covered.insert("a_conv");
            if (j.m && j.k && j.a_k0 && j.a_k0 < j.k && j.k % j.a_k0) covered.insert("a_k0_partial");
            if (j.m > 4 && j.k && j.a_m0 && j.a_m0 < j.m && j.a_m0 % 4) covered.insert("a_m0_wraps_strip");
            if (j.m > 1 && j.k && j.a_stride < j.k) covered.insert("a_rows_overlap");
            if (j.k > 1 && j.n && j.b_stride < j.n) covered.insert("b_rows_overlap");
            if (j.m && j.k && j.n && (j.n + 3) / 4 > 4096 / j.k) covered.insert("multiple_panels");
        }
    }

    Vnpu_shell& d;
    npu::Profile p;
    npu::Memory memory;
    std::vector<std::uint8_t> written;   // C bytes written in this job
    shell::Port port;
    shell::StableCheck stable;
    std::mt19937 stall_rng, garbage;
    bool stall = false, long_stall = false;
    int latency = 2, selftest = 0;
    bool cycle_check = false;
    std::size_t max_inflight = 2;
    bool error_cycle = false, error_next = false;
    bool reg_accepted = false, reg_answered = false, reg_error = false;
    std::uint32_t reg_rdata = 0;
    std::uint64_t reg_accept_cycle = 0, done_cycle = 0, abort_accept_cycle = 0;
    bool reg_accept_busy = false, abort_accept_busy = false;
    bool done_seen = false;
    std::uint64_t busy_cycles = 0, reads_accepted = 0, lanes_written = 0, writes_accepted = 0, accesses_seen = 0;
    std::uint64_t inject_at = 0;
    bool injected = false, injected_write = false;
    bool reset_while_busy = false;
    unsigned abort_phase = 0, reset_phase = 0;     // the engine's state then (shell_npu_v2.sv's chk_state)
    JobCounters totals;
    std::uint64_t total_jobs = 0;
    const npu::Job* job = nullptr;
    Kind kind_now = Kind::Normal;
    bool job_error = false;
};

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const unsigned seed = plusarg("seed").empty() ? 1u : unsigned(std::stoul(plusarg("seed")));
    Vnpu_shell dut;
    dut.eval();
    const npu::Profile profile = dut.chk_abi == 2 ? npu::v2_profile() : npu::v1_profile();
    Shell shell(dut, profile, seed * 7919u + 13u);
    shell.reset(8);
    shell.check_identity();

    if (!plusarg("case").empty()) {
        npu::Job j;
        if (std::sscanf(plusarg("case").c_str(), "%u,%u,%u", &j.m, &j.n, &j.k) != 3) {
            std::cerr << "+case=M,N,K\n";
            return 2;
        }
        j.a_stride = j.k; j.b_stride = j.n; j.c_stride = 4 * j.n;
        j.a_base = profile.mem_base;
        j.b_base = (j.a_base + j.m * j.k + 3) & ~3u;
        j.c_base = (j.b_base + j.k * j.n + 3) & ~3u;
        std::mt19937 rng(seed);
        if (npu::expected_error(profile, j) != 0) { std::cerr << "the case does not fit the window\n"; return 2; }
        shell.run_job(j, Kind::Normal, rng);
        const double macs = double(j.m) * j.n * j.k;
        std::printf("NPU %s profile=%s case=%ux%ux%u job_cycles=%llu array_steps=%llu macs=%.0f "
                    "utilization=%.4f%%\n", shell.failure.empty() ? "PASS" : shell.failure.c_str(),
                    profile.name.c_str(), j.m, j.n, j.k, (unsigned long long)shell.last.cycles,
                    (unsigned long long)shell.last.active, macs,
                    shell.last.cycles ? 100.0 * macs / (16.0 * double(shell.last.cycles)) : 0.0);
        return shell.failure.empty() ? 0 : 1;
    }

    if (!plusarg("conv").empty()) {
        unsigned h = 0, w = 0, c = 0, kh = 0, kw = 0, n = 0;
        if (profile.abi != 2 || std::sscanf(plusarg("conv").c_str(), "%u,%u,%u,%u,%u,%u", &h, &w, &c, &kh, &kw, &n) != 6
            || kh > h || kw > w) {
            std::cerr << "+conv=H,W,C,KH,KW,N (ABI 2)\n";
            return 2;
        }
        const std::uint32_t oh = h - kh + 1, ow = w - kw + 1, m = oh * ow, k = kh * kw * c;
        auto align = [](std::uint32_t a) { return (a + 3) & ~3u; };
        const std::uint32_t image = profile.mem_base, weights = align(image + h * w * c);
        const std::uint32_t out = align(weights + k * n), matrix = align(out + 4 * m * n);
        if (matrix + m * k > profile.mem_base + profile.mem_bytes) { std::cerr << "the convolution does not fit\n"; return 2; }
        npu::Job direct;
        direct.a_base = image; direct.a_stride = c; direct.a_m0 = ow; direct.a_stride_m1 = w * c;
        direct.a_k0 = kw * c; direct.a_stride_k1 = w * c;
        direct.b_base = weights; direct.b_stride = n; direct.c_base = out; direct.c_stride = 4 * n;
        direct.m = m; direct.n = n; direct.k = k;
        std::mt19937 rng(seed);
        if (npu::expected_error(profile, direct)) { std::cerr << "the convolution's descriptor is an error\n"; return 2; }
        shell.run_job(direct, Kind::Normal, rng);
        const JobCounters d_counters = shell.last;
        std::vector<std::uint8_t> result(4 * m * n);
        for (std::uint32_t i = 0; i < result.size(); ++i) result[i] = shell.peek(out + i);
        // im2col: row (oy, ox), column (ky, kx, channel) — the CPU's work, not timed.
        for (std::uint32_t oy = 0; oy < oh; ++oy)
            for (std::uint32_t ox = 0; ox < ow; ++ox)
                for (std::uint32_t ky = 0; ky < kh; ++ky)
                    for (std::uint32_t kx = 0; kx < kw; ++kx)
                        for (std::uint32_t ch = 0; ch < c; ++ch)
                            shell.poke(matrix + (oy * ow + ox) * k + (ky * kw + kx) * c + ch,
                                       shell.peek(image + ((oy + ky) * w + ox + kx) * c + ch));
        for (std::uint32_t i = 0; i < result.size(); ++i) shell.poke(out + i, std::uint8_t(~result[i]));
        npu::Job lowered = direct;
        lowered.a_base = matrix; lowered.a_stride = k; lowered.a_m0 = lowered.a_stride_m1 = lowered.a_k0 = lowered.a_stride_k1 = 0;
        shell.run_job(lowered, Kind::Normal, rng);
        for (std::uint32_t i = 0; i < result.size() && shell.failure.empty(); ++i)
            if (shell.peek(out + i) != result[i]) shell.fail("MEMORY_MISMATCH", "the two lowerings' results differ");
        const double macs = double(m) * n * k;
        auto util = [&](std::uint64_t cyc) { return cyc ? 100.0 * macs / (16.0 * double(cyc)) : 0.0; };
        std::printf("NPU %s profile=%s conv=%ux%ux%u kernel=%ux%u n=%u m=%u k=%u direct_cycles=%llu direct_utilization=%.4f%% "
                    "im2col_cycles=%llu im2col_utilization=%.4f%% im2col_bytes=%u\n",
                    shell.failure.empty() ? "PASS" : shell.failure.c_str(), profile.name.c_str(), h, w, c, kh, kw, n, m, k,
                    (unsigned long long)d_counters.cycles, util(d_counters.cycles), (unsigned long long)shell.last.cycles,
                    util(shell.last.cycles), m * k);
        return shell.failure.empty() ? 0 : 1;
    }

    if (plusflag("edges")) {
        // The directed edge list (ABI 2): the limits, every panel shape, zero
        // strides, empty regions outside the window, and extents past 2^32.
        // Each must end as the reference says: +edges_expect reports each
        // job's end (done or its error code), for the record.
        if (profile.abi != 2) { std::cerr << "+edges is ABI 2's\n"; return 2; }
        const std::uint32_t base = profile.mem_base;
        const npu::Job edges[] = {
            // a_base, b_base, c_base, a_stride, b_stride, c_stride, m, n, k, mode
            {base, base + 0x4000, base + 0x10000, 4096, 8, 32, 4, 8, 4096, 0},          // K at the limit: 2 panels
            {base + 1, base + 0x5003, base + 0x15000, 4096, 13, 52, 5, 13, 4096, 1},   // 4 panels of one group
            {base + 2, base + 0x2002, base + 0x16000, 1366, 40, 160, 6, 40, 1366, 0},  // 5 panels of two groups
            {base + 3, base + 0x100, base + 0x200, 0, 0, 36, 7, 9, 13, 1},             // both strides 0, unaligned
            {base + 1, base + 0x2000, base + 0x3000, 1, 1, 4, 4096, 1, 1, 0},          // M at the limit
            {base, base + 0x10, base + 0x2000, 1, 4096, 16384, 1, 4096, 1, 1},         // N at the limit
            {base + 3, base + 0x1001, base + 0x3000, 4096, 1, 4, 1, 1, 4096, 0},       // K at the limit, N = 1
            {base + 5, base + 0x800, base + 0x1000, 37, 1, 4, 9, 1, 37, 2},            // MODE 2
            {0, 0, base, 0, 0, 24, 5, 6, 0, 0},                                         // K = 0: A, B empty, outside
            {0, base, 0, 0, 0, 28, 0, 7, 9, 0},                                         // M = 0: A and C empty, outside
            {base, base + 0x100, base + 0x200, 0xFFFFFFF0u, 4, 16, 2, 4, 4, 0},         // A's extent past 2^32
            {base, base + 0x100, base + 0x200, 4, 0xFFFF0000u, 16, 2, 4, 3, 0},         // B's extent past 2^32
            {base, base + 0x100, base + 0x200, 4, 4, 0xFFFFFFF0u, 2, 4, 4, 0},          // C's extent past 2^32
            {0xFFFFFFF0u, base + 0x100, base + 0x200, 4, 4, 16, 2, 4, 32, 0},           // A's base near 2^32
            {base, base + 0x100, base + 0x201, 4, 4, 16, 0, 4, 4, 0},                   // M = 0, C misaligned: error 2
            // K-split (N = 1; 19.2)
            {base + 1, base + 0x9003, base + 0xD000, 4096, 3, 4, 9, 1, 4096, 0},       // K at the limit, B gathered
            {base + 2, base + 0x9001, base + 0xB000, 4096, 1, 4, 5, 1, 4096, 2},       // K at the limit, B packed, MODE 2
            {base + 3, base + 0x101, base + 0x200, 1, 1, 4, 6, 1, 1, 0},               // K = 1
            {base + 1, base + 0x102, base + 0x200, 2, 0, 4, 5, 1, 2, 2},               // K = 2, B_STRIDE 0
            {base + 2, base + 0x103, base + 0x200, 3, 7, 8, 7, 1, 3, 0},               // K = 3, B gathered
            {base + 3, base + 0x2001, base + 0x3000, 5, 1, 4, 11, 1, 23, 0},           // A rows overlapping
            {base, base + 0x2000, base + 0x3000, 37, 1, 4, 4096, 1, 0, 0},             // K = 0, M at the limit
            {base + 1, base + 0x2003, base + 0x3000, 37, 1, 4, 9, 1, 37, 1},           // N = 1 as tiles (MODE 1)
            // A in two levels (19.3): a_base, ..., mode, A_M0, A_STRIDE_M1, A_K0, A_STRIDE_K1
            {base, base + 0x400, base + 0x1000, 1, 1, 4, 784, 1, 25, 0, 28, 32, 5, 32},           // Conv2D, direct (K-split)
            {base + 1, base + 0x400, base + 0x1000, 3, 16, 64, 196, 16, 27, 0, 14, 48, 9, 48},    // CIFAR conv1, channels last
            {base + 2, base + 0x400, base + 0x2000, 16, 32, 128, 25, 32, 144, 0, 5, 112, 48, 112},  // CIFAR conv2, channels last
            {base + 3, base + 0x100, base + 0x400, 5, 3, 12, 9, 3, 11, 0, 1, 40, 1, 7},           // A_M0 = A_K0 = 1
            {base, base + 0x100, base + 0x400, 2, 3, 12, 6, 3, 10, 1, 100, 9, 64, 3},             // levels larger than M, K
            {base + 1, base + 0x200, base + 0x800, 6, 7, 28, 13, 7, 23, 0, 3, 50, 4, 9},          // A_K0 not dividing K
            {base, base + 0x100, base + 0x200, 4, 4, 16, 9, 4, 8, 0, 2, 0xFFFFFFF0u, 0, 0},     // A's two-level extent past 2^32
            // A's two-level extent is (5 div 2) x 50 + 1 x 3 + (9 div 4) x 20 + 3 + 1 = 147 bytes: ending
            // exactly at the window's top (valid), then one byte past it (error 4).
            {base + 0x18000 - 147, base + 0x100, base + 0x400, 3, 4, 16, 6, 4, 10, 0, 2, 50, 4, 20},
            {base + 0x18000 - 146, base + 0x100, base + 0x400, 3, 4, 16, 6, 4, 10, 0, 2, 50, 4, 20},
            // A_K0 = 4 not dividing K = 23: a row's last segment, 3 bytes, starts a byte into a word.
            {base + 1, base + 0x800, base + 0xC00, 64, 5, 20, 5, 5, 23, 0, 0, 0, 4, 8},
            // A convolution over two B panels: 5x5x64 channels last, 4x4 kernel (K = 1024), 20 outputs.
            {base, base + 0x800, base + 0x5800, 64, 20, 80, 4, 20, 1024, 0, 2, 320, 256, 320},
            {base + 2, base + 0x100, base + 0x200, 4, 3, 12, 3, 3, 10, 0, 0, 0, 0x10005, 7},       // A_K0 above K
            {base + 1, base + 0x100, base + 0x200, 5, 3, 12, 5, 3, 9, 1, 0x80000003u, 9, 0, 0},     // A_M0 above M
            {base, base + 0x1800, base + 0x2000, 3, 1, 4, 4096, 1, 1, 0, 1, 1, 0, 0},               // M at its limit, A_M0 = 1
        };
        std::mt19937 rng(seed);
        int ran = 0;
        for (const npu::Job& j : edges) {
            if (!shell.failure.empty()) break;
            if (plusflag("edges_expect"))
                std::fprintf(stderr, "edge %d: M %u N %u K %u -> %s\n", ran, j.m, j.n, j.k,
                             npu::expected_error(profile, j) ? ("error " + dec(npu::expected_error(profile, j))).c_str() : "done");
            shell.run_job(j, Kind::Normal, rng);
            ++ran;
        }
        std::printf("NPU %s profile=%s edges=%d cycles=%llu\n", shell.failure.empty() ? "PASS" : shell.failure.c_str(),
                    profile.name.c_str(), ran, (unsigned long long)shell.cycles);
        return shell.failure.empty() ? 0 : 1;
    }

    const int jobs = plusarg("jobs").empty() ? 1000 : std::stoi(plusarg("jobs"));
    const bool trace = plusflag("trace_jobs");     // each job's descriptor on stderr as it starts
    npu::Generator gen(profile, seed);
    std::mt19937& rng = gen.random();
    int done = 0;
    for (; done < jobs && shell.failure.empty(); ++done) {
        if (profile.abi == 2 && rng() % 40 == 0) shell.clear_totals();
        const unsigned roll = rng() % 100;
        Kind kind = roll < 62 ? Kind::Normal : roll < 76 ? Kind::Error : roll < 82 ? Kind::Abort
                  : roll < 85 ? Kind::AbortAfterEnd : roll < 88 ? Kind::Reset : roll < 93 ? Kind::BusyWrites
                  : roll < 96 ? Kind::BadControl : Kind::BusError;
        if (kind == Kind::BusError && profile.abi == 1) kind = Kind::Normal;
        npu::Job j = kind == Kind::Error || (kind == Kind::Abort && profile.abi == 2 && rng() % 5 == 0)
                         ? gen.error(1 + int(rng() % 6)) : gen.valid();
        if (kind == Kind::BusyWrites) {
            // Only a job long enough that the writes land while it is busy.
            const bool long_enough = profile.abi == 1
                ? npu::v1_counters(j).bytes_read + npu::v1_counters(j).bytes_written >= 400
                : npu::v2_job_cycles(profile, j, 2) >= 80;
            if (!long_enough) kind = Kind::Normal;
        }
        if (trace)
            std::fprintf(stderr, "job %d kind %d: A 0x%x+%u B 0x%x+%u C 0x%x+%u M %u N %u K %u MODE %u (cycle %llu)\n",
                         done, int(kind), j.a_base, j.a_stride, j.b_base, j.b_stride, j.c_base, j.c_stride, j.m, j.n,
                         j.k, j.mode, (unsigned long long)shell.cycles);
        shell.run_job(j, kind, rng);
    }
    std::vector<std::string> wanted = npu::bins(profile);
    if (plusflag("cycle_check")) wanted.push_back("cycle_model");
    std::vector<std::string> missing;
    for (const auto& bin : wanted) if (!shell.covered.count(bin)) missing.push_back(bin);
    std::string result = shell.failure.empty() ? "PASS" : shell.failure;
    if (result == "PASS" && plusflag("require_coverage") && !missing.empty()) result = "COVERAGE_MISSED";
    std::printf("NPU %s profile=%s seed=%u jobs=%d cycles=%llu coverage=%zu/%zu", result.c_str(),
                profile.name.c_str(), seed, done, (unsigned long long)shell.cycles,
                wanted.size() - missing.size(), wanted.size());
    if (!missing.empty()) {
        std::printf(" missing=");
        for (std::size_t i = 0; i < missing.size(); ++i) std::printf("%s%s", i ? "," : "", missing[i].c_str());
    }
    std::printf("\n");
    return result == "PASS" ? 0 : 1;
}
