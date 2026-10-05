// The NPU shell's independent reference (docs/npu.md §2, §6; docs/phase9.md for
// v1): what a job must leave in memory, which descriptors are errors, what the
// counters must read — written from the documents, not from the RTL — and the
// seeded random job generator with its coverage bins. tb_npu.cpp drives the
// DUT and compares.
//
// A profile is one NPU ABI's rules. v1 (ABI 1, docs/phase9.md "Matrix layout
// and bounds", "Control and memory interface"): the shared-RAM window
// 0x1000_0000-0x1000_8000; M, N, K at most 1024; A_STRIDE >= K, B_STRIDE >= N,
// C_STRIDE >= 4N; C at any byte offset, written a byte at a time; overlapping
// A, B and C rejected. v1's error code numbers are documented only in its RTL
// (aster_npu_engine.sv, "stable software-visible ABI values"): 1 a dimension
// above 1024, 2 a stride below its minimum, 3/4/5 A/B/C outside the window,
// 6 two regions overlapping; 7 a malformed CONTROL write (the register
// boundary). v1's counters (phase9.md): BYTES_READ counts each operand byte
// loaded, one per load, the 4x4 tile traversal rereading A for every column
// tile and B for every row tile; BYTES_WRITTEN one per C byte; COMPUTE_CYCLES
// one per K step of each tile; TILES the output tiles.
//
// v2 (ABI 2, docs/npu.md §2-§4): the SoC's main memory (96 KiB at
// 0x8000_0000 here); M, N, K at most 4096; C_BASE and C_STRIDE multiples of 4,
// C_STRIDE >= 4N; A's and B's rows may overlap each other and A and B may
// overlap; C may not overlap A or B; error codes as §3.2 (the lowest that
// applies, regions without bytes not checked). Its counters count whole words;
// the words a job reads follow from §4.2's mapping (v2_words_read).
#ifndef ASTER_NPU_MODEL_H
#define ASTER_NPU_MODEL_H

#include <algorithm>
#include <cstdint>
#include <deque>
#include <map>
#include <random>
#include <string>
#include <vector>

namespace npu {

struct Job {
    std::uint32_t a_base = 0, b_base = 0, c_base = 0;
    std::uint32_t a_stride = 0, b_stride = 0, c_stride = 0;
    std::uint32_t m = 0, n = 0, k = 0;
    std::uint32_t mode = 0;              // ABI 2: 0 automatic, 1 tiles, 2 K-split (N = 1)
    // ABI 2 (19.3): A's second level, npu.md §4.5 — 0 turns a level off.
    std::uint32_t a_m0 = 0, a_stride_m1 = 0, a_k0 = 0, a_stride_k1 = 0;
};

// A(i,k)'s offset from A_BASE (npu.md §2, §4.5), in 64-bit arithmetic.
inline std::uint64_t a_row_offset(const Job& j, std::uint64_t i) {
    return j.a_m0 ? (i / j.a_m0) * std::uint64_t(j.a_stride_m1) + (i % j.a_m0) * std::uint64_t(j.a_stride)
                  : i * std::uint64_t(j.a_stride);
}
inline std::uint64_t a_k_offset(const Job& j, std::uint64_t k) {
    return j.a_k0 ? (k / j.a_k0) * std::uint64_t(j.a_stride_k1) + k % j.a_k0 : k;
}

struct Profile {
    std::string name;
    int abi;
    std::uint32_t mem_base, mem_bytes;   // the NPU's memory window
    std::uint32_t max_dim;
    bool c_any_alignment;                // v1: C at any byte offset
    bool rows_may_overlap;               // A_STRIDE < K, B_STRIDE < N allowed
    bool operands_may_overlap;           // A and B may overlap each other
    // STATUS bits
    std::uint32_t busy_bit, done_bit, error_bit, aborted_bit;
    bool done_with_error;                // v1 sets done with error and with abort
    std::size_t max_in_flight;           // requests the DUT may have unanswered (v1: one at a time)
};

inline Profile v1_profile() {
    return {"v1", 1, 0x10000000u, 0x8000u, 1024, true, false, false, 1u, 2u, 4u, 8u, true, 1};
}
inline Profile v2_profile() {
    return {"v2", 2, 0x80000000u, 0x18000u, 4096, false, true, true, 1u, 2u, 4u, 8u, false, 2};
}

// A region's bytes as a half-open interval [lo, hi) in 64-bit arithmetic; empty
// when it has no elements.
struct Region { std::uint64_t lo = 0, hi = 0; bool empty() const { return hi <= lo; } };

// A's region: from A_BASE to one past the bound of its highest byte — the
// rows' largest offset bounded by its two terms' largest values, the same for
// k (npu.md §9, 19.3); exact in the plain form.
inline Region region_a(const Job& j) {
    if (!j.m || !j.k) return {};
    const std::uint64_t m1 = j.m - 1, k1 = j.k - 1;
    const std::uint64_t rows = j.a_m0 ? (m1 / j.a_m0) * std::uint64_t(j.a_stride_m1)
                                        + std::min<std::uint64_t>(m1, j.a_m0 - 1) * j.a_stride
                                      : m1 * std::uint64_t(j.a_stride);
    const std::uint64_t ks = j.a_k0 ? (k1 / j.a_k0) * std::uint64_t(j.a_stride_k1) + std::min<std::uint64_t>(k1, j.a_k0 - 1)
                                    : k1;
    return {j.a_base, std::uint64_t(j.a_base) + rows + ks + 1};
}
inline Region region_b(const Job& j) {
    if (!j.k || !j.n) return {};
    return {j.b_base, std::uint64_t(j.b_base) + std::uint64_t(j.k - 1) * j.b_stride + j.n};
}
inline Region region_c(const Job& j) {
    if (!j.m || !j.n) return {};
    return {j.c_base, std::uint64_t(j.c_base) + std::uint64_t(j.m - 1) * j.c_stride + 4ull * j.n};
}
inline bool overlap(const Region& x, const Region& y) {
    return !x.empty() && !y.empty() && x.lo < y.hi && y.lo < x.hi;
}

// The error a descriptor must end with (0: none).
inline std::uint32_t expected_error(const Profile& p, const Job& j) {
    const std::uint64_t lo = p.mem_base, hi = std::uint64_t(p.mem_base) + p.mem_bytes;
    const Region a = region_a(j), b = region_b(j), c = region_c(j);
    auto outside = [&](const Region& r) { return !r.empty() && (r.lo < lo || r.hi > hi); };
    if (p.name == "v1") {
        if (j.m > p.max_dim || j.n > p.max_dim || j.k > p.max_dim) return 1;
        if (j.a_stride < j.k || j.b_stride < j.n || std::uint64_t(j.c_stride) < 4ull * j.n) return 2;
        if (outside(a)) return 3;
        if (outside(b)) return 4;
        if (outside(c)) return 5;
        if (overlap(a, b) || overlap(a, c) || overlap(b, c)) return 6;
        return 0;
    }
    // ABI 2, npu.md §3.2.
    if (j.m > p.max_dim || j.n > p.max_dim || j.k > p.max_dim || j.mode == 3 || (j.mode == 2 && j.n != 1)) return 1;
    if (j.c_base % 4 || j.c_stride % 4) return 2;
    if (std::uint64_t(j.c_stride) < 4ull * j.n) return 3;
    if (outside(a) || outside(b) || outside(c)) return 4;
    if (overlap(c, a) || overlap(c, b)) return 5;
    return 0;
}

// ABI 2: the words row i of A takes — one segment of K bytes, or with
// A_K0 a segment of up to A_K0 bytes per kernel row (npu.md §4.5) — each as
// the aligned words that cover it.
inline std::uint64_t v2_a_row_words(const Job& j, std::uint64_t i) {
    const std::uint64_t row = j.a_base + a_row_offset(j, i);
    if (!j.a_k0 || j.a_k0 >= j.k) return ((row % 4) + j.k + 3) / 4;
    std::uint64_t total = 0;
    for (std::uint64_t k0 = 0; k0 < j.k; k0 += j.a_k0) {
        const std::uint64_t addr = row + (k0 / j.a_k0) * j.a_stride_k1, len = std::min<std::uint64_t>(j.a_k0, j.k - k0);
        total += ((addr % 4) + len + 3) / 4;
    }
    return total;
}

// ABI 2: does the job run the K-split mapping (npu.md §3 MODE, §4.3)?
inline bool v2_ksplit(const Job& j) { return j.mode == 2 || (j.mode == 0 && j.n == 1); }

// ABI 2: the words a completed job reads. Tiles (npu.md §4.2): B's panels —
// as many columns as fit 16 KiB, floor(4096 / K) groups of four — each read
// once, row by row; for each panel, A read once, strip by strip; each row
// segment as the aligned words that cover it. K-split (§4.3): B, the vector,
// once — the words covering its K bytes when B_STRIDE is 1, else a word per
// byte — then A once.
inline std::uint64_t v2_words_read(const Job& j) {
    if (!j.m || !j.n || !j.k || j.k > 4096) return 0;     // (K above the limit: a descriptor error)
    auto words = [](std::uint64_t addr, std::uint64_t len) { return ((addr % 4) + len + 3) / 4; };
    if (v2_ksplit(j)) {
        std::uint64_t total = j.b_stride == 1 ? words(j.b_base, j.k) : j.k;
        for (std::uint64_t i = 0; i < j.m; ++i) total += v2_a_row_words(j, i);
        return total;
    }
    const std::uint64_t groups = (j.n + 3) / 4, fit = 4096 / j.k;
    const std::uint64_t panel = std::min(groups, fit);
    std::uint64_t total = 0;
    for (std::uint64_t g0 = 0; g0 < groups; g0 += panel) {
        const std::uint64_t gp = std::min(panel, groups - g0);
        const std::uint64_t cols = std::min<std::uint64_t>(4 * gp, j.n - 4 * g0);
        for (std::uint64_t k = 0; k < j.k; ++k) total += words(j.b_base + k * j.b_stride + 4 * g0, cols);
        for (std::uint64_t i = 0; i < j.m; ++i) total += v2_a_row_words(j, i);
    }
    return total;
}

// Memory as the shell holds it: the window's bytes.
struct Memory {
    std::uint32_t base;
    std::vector<std::uint8_t> bytes;
    Memory(std::uint32_t base_, std::uint32_t size) : base(base_), bytes(size, 0) {}
    bool contains(std::uint64_t addr, std::uint64_t len = 1) const {
        return addr >= base && addr + len <= std::uint64_t(base) + bytes.size();
    }
    std::uint8_t& at(std::uint32_t addr) { return bytes[addr - base]; }
    std::uint8_t at(std::uint32_t addr) const { return bytes[addr - base]; }
};

// The memory a completed job must leave: C(i,j) = sum over k of
// sext(A(i,k)) * sext(B(k,j)) modulo 2^32, little-endian, at
// C_BASE + i*C_STRIDE + 4j; nothing else changes.
inline void apply(const Job& j, Memory& memory) {
    for (std::uint32_t i = 0; i < j.m; ++i)
        for (std::uint32_t col = 0; col < j.n; ++col) {
            std::uint32_t sum = 0;
            for (std::uint32_t k = 0; k < j.k; ++k) {
                const auto a = std::int8_t(memory.at(std::uint32_t(j.a_base + a_row_offset(j, i) + a_k_offset(j, k))));
                const auto b = std::int8_t(memory.at(j.b_base + k * j.b_stride + col));
                sum += std::uint32_t(std::int32_t(a) * std::int32_t(b));
            }
            const std::uint32_t at = j.c_base + i * j.c_stride + 4 * col;
            for (int byte = 0; byte < 4; ++byte) memory.at(at + byte) = std::uint8_t(sum >> (8 * byte));
        }
}

// Is this byte one of C's (a byte the job may write)?
inline bool in_c(const Job& j, std::uint32_t addr) {
    const Region c = region_c(j);
    if (c.empty() || addr < c.lo || addr >= c.hi) return false;
    if (j.m == 1) return true;           // one row: the region is C's bytes
    return (addr - j.c_base) % j.c_stride < 4 * j.n;
}

// ABI 2: a completed job's JOB_CYCLES on a memory that answers on time — every
// request accepted when offered, answered `latency` cycles later (1 or 2) —
// from the schedule npu.md §4 describes, as 19.1 builds it (phase19.md,
// "Milestone 19.1"; the CPI model's role for the core). Cycle 0 is the first
// busy cycle (the one after START's acceptance):
// - CHECK: cycles 0-15; a descriptor error, or M or N = 0, ends the job at
//   cycle 16 (17 cycles);
// - PANEL and STRIP: one cycle each;
// - LOAD: one word requested a cycle, as long as fewer than two requests stay
//   unanswered past the cycle; the next phase starts three cycles after the
//   last word's answer (the answer registered, written to its buffer, then
//   the end seen);
// - TILES: each tile's max(K, 1) steps (K-split: one tile a strip, of
//   max(ceil(K/4), 1) steps, writing one element a row) on consecutive cycles, a tile starting
//   right after the previous one unless its output bank (the one two tiles
//   back used) is still held, which it is until the cycle after the writer
//   took that tile's last element; a tile's results are in its bank three
//   cycles after its last step;
// - the writer takes a bank's elements in order, row by row, one a cycle, each
//   offered the cycle after it is taken and taken again (the next) in the cycle
//   the previous is accepted, under the same two-unanswered rule;
// - DRAIN: one cycle after the last step's results are in the bank, the
//   writer's last element accepted and its bank released; then the next
//   strip, panel, or FINISH;
// - FINISH: the job ends in the first cycle with no answer still owed.
inline std::uint64_t v2_job_cycles(const Profile& p, const Job& j, int latency) {
    if (expected_error(p, j) || !j.m || !j.n) return 17;
    std::deque<std::uint64_t> owed;                  // answer cycles of the requests in flight
    std::uint64_t last_answer = 0;
    auto issue = [&](std::uint64_t c) {              // the first cycle from c a request is accepted
        for (;; ++c) {
            while (!owed.empty() && owed.front() < c) owed.pop_front();
            std::size_t remaining = 0;
            for (const std::uint64_t t : owed) remaining += t > c;
            if (remaining < 2) {
                owed.push_back(c + latency);
                last_answer = c + latency;
                return c;
            }
        }
    };
    auto load = [&](std::uint64_t c, std::uint64_t words) {   // returns the next phase's first cycle
        std::uint64_t at = c, last = 0;
        for (std::uint64_t w = 0; w < words; ++w) { last = issue(at); at = last + 1; }
        return last + latency + 3;
    };
    auto words = [](std::uint64_t addr, std::uint64_t len) { return ((addr % 4) + len + 3) / 4; };
    const bool ksplit = v2_ksplit(j);
    const std::uint64_t groups = (j.n + 3) / 4;
    const std::uint64_t panel = j.k && !ksplit ? std::min<std::uint64_t>(groups, 4096 / j.k) : groups;
    const std::uint64_t steps = std::max<std::uint64_t>(ksplit ? (j.k + 3) / 4 : j.k, 1);
    std::vector<std::uint64_t> release;              // per tile: the cycle the writer took its last element
    bool wrote = false;
    std::uint64_t last_accept = 0;
    std::uint64_t c = 16;
    for (std::uint64_t g0 = 0; g0 < groups; g0 += panel) {
        const std::uint64_t gp = std::min(panel, groups - g0);
        const std::uint64_t cols = std::min<std::uint64_t>(4 * gp, j.n - 4 * g0);
        c += 1;                                                       // PANEL
        if (j.k) {
            std::uint64_t total = 0;
            if (ksplit) total = j.b_stride == 1 ? words(j.b_base, j.k) : j.k;
            else for (std::uint64_t k = 0; k < j.k; ++k) total += words(j.b_base + k * j.b_stride + 4 * g0, cols);
            c = load(c, total);
        }
        for (std::uint64_t i0 = 0; i0 < j.m; i0 += 4) {
            const std::uint64_t rv = std::min<std::uint64_t>(4, j.m - i0);
            c += 1;                                                   // STRIP
            if (j.k) {
                std::uint64_t total = 0;
                for (std::uint64_t r = 0; r < rv; ++r) total += v2_a_row_words(j, i0 + r);
                c = load(c, total);
            }
            std::uint64_t next = c, last_step = 0;
            for (std::uint64_t t = 0; t < gp; ++t) {
                const std::uint64_t cv = ksplit ? 1 : std::min<std::uint64_t>(4, cols - 4 * t);
                std::uint64_t start = next;
                if (release.size() >= 2) start = std::max(start, release[release.size() - 2] + 1);
                next = start + steps;
                last_step = next - 1;
                const std::uint64_t full = start + steps + 3;         // the results in the bank
                std::uint64_t taken = 0;
                for (std::uint64_t e = 0; e < rv * cv; ++e) {
                    taken = wrote ? std::max(full, last_accept) : full;
                    last_accept = issue(taken + 1);
                    wrote = true;
                }
                release.push_back(taken);
            }
            c = std::max({next, last_step + 4, last_accept + 1, release.back() + 1}) + 1;   // DRAIN
        }
    }
    return std::max(c, last_answer + 1) + 1;                          // FINISH
}

// v1's counters for a completed job (phase9.md).
struct Counters { std::uint64_t bytes_read, bytes_written, compute_cycles, tiles; };
inline Counters v1_counters(const Job& j) {
    if (!j.m || !j.n) return {0, 0, 0, 0};
    const std::uint64_t row_tiles = (j.m + 3) / 4, col_tiles = (j.n + 3) / 4;
    const std::uint64_t tiles = row_tiles * col_tiles;
    return {std::uint64_t(j.k) * (std::uint64_t(j.m) * col_tiles + std::uint64_t(j.n) * row_tiles),
            4ull * j.m * j.n, std::uint64_t(j.k) * tiles, tiles};
}

// The coverage bins a random run must hit (+require_coverage).
inline std::vector<std::string> bins(const Profile& p) {
    std::vector<std::string> names;
    for (const char* d : {"m", "n", "k"})
        for (int r = 0; r < 4; ++r) names.push_back(std::string(d) + "%4=" + std::to_string(r));
    for (const char* name : {"m=0", "n=0", "k=0", "m=1", "n=1", "k=1", "a_unaligned", "b_unaligned",
                             "a_stride_min", "b_stride_min", "c_stride_min", "a_at_top", "b_at_top",
                             "c_at_top", "at_base", "dim_max", "abort", "abort_after_end",
                             "reset", "busy_writes", "bad_control"})
        names.push_back(name);
    if (p.c_any_alignment) names.push_back("c_unaligned");
    for (int code = 1; code <= (p.abi == 1 ? 6 : 5); ++code) names.push_back("error" + std::to_string(code));
    if (p.abi == 2) {
        // bad_control is v1's malformed command; ABI 2's is a sub-word access.
        names.erase(std::find(names.begin(), names.end(), "bad_control"));
        for (const char* name : {"bad_access", "bus_error_read", "bus_error_write", "clear_totals", "a_rows_overlap",
                                 "b_rows_overlap", "mode0", "mode1", "mode2", "multiple_panels", "register_map",
                                 "extent_past_2^32", "ksplit_auto", "ksplit_mode2", "ksplit_b_stride1",
                                 "ksplit_b_gathered", "ksplit_k%4=0", "ksplit_k%4=1", "ksplit_k%4=2", "ksplit_k%4=3",
                                 "tiles_n1", "abort_check", "abort_load", "abort_tiles", "abort_between",
                                 "reset_load", "reset_tiles", "abort_error_descriptor", "a_two_level_m",
                                 "a_two_level_k", "a_conv", "a_k0_partial", "a_m0_wraps_strip"})
            names.push_back(name);
    }
    return names;
}

// A random job for the profile, of the kind `want` names ("" any valid job,
// or "error1".."error6"), with its regions placed in the window and each
// valid job's regions disjoint. Sizes stay small, so a run covers many jobs.
class Generator {
public:
    Generator(const Profile& profile, unsigned seed) : p(profile), rng(seed) {}

    std::uint32_t pick_dim() {
        switch (rng() % 8) {
            case 0: return rng() % 2;                 // 0 or 1
            case 1: return 2 + rng() % 3;             // 2-4
            case 2: case 3: return 1 + rng() % 9;     // 1-9
            case 4: case 5: return 4 + rng() % 13;    // 4-16
            case 6: return 12 + rng() % 13;           // 12-24
            default: return 1 + rng() % 40;
        }
    }

    Job valid() {
        for (int attempt = 0; attempt < 1000; ++attempt) {
            Job j;
            j.m = pick_dim(); j.n = pick_dim(); j.k = pick_dim();
            if (rng() % 16 == 0) j.k = 40 + rng() % 120;    // a longer reduction now and then
            if (rng() % 32 == 0) {                          // one dimension at the limit, the others 1
                j.m = j.n = j.k = 1;
                (rng() % 3 == 0 ? j.m : rng() % 2 ? j.n : j.k) = p.max_dim;
            }
            j.a_stride = j.k + (rng() % 3 ? 0 : rng() % 9);
            j.b_stride = j.n + (rng() % 3 ? 0 : rng() % 9);
            j.c_stride = 4 * j.n + (rng() % 3 ? 0 : 4 * (rng() % 3) + (p.c_any_alignment ? rng() % 4 : 0));
            if (p.rows_may_overlap && rng() % 8 == 0) j.a_stride = j.k ? rng() % j.k : 0;
            if (p.rows_may_overlap && rng() % 8 == 0) j.b_stride = j.n ? rng() % j.n : 0;
            if (p.abi == 2 && rng() % 6 == 0) j.n = 1;              // N = 1: K-split (or tiles under MODE 1)
            if (p.abi == 2 && j.n == 1 && rng() % 2) j.b_stride = 1;
            if (p.abi == 2) j.mode = j.n == 1 && rng() % 3 == 0 ? 2 : rng() % 2;
            if (p.abi == 2 && rng() % 6 == 0) {        // A in two levels (19.3)
                if (rng() % 2) { j.a_m0 = 1 + rng() % std::max(1u, j.m); j.a_stride_m1 = rng() % 3 ? j.a_stride * j.a_m0 + rng() % 9 : rng() % 64; }
                if (rng() % 2) { j.a_k0 = 1 + rng() % std::max(1u, j.k); j.a_stride_k1 = rng() % 3 ? j.a_k0 + rng() % 9 : rng() % 40; }
            }
            if (p.abi == 2 && rng() % 10 == 0) {       // a direct convolution: HxW input with C channels (HWC or CHW)
                const std::uint32_t c = 1 + rng() % 4, kh = 1 + rng() % 3, kw = 1 + rng() % 3;
                const std::uint32_t oh = 1 + rng() % 6, ow = 1 + rng() % 6, w = ow + kw - 1 + rng() % 2;
                j.m = oh * ow; j.k = kh * kw * c; j.n = 1 + rng() % 6;
                if (rng() % 2) {                       // HWC: a kernel row is kw x C contiguous bytes
                    j.a_stride = c; j.a_m0 = ow; j.a_stride_m1 = w * c; j.a_k0 = kw * c; j.a_stride_k1 = w * c;
                } else {                               // one channel (C = 1 makes HWC and CHW alike)
                    j.k = kh * kw; j.a_stride = 1; j.a_m0 = ow; j.a_stride_m1 = w; j.a_k0 = kw; j.a_stride_k1 = w;
                }
                j.b_stride = j.n + (rng() % 3 ? 0 : rng() % 5);
                j.c_stride = 4 * j.n;
                j.mode = j.n == 1 && rng() % 3 == 0 ? 2 : 0;
            }
            if (p.abi == 2 && rng() % 16 == 0) {       // more than one B panel: K x N beyond 16 KiB, kept small
                j.k = 600 + rng() % 400;
                j.n = 5 + rng() % 20;
                j.m = 1 + rng() % 8;
                j.a_stride = j.k + rng() % 4;
                j.b_stride = j.n + rng() % 4;
                j.c_stride = 4 * j.n;
                j.a_m0 = j.a_stride_m1 = j.a_k0 = j.a_stride_k1 = 0;
            }
            if (!place(j)) continue;
            return j;
        }
        return Job{};   // M = N = K = 0 at the window's base: a valid no-op
    }

    Job error(int code) {
        Job j = valid();
        if (!j.m) j.m = 1;
        if (!j.n) j.n = 1;
        if (!j.k) j.k = 1;
        const std::uint32_t lo = p.mem_base, hi = p.mem_base + p.mem_bytes;
        if (p.abi == 2) return error_v2(j, code % 5 + 1, lo, hi);
        switch (code) {
            case 1: (rng() % 3 == 0 ? j.m : rng() % 2 ? j.n : j.k) = p.max_dim + 1 + rng() % 4; break;
            case 2: switch (rng() % 3) {
                        case 0: j.a_stride = j.k - 1; break;
                        case 1: j.b_stride = j.n - 1; break;
                        default: j.c_stride = 4 * j.n - 1 - rng() % 3; break;
                    } break;
            case 3: j.a_base = rng() % 2 ? lo - 1 - rng() % 64 : hi - std::uint32_t(region_a(j).hi - region_a(j).lo) + 1; break;
            case 4: j.b_base = rng() % 2 ? lo - 1 - rng() % 64 : hi - std::uint32_t(region_b(j).hi - region_b(j).lo) + 1; break;
            case 5: j.c_base = rng() % 2 ? lo - 4 - rng() % 64 : hi - std::uint32_t(region_c(j).hi - region_c(j).lo) + 1; break;
            case 6: switch (rng() % 3) {
                        case 0: j.b_base = j.a_base + std::uint32_t(rng() % (region_a(j).hi - region_a(j).lo)); break;
                        case 1: j.c_base = j.a_base + std::uint32_t(rng() % (region_a(j).hi - region_a(j).lo)); break;
                        default: j.c_base = j.b_base + std::uint32_t(rng() % (region_b(j).hi - region_b(j).lo)); break;
                    } break;
        }
        return j;
    }

    std::mt19937& random() { return rng; }

    // An ABI 2 descriptor error of the given code (npu.md §3.2); the job may
    // still end with a lower code that also applies.
    Job error_v2(Job j, int code, std::uint32_t lo, std::uint32_t hi) {
        j.a_stride = std::max(j.a_stride, 1u);
        switch (code) {
            case 1: switch (rng() % 4) {
                        case 0: j.m = p.max_dim + 1 + rng() % 4; break;
                        case 1: j.k = p.max_dim + 1 + rng() % 4; break;
                        case 2: j.mode = 3; break;
                        default: j.mode = 2; j.n = 2 + rng() % 5; j.c_stride = std::max(j.c_stride, 4 * j.n); break;
                    } break;
            case 2: if (rng() % 2) j.c_base += 1 + rng() % 3; else j.c_stride += 1 + rng() % 3; break;
            case 3: j.c_stride = 4 * (j.n - 1 - (j.n > 1 ? rng() % (j.n - 1) : 0)); break;
            case 4: if (rng() % 4 == 0) {                 // an extent past 2^32 (a stride near 2^32)
                        j.m = std::max(j.m, 2u); j.k = std::max(j.k, 2u);
                        switch (rng() % 3) {
                            case 0: j.a_stride = 0xFFFFFFFFu - rng() % 64; break;
                            case 1: j.b_stride = 0xFFFFFFFFu - rng() % 64; break;
                            default: j.c_stride = 0xFFFFFFFCu - 4 * (rng() % 16); break;
                        }
                        break;
                    }
                    switch (rng() % 3) {
                        case 0: j.a_base = rng() % 2 ? lo - 1 - rng() % 64 : hi - std::uint32_t(region_a(j).hi - region_a(j).lo) + 1; break;
                        case 1: j.b_base = rng() % 2 ? lo - 1 - rng() % 64 : hi - std::uint32_t(region_b(j).hi - region_b(j).lo) + 1; break;
                        default: j.c_base = rng() % 2 ? lo - 4 - 4 * (rng() % 16) : (hi - std::uint32_t(region_c(j).hi - region_c(j).lo) + 4) & ~3u; break;
                    } break;
            default: if (rng() % 2) j.c_base = (j.a_base + std::uint32_t(rng() % (region_a(j).hi - region_a(j).lo))) & ~3u;
                     else j.c_base = (j.b_base + std::uint32_t(rng() % (region_b(j).hi - region_b(j).lo))) & ~3u;
                     break;
        }
        return j;
    }

private:
    // Place A, B and C at random disjoint offsets in the window; one job in
    // four pins one region to the window's base or to its top (its last byte
    // the window's last).
    bool place(Job& j) {
        Job sizes = j;                       // the regions' sizes: the job at base 0 (two levels included)
        sizes.a_base = sizes.b_base = sizes.c_base = 0;
        const std::uint64_t sa = region_a(sizes).hi, sb = region_b(sizes).hi, sc = region_c(sizes).hi;
        if (sa + sb + sc + 16 > p.mem_bytes) return false;
        const unsigned pin = rng() % 4 == 0 ? 1 + rng() % 6 : 0;   // 1-3 base, 4-6 top (A, B, C)
        for (int attempt = 0; attempt < 64; ++attempt) {
            j.a_base = p.mem_base + std::uint32_t(rng() % (p.mem_bytes - sa + 1));
            j.b_base = p.mem_base + std::uint32_t(rng() % (p.mem_bytes - sb + 1));
            std::uint32_t c = std::uint32_t(rng() % (p.mem_bytes - sc + 1));
            if (!p.c_any_alignment || rng() % 2) c &= ~3u;   // (the top pin below keeps C aligned: sc is)
            j.c_base = p.mem_base + c;
            if (rng() % 3 == 0) j.a_base &= ~3u;
            if (rng() % 3 == 0) j.b_base &= ~3u;
            const std::uint32_t top = p.mem_base + p.mem_bytes;
            switch (pin) {
                case 1: j.a_base = p.mem_base; break;
                case 2: j.b_base = p.mem_base; break;
                case 3: j.c_base = p.mem_base; break;
                case 4: j.a_base = top - std::uint32_t(sa); break;
                case 5: j.b_base = top - std::uint32_t(sb); break;
                case 6: j.c_base = top - std::uint32_t(sc); break;
            }
            if ((p.operands_may_overlap || !overlap(region_a(j), region_b(j))) && !overlap(region_a(j), region_c(j))
                && !overlap(region_b(j), region_c(j)) && expected_error(p, j) == 0)
                return true;
        }
        return false;
    }

    Profile p;
    std::mt19937 rng;
};

}  // namespace npu

#endif
