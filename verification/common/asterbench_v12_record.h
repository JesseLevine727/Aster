#pragma once

#include <cstdint>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

// Mirrors scripts/asterbench_v12.py (docs/asterbench-v12.md): the same schema and
// invariants, check for check. The v12 corpus (verification/host/test_asterbench_v12.py)
// runs against both, which must agree on every record.
namespace asterbench_v12 {

inline const std::vector<std::string>& field_order() {
    static const std::vector<std::string> order = [] {
        std::vector<std::string> f = {"version", "name", "family", "method", "window", "status", "size",
                                      "iterations", "param", "seed", "checksum", "clock_hz", "harts", "workers",
                                      "dcache", "cache_state", "line_words", "line_count", "memory_wait", "npu_dim",
                                      "npu_port_bytes", "npu_strips"};
        const char* hart[] = {"cycles", "retired", "memory_transactions", "icache_accesses", "icache_misses",
                              "dcache_accesses", "dcache_misses", "backing_transactions", "amos", "sc_success",
                              "sc_failure", "dirty_interventions", "invalidations", "writeback_words",
                              "dot8_accept", "dot8_wait", "dot8_complete", "dot8_retire", "work_start", "work_end"};
        for (int h = 0; h < 2; ++h)
            for (const char* k : hart) f.push_back("h" + std::to_string(h) + "_" + k);
        for (const char* k : {"dma_jobs", "dma_completed_jobs", "dma_aborted_jobs", "dma_error_jobs", "dma_rejected",
                              "dma_bytes", "dma_busy_cycles", "dma_wait_cycles", "dma_reads", "dma_writes",
                              "dma_backing_reads", "dma_backing_writes", "dma_invalidations",
                              "npu_jobs", "npu_completed_jobs", "npu_aborted_jobs", "npu_error_jobs", "npu_job_cycles",
                              "npu_active_cycles", "npu_macs", "npu_bytes_read", "npu_bytes_written", "npu_tiles"})
            f.push_back(k);
        const char* req[] = {"i0", "d0", "i1", "d1", "n", "r", "w"};
        for (const char* r : req) f.push_back(std::string("f_accepted_") + r);
        for (const char* r : req) f.push_back(std::string("f_waited_") + r);
        for (const char* kind : {"reads", "writes", "conflicts"})
            for (int b = 0; b < 4; ++b) f.push_back("f_bank" + std::to_string(b) + "_" + kind);
        for (const char* kind : {"snoops", "invalidations"})
            for (int c = 0; c < 2; ++c)
                for (int p = 0; p < 3; ++p) f.push_back("f_" + std::string(kind) + "_c" + std::to_string(c) + "p" + std::to_string(p));
        for (const char* k : {"f_resv_ended_h0", "f_resv_ended_h1", "f_amos"}) f.push_back(k);
        for (const char* r : req) f.push_back(std::string("f_longest_") + r);
        return f;
    }();
    return order;
}

struct Record {
    std::map<std::string, std::string> text;          // the string fields
    std::map<std::string, std::uint64_t> value;      // every numeric field
    std::uint64_t operator[](const std::string& key) const { return value.at(key); }
};

inline void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}

inline bool canonical_decimal(const std::string& s) {
    if (s.empty() || s.size() > 20) return false;
    for (char c : s) if (c < '0' || c > '9') return false;
    return s.size() == 1 || s[0] != '0';
}

// Sums and products of 64-bit counts are formed in 128 bits, as Python's integers are unbounded: a
// mutated record near 2^64 must fail here as it does there, not wrap.
using u128 = unsigned __int128;

inline Record validate(const std::string& line, bool allow_fail = false) {
    static const std::set<std::string> strings = {"name", "family", "method", "window", "status", "cache_state"};
    static const std::set<std::string> hex32 = {"seed", "checksum"};
    static const std::set<std::string> small = {"version", "size", "iterations", "param", "clock_hz", "harts", "workers",
                                                "dcache", "line_words", "line_count", "memory_wait", "npu_dim",
                                                "npu_port_bytes", "npu_strips"};
    const auto& order = field_order();
    static const std::set<std::string> schema(order.begin(), order.end());

    require(line.size() <= 6144, "record exceeds the v12 length bound");
    require(line.find('\r') == std::string::npos, "record contains a carriage return");
    require(line.rfind("ASTERBENCH,", 0) == 0, "record prefix is not ASTERBENCH");
    require(!line.empty() && line.back() == '\n' && line.find('\n') == line.size() - 1, "record is not one complete line");
    const std::string body = line.substr(11, line.size() - 12);
    require(!body.empty(), "record has no fields");

    Record r;
    std::set<std::string> seen;
    std::size_t start = 0;
    while (true) {
        const std::size_t end = body.find(',', start);
        const std::string token = body.substr(start, end == std::string::npos ? std::string::npos : end - start);
        const std::size_t eq = token.find('=');
        require(eq != std::string::npos, "record field has no '='");
        const std::string key = token.substr(0, eq), val = token.substr(eq + 1);
        require(!key.empty() && !val.empty(), "record field has an empty key or value");
        require(!seen.count(key), "duplicate record field");
        seen.insert(key);
        require(schema.count(key), "record fields do not match the v12 schema");
        if (strings.count(key)) {
            r.text[key] = val;
        } else if (hex32.count(key)) {
            require(val.size() == 10 && val[0] == '0' && val[1] == 'x', "hex field is not 0x and eight digits");
            for (std::size_t i = 2; i < 10; ++i)
                require((val[i] >= '0' && val[i] <= '9') || (val[i] >= 'a' && val[i] <= 'f'), "hex field is not lowercase hex");
            r.value[key] = std::stoull(val, nullptr, 16);
        } else {
            require(canonical_decimal(val), "field is not canonical decimal");
            // 64-bit counts and 32-bit identity and configuration fields
            const bool narrow = small.count(key) != 0;
            require(val.size() < 20 || val <= std::string("18446744073709551615"), "field exceeds 64 bits");
            const unsigned long long x = std::stoull(val);
            require(!narrow || x <= 0xFFFFFFFFull, "field exceeds 32 bits");
            r.value[key] = x;
        }
        if (end == std::string::npos) break;
        start = end + 1;
    }
    require(seen.size() == schema.size(), "record fields do not match the v12 schema");

    auto v = [&](const std::string& k) { return r.value.at(k); };
    auto t = [&](const std::string& k) { return r.text.at(k); };
    auto in = [](const std::string& s, std::initializer_list<const char*> set) {
        for (const char* x : set) if (s == x) return true;
        return false;
    };
    // identity
    require(v("version") == 12, "version is not 12");
    const std::string& name = t("name");
    require(!name.empty() && name.size() <= 48 && name[0] >= 'a' && name[0] <= 'z', "invalid case name");
    for (char c : name) require((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_', "invalid case name");
    require(in(t("family"), {"cpu", "memory", "dma", "coherence", "dsp", "npu_gemm", "ml", "ecg"}), "unknown family");
    require(in(t("method"), {"scalar", "multicore", "dot8", "dma", "cpu_copy", "npu", "npu_direct", "npu_im2col", "pipeline"}),
            "unknown method");
    require(in(t("window"), {"kernel", "e2e"}), "unknown window");
    require(t("status") == "PASS" || (allow_fail && t("status") == "FAIL"), "record status is not PASS");
    require(in(t("cache_state"), {"cold", "warm"}), "unknown cache state");
    require(t("cache_state") == "warm" || t("window") == "e2e", "a cold record must be an e2e window");
    require(v("iterations") >= 1, "iterations must be positive");

    // configuration
    const std::uint64_t harts = v("harts");
    require(v("clock_hz") > 0, "clock_hz must be positive");
    require((harts == 1 || harts == 2) && v("workers") >= 1 && v("workers") <= harts, "invalid hart and worker topology");
    require(v("dcache") <= 1, "dcache must be 0 or 1");
    for (const char* k : {"line_words", "line_count"})
        require(v(k) >= 2 && v(k) <= 1024 && (v(k) & (v(k) - 1)) == 0, "line geometry must be a power of two, 2..1024");
    require(v("memory_wait") >= 1 && v("memory_wait") <= 16, "memory_wait is out of range");
    require(v("npu_dim") == 4 || v("npu_dim") == 8, "npu_dim must be 4 or 8");
    require(v("npu_port_bytes") == 4 || v("npu_port_bytes") == 8, "npu_port_bytes must be 4 or 8");
    require(v("npu_dim") != 8 || v("npu_port_bytes") == 8, "an 8x8 NPU needs the 64-bit port");
    require(v("npu_strips") == 1 || v("npu_strips") == 2, "npu_strips must be 1 or 2");

    // each hart
    const std::uint64_t cycles = v("h0_cycles");
    require(cycles > 0 && v("h0_retired") > 0, "hart 0 has no window or retired nothing");
    require(v("h1_cycles") == cycles, "the harts' window cycles differ");
    const char* counters[] = {"retired", "memory_transactions", "icache_accesses", "icache_misses", "dcache_accesses",
                              "dcache_misses", "backing_transactions", "amos", "sc_success", "sc_failure",
                              "dirty_interventions", "invalidations", "writeback_words", "dot8_accept", "dot8_wait",
                              "dot8_complete", "dot8_retire", "work_start", "work_end"};
    for (int h = 0; h < 2; ++h) {
        const std::string p = "h" + std::to_string(h) + "_";
        if (static_cast<std::uint64_t>(h) >= harts) {
            for (const char* k : counters) require(v(p + k) == 0, "an absent hart has activity");
            continue;
        }
        for (const char* k : {"retired", "memory_transactions", "icache_accesses", "dcache_accesses"})
            require(v(p + k) <= cycles, "a hart's count exceeds the window");
        require(u128(v(p + "backing_transactions")) <= 2 * u128(cycles), "backing transactions exceed twice the window");
        require(v(p + "icache_misses") <= v(p + "icache_accesses"), "icache misses exceed accesses");
        require(v(p + "dcache_misses") <= v(p + "dcache_accesses"), "dcache misses exceed accesses");
        require(u128(v(p + "sc_success")) + v(p + "sc_failure") <= v(p + "amos"), "sc attempts exceed A instructions");
        require(v(p + "dirty_interventions") == 0 && v(p + "writeback_words") == 0, "write-back events on write-through caches");
        require(v(p + "work_start") <= v(p + "work_end") && v(p + "work_end") <= cycles, "work interval is outside the window");
        require(v(p + "dot8_accept") == v(p + "dot8_complete") && v(p + "dot8_complete") == v(p + "dot8_retire") &&
                v(p + "dot8_retire") <= cycles, "DOT8 accept, complete and retire differ");
        require(v(p + "dot8_wait") == 0, "DOT8 waited");
    }

    // the DMA
    const bool passed = t("status") == "PASS";
    require(u128(v("dma_completed_jobs")) + v("dma_aborted_jobs") + v("dma_error_jobs") == v("dma_jobs"),
            "DMA job outcomes do not sum to its jobs");
    if (passed) {
        require(v("dma_aborted_jobs") == 0 && v("dma_error_jobs") == 0 && v("dma_rejected") == 0,
                "a PASS record has an aborted, failed or rejected DMA job");
        require(v("dma_reads") == v("dma_backing_reads") && v("dma_writes") == v("dma_backing_writes"),
                "a PASS record has DMA requests unanswered");
    }
    if (v("dma_jobs") == 0)
        for (const char* k : {"dma_completed_jobs", "dma_aborted_jobs", "dma_error_jobs", "dma_rejected", "dma_bytes",
                              "dma_busy_cycles", "dma_wait_cycles", "dma_reads", "dma_writes", "dma_backing_reads",
                              "dma_backing_writes", "dma_invalidations"})
            require(v(k) == 0, "DMA counts without a DMA job");
    require(v("dma_busy_cycles") <= cycles, "DMA busy cycles exceed the window");
    require(v("dma_reads") <= v("dma_backing_reads") && v("dma_writes") <= v("dma_backing_writes"),
            "DMA answers exceed its acceptances");
    require(u128(v("dma_bytes")) <= 8 * u128(v("dma_backing_writes")), "DMA bytes exceed its writes");
    require(v("dma_backing_reads") == v("f_accepted_r") && v("dma_backing_writes") == v("f_accepted_w"),
            "the DMA's acceptances differ from the fabric's R and W");
    require(u128(v("dma_wait_cycles")) == u128(v("f_waited_r")) + v("f_waited_w"), "the DMA's waits differ from the fabric's R and W");
    require(u128(v("dma_invalidations")) == u128(v("f_invalidations_c0p2")) + v("f_invalidations_c1p2"),
            "the DMA's invalidations differ from the fabric's snoop hits on W's port");

    // the NPU
    require(u128(v("npu_completed_jobs")) + v("npu_aborted_jobs") + v("npu_error_jobs") == v("npu_jobs"),
            "NPU job outcomes do not sum to its jobs");
    if (passed) require(v("npu_aborted_jobs") == 0 && v("npu_error_jobs") == 0, "a PASS record has an aborted or failed NPU job");
    require(v("npu_active_cycles") <= v("npu_job_cycles") && v("npu_job_cycles") <= cycles, "NPU cycles exceed the window");
    require(u128(v("npu_macs")) <= u128(v("npu_active_cycles")) * v("npu_dim") * v("npu_dim"), "NPU MACs exceed its array's capacity");
    if (v("npu_jobs") == 0) {
        for (const char* k : {"npu_completed_jobs", "npu_aborted_jobs", "npu_error_jobs", "npu_job_cycles",
                              "npu_active_cycles", "npu_macs", "npu_bytes_read", "npu_bytes_written", "npu_tiles"})
            require(v(k) == 0, "NPU activity without an NPU job");
        require(v("f_accepted_n") == 0, "NPU activity without an NPU job");
    }

    // the fabric
    for (const char* q : {"i0", "d0", "i1", "d1", "n", "r", "w"}) {
        const std::string s(q);
        require(v("f_accepted_" + s) <= cycles && v("f_waited_" + s) <= cycles, "the fabric's counts exceed the window");
        require(v("f_longest_" + s) <= v("f_waited_" + s) && v("f_longest_" + s) <= 0xFFFF,
                "the fabric's longest wait is out of range");
    }
    for (int c = 0; c < 2; ++c)
        for (int p = 0; p < 3; ++p) {
            const std::string s = "_c" + std::to_string(c) + "p" + std::to_string(p);
            require(v("f_invalidations" + s) <= v("f_snoops" + s), "a snoop port invalidated more lines than it was snooped");
        }
    u128 bank_writes = 0;
    for (int b = 0; b < 4; ++b) {
        const std::string s = "f_bank" + std::to_string(b);
        require(v(s + "_writes") <= cycles && v(s + "_conflicts") <= cycles && u128(v(s + "_reads")) <= 2 * u128(cycles),
                "a bank's counts exceed the window");
        bank_writes += v(s + "_writes");
    }
    require(bank_writes <= u128(v("f_accepted_d0")) + v("f_accepted_d1") + v("f_accepted_n") + v("f_accepted_w"),
            "the banks' writes exceed the writers' acceptances");
    require(v("f_resv_ended_h0") <= cycles && v("f_resv_ended_h1") <= cycles && v("f_amos") <= cycles,
            "the fabric's reservation or AMO counts exceed the window");
    return r;
}

}  // namespace asterbench_v12
