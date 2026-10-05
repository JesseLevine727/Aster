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
#ifndef ASTER_NPU_MODEL_H
#define ASTER_NPU_MODEL_H

#include <algorithm>
#include <cstdint>
#include <map>
#include <random>
#include <string>
#include <vector>

namespace npu {

struct Job {
    std::uint32_t a_base = 0, b_base = 0, c_base = 0;
    std::uint32_t a_stride = 0, b_stride = 0, c_stride = 0;
    std::uint32_t m = 0, n = 0, k = 0;
};

struct Profile {
    std::string name;
    std::uint32_t mem_base, mem_bytes;   // the NPU's memory window
    std::uint32_t max_dim;
    bool c_any_alignment;                // v1: C at any byte offset
    bool rows_may_overlap;               // A_STRIDE < K, B_STRIDE < N allowed
    bool operands_may_overlap;           // A and B may overlap each other
    // STATUS bits
    std::uint32_t busy_bit, done_bit, error_bit, aborted_bit;
    bool done_with_error;                // v1 sets done with error and with abort
};

inline Profile v1_profile() {
    return {"v1", 0x10000000u, 0x8000u, 1024, true, false, false, 1u, 2u, 4u, 8u, true};
}

// A region's bytes as a half-open interval [lo, hi) in 64-bit arithmetic; empty
// when it has no elements.
struct Region { std::uint64_t lo = 0, hi = 0; bool empty() const { return hi <= lo; } };

inline Region region_a(const Job& j) {
    if (!j.m || !j.k) return {};
    return {j.a_base, std::uint64_t(j.a_base) + std::uint64_t(j.m - 1) * j.a_stride + j.k};
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
    return 0xFFFFFFFFu;   // ABI 2 arrives with 19.1
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
                const auto a = std::int8_t(memory.at(j.a_base + i * j.a_stride + k));
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
    for (int code = 1; code <= 6; ++code) names.push_back("error" + std::to_string(code));
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

private:
    // Place A, B and C at random disjoint offsets in the window; one job in
    // four pins one region to the window's base or to its top (its last byte
    // the window's last).
    bool place(Job& j) {
        const std::uint64_t sa = region_a({0, 0, 0, j.a_stride, j.b_stride, j.c_stride, j.m, j.n, j.k}).hi;
        const std::uint64_t sb = region_b({0, 0, 0, j.a_stride, j.b_stride, j.c_stride, j.m, j.n, j.k}).hi;
        const std::uint64_t sc = region_c({0, 0, 0, j.a_stride, j.b_stride, j.c_stride, j.m, j.n, j.k}).hi;
        if (sa + sb + sc + 16 > p.mem_bytes) return false;
        const unsigned pin = rng() % 4 == 0 ? 1 + rng() % 6 : 0;   // 1-3 base, 4-6 top (A, B, C)
        for (int attempt = 0; attempt < 64; ++attempt) {
            j.a_base = p.mem_base + std::uint32_t(rng() % (p.mem_bytes - sa + 1));
            j.b_base = p.mem_base + std::uint32_t(rng() % (p.mem_bytes - sb + 1));
            std::uint32_t c = std::uint32_t(rng() % (p.mem_bytes - sc + 1));
            if (!p.c_any_alignment || rng() % 2) c &= ~3u;
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
            if (!overlap(region_a(j), region_b(j)) && !overlap(region_a(j), region_c(j))
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
