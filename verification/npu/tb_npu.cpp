// The NPU shell (Phase 19, docs/npu.md §6): an NPU DUT (shell_npu_*.sv) on
// the CPU shell's memory model, driven through its register port job by job,
// each job checked against the independent reference (npu_model.h).
//
// Memory: the profile's window, filled with random bytes; requests accepted
// when fewer than +max_inflight (default 2) answers are owed, answered in
// order after +latency (1 or 2, default 2) cycles; +stall_seed adds random
// ready-low cycles and 0-2 extra cycles per answer, +long_stall one answer in
// sixteen 16-63 cycles late — the CPU shell's modes (verification/core). Read
// data and the error signal are garbage outside their cycles; an error comes
// in the cycle after acceptance. Checks every cycle: a request not accepted
// is presented unchanged (REQ_UNSTABLE); an access lies in the window
// (OUT_OF_WINDOW); a read's word overlaps A or B and a write's enabled bytes
// are C's (STRAY_READ, STRAY_WRITE); no access during a job that must end in
// a descriptor error (ACCESS_ON_ERROR); none presented or owed while the DUT
// shows DONE (DONE_EARLY) or is not busy (IDLE_ACCESS). (v1 takes one
// transaction at a time, so +max_inflight above 1 changes nothing for it but
// the stall pattern.)
//
// Each job: the descriptor written, START, STATUS polled every 1-32 cycles,
// then: STATUS exactly as the reference expects (ABORTED only after an ABORT
// the shell sent during the job) and ERROR_CODE; the whole window as
// the reference leaves it (every byte, not only C) — after an abort, outside C
// unchanged and each C element either unchanged or final, after a reset each
// C byte so (MEMORY_MISMATCH); JOB_CYCLES equal to the cycles the shell saw
// the DUT busy, BYTES_READ and BYTES_WRITTEN equal to the reads and write
// lanes the shell accepted, and for a whole job the profile's counters
// (COUNTER_MISMATCH); ACK clearing
// STATUS. Job kinds, chosen at random: valid descriptors; each descriptor
// error; ABORT during the job and after it ended (then ignored); a reset
// pulse during the job; descriptor writes and START while busy (ignored); a
// malformed CONTROL write (v1's error 7). With +require_coverage a run fails
// unless it hits every coverage bin of npu_model.h.
//
// +case=M,N,K runs one job with A, B and C packed from the window's base
// (minimal strides) on the memory as configured and prints its cycles and
// utilization (useful MACs / (16 x JOB_CYCLES)) — the same-shell measurement.
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

// v1's register page (docs/phase9.md).
enum : std::uint32_t {
    CONTROL = 0x000, STATUS = 0x004, A_BASE = 0x010, B_BASE = 0x014, C_BASE = 0x018, A_STRIDE = 0x01C,
    B_STRIDE = 0x020, C_STRIDE = 0x024, REG_M = 0x028, REG_N = 0x02C, REG_K = 0x030, ERROR_CODE = 0x034,
    BYTES_READ = 0x038, BYTES_WRITTEN = 0x03C, JOB_CYCLES = 0x040, COMPUTE_CYCLES = 0x048, TILES = 0x050,
};
enum : std::uint32_t { START = 1, ABORT = 2, ACK = 4 };

enum class Kind { Normal, Error, Abort, AbortAfterEnd, Reset, BusyWrites, BadControl };

class Shell {
public:
    Shell(Vnpu_shell& dut, const npu::Profile& profile, unsigned memory_seed)
        : d(dut), p(profile), memory(profile.mem_base, profile.mem_bytes), garbage(0x5eed1234u) {
        std::mt19937 fill(memory_seed);
        for (auto& byte : memory.bytes) byte = std::uint8_t(fill());
        stall = !plusarg("stall_seed").empty();
        if (stall) stall_rng.seed(std::stoul(plusarg("stall_seed")));
        long_stall = plusflag("long_stall");
        latency = plusarg("latency").empty() ? 2 : std::stoi(plusarg("latency"));
        max_inflight = plusarg("max_inflight").empty() ? 2 : std::stoul(plusarg("max_inflight"));
        selftest = plusarg("selftest").empty() ? 0 : std::stoi(plusarg("selftest"));
        d.selftest = selftest;
    }

    std::string failure;
    std::uint64_t cycles = 0;
    std::set<std::string> covered;

    void fail(const std::string& status, const std::string& detail) {
        if (failure.empty()) {
            failure = status;
            std::cerr << status << ": " << detail << " (cycle " << cycles << ")\n";
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
        if (!in_reset && d.chk_done && (!port.owed.empty() || d.m_req_valid))
            fail("DONE_EARLY", "DONE with a memory request presented or an answer owed");
        else if (!in_reset && !d.chk_busy && (!port.owed.empty() || d.m_req_valid))
            fail("IDLE_ACCESS", "a memory request presented or an answer owed while not busy");
        const bool accept = !in_reset && d.m_req_valid && d.m_req_ready;
        std::uint32_t rdata = std::uint32_t(garbage());
        bool error = false;
        if (accept) rdata = perform(addr, error);
        reg_accepted = d.r_req_valid && d.r_req_ready;
        reg_answered = !in_reset && d.r_rsp_valid;
        reg_rdata = d.r_rsp_rdata;
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
    std::uint32_t reg(bool write, std::uint32_t offset, std::uint32_t data = 0) {
        d.r_req_valid = 1;
        d.r_req_write = write;
        d.r_req_addr = offset;
        d.r_req_wdata = data;
        int guard = 0;
        do { tick(); } while (!reg_accepted && ++guard < 1000);
        d.r_req_valid = 0;
        while (!reg_answered && ++guard < 2000) tick();
        if (guard >= 2000) fail("REG_TIMEOUT", "register access at " + hex(offset));
        return reg_rdata;
    }
    std::uint64_t reg64(std::uint32_t offset) { return reg(false, offset) | (std::uint64_t(reg(false, offset + 4)) << 32); }

    void write_descriptor(const npu::Job& j) {
        const std::pair<std::uint32_t, std::uint32_t> fields[] = {
            {A_BASE, j.a_base}, {B_BASE, j.b_base}, {C_BASE, j.c_base}, {A_STRIDE, j.a_stride},
            {B_STRIDE, j.b_stride}, {C_STRIDE, j.c_stride}, {REG_M, j.m}, {REG_N, j.n}, {REG_K, j.k}};
        for (const auto& [offset, value] : fields) reg(true, offset, value);
    }

    bool terminal(std::uint32_t status) const {
        return status & (p.done_bit | p.error_bit | p.aborted_bit);
    }

    // One job of the given kind; returns false once the run has failed.
    bool run_job(const npu::Job& j, Kind kind, std::mt19937& rng) {
        job = &j;
        const std::uint32_t error = npu::expected_error(p, j);
        job_error = error != 0;
        const npu::Memory before = memory;
        npu::Memory expected = memory;
        if (!error) npu::apply(j, expected);
        cover(j, error, kind);
        if (kind == Kind::BadControl) return bad_control();
        write_descriptor(j);
        const npu::Counters work = npu::v1_counters(j);
        const std::uint64_t estimate = (work.bytes_read + work.bytes_written) * (latency + 2) + 64;
        busy_cycles = reads_accepted = lanes_written = 0;
        reg(true, CONTROL, START);
        const std::uint64_t started = cycles;
        const std::uint64_t abort_at = started + rng() % (estimate + 1);
        const std::uint64_t reset_at = started + rng() % (estimate + 1);
        bool aborted_sent = false, was_reset = false;
        bool busy_during_writes = false;
        if (kind == Kind::BusyWrites) {
            // Descriptor writes and START while busy must be ignored.
            write_descriptor({0x1234u, 0x5678u, 0x9abcu, 1, 1, 4, 3, 3, 3});
            reg(true, CONTROL, START);
            busy_during_writes = d.chk_busy;
        }
        std::uint32_t status = 0;
        const std::uint64_t limit = started + 20 * estimate + 100000;
        while (failure.empty()) {
            const int wait = 1 + int(rng() % 32);
            for (int i = 0; i < wait; ++i) {
                tick();
                if (kind == Kind::Reset && !was_reset && cycles >= reset_at) {
                    reset_while_busy = d.chk_busy;
                    reset(3);
                    was_reset = true;
                    break;
                }
            }
            if (was_reset) break;
            if (kind == Kind::Abort && !aborted_sent && cycles >= abort_at) {
                reg(true, CONTROL, ABORT);
                aborted_sent = true;
            }
            status = reg(false, STATUS);
            if (terminal(status)) break;
            if (cycles > limit) { fail("TIMEOUT", "job did not end"); return false; }
        }
        if (!failure.empty()) return false;
        if (was_reset) return after_reset(j, before, expected);
        if (kind == Kind::AbortAfterEnd) {
            reg(true, CONTROL, ABORT);
            status = reg(false, STATUS);
        }
        const bool aborted = status & p.aborted_bit;
        if (aborted && !(kind == Kind::Abort && aborted_sent))
            fail("STATUS_MISMATCH", "ABORTED without an ABORT during the job");
        if (aborted) covered.insert("abort");
        if (kind == Kind::AbortAfterEnd && !aborted) covered.insert("abort_after_end");
        // STATUS and ERROR_CODE.
        std::uint32_t want = error ? p.error_bit | (p.done_with_error ? p.done_bit : 0)
                           : aborted ? p.aborted_bit | (p.done_with_error ? p.done_bit : 0) : p.done_bit;
        if (kind == Kind::AbortAfterEnd && aborted) fail("STATUS_MISMATCH", "ABORT after the job ended took effect");
        if (status != want) fail("STATUS_MISMATCH", "STATUS " + hex(status) + ", expected " + hex(want));
        const std::uint32_t code = reg(false, ERROR_CODE);
        if (code != error) fail("STATUS_MISMATCH", "ERROR_CODE " + std::to_string(code) + ", expected " + std::to_string(error));
        // Memory.
        if (selftest == 1 && !npu::region_c(j).empty()) memory.at(j.c_base + 1) ^= 0x10;
        if (aborted) check_partial(j, before, expected, 4);
        else compare(expected, "after the job");
        // Counters.
        const std::uint64_t job_cycles = reg64(JOB_CYCLES);
        const std::uint64_t compute = reg64(COMPUTE_CYCLES);
        const std::uint64_t bytes_read = reg(false, BYTES_READ);
        std::uint64_t bytes_written = reg(false, BYTES_WRITTEN);
        const std::uint64_t tiles = reg(false, TILES);
        if (selftest == 3) bytes_written += 1;
        if (job_cycles != busy_cycles)
            fail("COUNTER_MISMATCH", "JOB_CYCLES " + std::to_string(job_cycles) + ", the shell saw " +
                 std::to_string(busy_cycles) + " busy cycles");
        if (bytes_read != reads_accepted || bytes_written != lanes_written)
            fail("COUNTER_MISMATCH", "BYTES_READ/WRITTEN " + std::to_string(bytes_read) + "/" +
                 std::to_string(bytes_written) + ", the shell accepted " + std::to_string(reads_accepted) +
                 " reads and " + std::to_string(lanes_written) + " written bytes");
        const npu::Counters c = error ? npu::Counters{0, 0, 0, 0} : work;
        if (!aborted && (bytes_read != c.bytes_read || bytes_written != c.bytes_written ||
                         compute != c.compute_cycles || tiles != c.tiles))
            fail("COUNTER_MISMATCH", "read/written/compute/tiles " + std::to_string(bytes_read) + "/" +
                 std::to_string(bytes_written) + "/" + std::to_string(compute) + "/" + std::to_string(tiles) +
                 ", expected " + std::to_string(c.bytes_read) + "/" + std::to_string(c.bytes_written) + "/" +
                 std::to_string(c.compute_cycles) + "/" + std::to_string(c.tiles));
        if (aborted && (bytes_written > c.bytes_written || tiles > c.tiles || compute > c.compute_cycles))
            fail("COUNTER_MISMATCH", "an aborted job counted more than the whole job");
        if (kind == Kind::BusyWrites) {
            if (reg(false, A_BASE) != j.a_base || reg(false, REG_K) != j.k)
                fail("STATUS_MISMATCH", "a descriptor write while busy took effect");
            else if (busy_during_writes) covered.insert("busy_writes");
        }
        // ACK clears STATUS.
        reg(true, CONTROL, ACK);
        if (const std::uint32_t after = reg(false, STATUS); after != 0)
            fail("STATUS_MISMATCH", "STATUS " + hex(after) + " after ACK");
        job = nullptr;
        last_job_cycles = job_cycles;
        last_compute = compute;
        return failure.empty();
    }

    std::uint64_t last_job_cycles = 0, last_compute = 0;

private:
    std::uint32_t perform(std::uint32_t addr, bool& error) {
        if (!memory.contains(addr, 4)) {
            fail("OUT_OF_WINDOW", "access at " + hex(addr));
            error = true;
            return 0;
        }
        if (job && job_error) fail("ACCESS_ON_ERROR", "access at " + hex(addr) + " in a job that must end in an error");
        if (d.m_req_we) {
            for (int lane = 0; lane < 4; ++lane) {
                if (!((d.m_req_be >> lane) & 1u)) continue;
                if (!job || !npu::in_c(*job, addr + lane))
                    fail("STRAY_WRITE", "write to " + hex(addr + lane) + ", not a byte of C");
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

    // After an abort (grain 4: each C element) or a reset (grain 1: each C
    // byte): outside C unchanged; each grain of C unchanged or final.
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
                    bool old = true, fresh = true;
                    for (int b = 0; b < grain; ++b) {
                        old = old && memory.at(at + b) == before.at(at + b);
                        fresh = fresh && memory.at(at + b) == expected.at(at + b);
                    }
                    if (!old && !fresh) {
                        fail("MEMORY_MISMATCH", "C(" + std::to_string(row) + "," + std::to_string(col) +
                             ") at " + hex(at) + " neither unchanged nor final");
                        return;
                    }
                }
    }

    bool after_reset(const npu::Job& j, const npu::Memory& before, const npu::Memory& expected) {
        if (reset_while_busy) covered.insert("reset");
        if (const std::uint32_t status = reg(false, STATUS); status != 0)
            fail("STATUS_MISMATCH", "STATUS " + hex(status) + " after reset");
        if (reg(false, A_BASE) != 0 || reg64(JOB_CYCLES) != 0 || reg(false, BYTES_WRITTEN) != 0)
            fail("STATUS_MISMATCH", "registers not cleared by reset");
        check_partial(j, before, expected, 1);
        job = nullptr;
        return failure.empty();
    }

    bool bad_control() {
        // v1: a CONTROL write of an unknown command is error 7 (phase9.md).
        reg(true, CONTROL, 3);
        const std::uint32_t status = reg(false, STATUS), code = reg(false, ERROR_CODE);
        if (!(status & p.error_bit) || code != 7)
            fail("STATUS_MISMATCH", "malformed CONTROL: STATUS " + hex(status) + ", ERROR_CODE " + std::to_string(code));
        reg(true, CONTROL, ACK);
        if (reg(false, STATUS) != 0) fail("STATUS_MISMATCH", "STATUS after ACK of error 7");
        covered.insert("bad_control");
        job = nullptr;
        return failure.empty();
    }

    void cover(const npu::Job& j, std::uint32_t error, Kind kind) {
        if (kind == Kind::BadControl) return;
        if (error) { covered.insert("error" + std::to_string(error)); return; }
        const std::uint64_t top = std::uint64_t(p.mem_base) + p.mem_bytes;
        if (npu::region_a(j).hi == top && !npu::region_a(j).empty()) covered.insert("a_at_top");
        if (npu::region_b(j).hi == top && !npu::region_b(j).empty()) covered.insert("b_at_top");
        if (npu::region_c(j).hi == top && !npu::region_c(j).empty()) covered.insert("c_at_top");
        for (const npu::Region& r : {npu::region_a(j), npu::region_b(j), npu::region_c(j)})
            if (!r.empty() && r.lo == p.mem_base) covered.insert("at_base");
        if (j.m == p.max_dim || j.n == p.max_dim || j.k == p.max_dim) covered.insert("dim_max");
        const std::pair<const char*, std::uint32_t> dims[] = {{"m", j.m}, {"n", j.n}, {"k", j.k}};
        for (const auto& [name, value] : dims) {
            if (value) covered.insert(std::string(name) + "%4=" + std::to_string(value % 4));
            if (value <= 1) covered.insert(std::string(name) + "=" + std::to_string(value));
        }
        if (j.m && j.k && j.a_base % 4) covered.insert("a_unaligned");
        if (j.k && j.n && j.b_base % 4) covered.insert("b_unaligned");
        if (j.m && j.n && j.c_base % 4) covered.insert("c_unaligned");
        if (j.m > 1 && j.k && j.a_stride == j.k) covered.insert("a_stride_min");
        if (j.k > 1 && j.n && j.b_stride == j.n) covered.insert("b_stride_min");
        if (j.m > 1 && j.n && j.c_stride == 4 * j.n) covered.insert("c_stride_min");
    }

    Vnpu_shell& d;
    npu::Profile p;
    npu::Memory memory;
    shell::Port port;
    shell::StableCheck stable;
    std::mt19937 stall_rng, garbage;
    bool stall = false, long_stall = false;
    int latency = 2, selftest = 0;
    std::size_t max_inflight = 2;
    bool error_cycle = false, error_next = false;
    bool reg_accepted = false, reg_answered = false;
    std::uint32_t reg_rdata = 0;
    std::uint64_t busy_cycles = 0, reads_accepted = 0, lanes_written = 0;
    bool reset_while_busy = false;
    const npu::Job* job = nullptr;
    bool job_error = false;
};

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const npu::Profile profile = npu::v1_profile();
    const unsigned seed = plusarg("seed").empty() ? 1u : unsigned(std::stoul(plusarg("seed")));
    Vnpu_shell dut;
    Shell shell(dut, profile, seed * 7919u + 13u);
    shell.reset(8);

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
        std::printf("NPU %s profile=%s case=%ux%ux%u job_cycles=%llu compute_cycles=%llu macs=%.0f "
                    "utilization=%.4f%%\n", shell.failure.empty() ? "PASS" : shell.failure.c_str(),
                    profile.name.c_str(), j.m, j.n, j.k, (unsigned long long)shell.last_job_cycles,
                    (unsigned long long)shell.last_compute, macs,
                    shell.last_job_cycles ? 100.0 * macs / (16.0 * double(shell.last_job_cycles)) : 0.0);
        return shell.failure.empty() ? 0 : 1;
    }

    const int jobs = plusarg("jobs").empty() ? 1000 : std::stoi(plusarg("jobs"));
    npu::Generator gen(profile, seed);
    std::mt19937& rng = gen.random();
    int done = 0;
    for (; done < jobs && shell.failure.empty(); ++done) {
        const unsigned roll = rng() % 100;
        Kind kind = roll < 66 ? Kind::Normal : roll < 80 ? Kind::Error : roll < 86 ? Kind::Abort
                  : roll < 89 ? Kind::AbortAfterEnd : roll < 92 ? Kind::Reset : roll < 97 ? Kind::BusyWrites
                  : Kind::BadControl;
        npu::Job j = kind == Kind::Error ? gen.error(1 + int(rng() % 6)) : gen.valid();
        if (kind == Kind::BusyWrites) {
            // Only a job long enough that the writes land while it is busy.
            const npu::Counters c = npu::v1_counters(j);
            if (c.bytes_read + c.bytes_written < 400) kind = Kind::Normal;
        }
        shell.run_job(j, kind, rng);
    }
    const std::vector<std::string> wanted = npu::bins(profile);
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
