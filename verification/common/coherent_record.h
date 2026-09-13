#ifndef ASTER_COHERENT_RECORD_H
#define ASTER_COHERENT_RECORD_H
#include <array>
#include <cstdint>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static const std::array<std::string, 9> coherent_names = {"atomic_add", "lrsc_counter", "cas_counter",
    "lock_sum", "false_shared", "padded", "ping_pong", "spsc_queue", "shared_mix"};
static const std::array<std::string, 14> coherent_events = {"cycles", "retired", "memory", "i_access",
    "i_miss", "d_access", "d_miss", "backing", "atomic", "sc_success", "sc_failure",
    "intervention", "invalidation", "writeback"};
inline void coherent_require(bool ok, const std::string& why) {
    if (!ok) throw std::runtime_error(why);
}
inline uint64_t coherent_number(const std::string& s) {
    coherent_require(!s.empty() && s[0] >= '0' && s[0] <= '9', "invalid unsigned number");
    const bool hex = s.size() > 2 && s.substr(0, 2) == "0x";
    coherent_require(hex || s.size() == 1 || s[0] != '0', "noncanonical leading zero");
    for (unsigned i = hex ? 2 : 0; i < s.size(); ++i)
        coherent_require((s[i] >= '0' && s[i] <= '9') || (hex && s[i] >= 'a' && s[i] <= 'f'), "noncanonical number");
    std::size_t end;
    auto result = std::stoull(s, &end, hex ? 16 : 10);
    coherent_require(end == s.size(), "trailing numeric characters"); return result;
}
struct CoherentReference {
    uint32_t result0, result1, checksum, units0, units1;
    std::vector<uint32_t> output;
};
inline CoherentReference coherent_reference(unsigned kind, unsigned items, unsigned rounds, unsigned workers, uint32_t seed) {
    coherent_require(kind < 9 && items >= 2 && items <= 1024 && rounds >= 1 && rounds <= 64 &&
                     (workers == 1 || workers == 2), "invalid reference configuration");
    const unsigned split = workers == 1 ? items : (items+1)/2, other = items-split;
    CoherentReference r{0, 0, 0, split, other, std::vector<uint32_t>(items, 0)};
    if (kind <= 2) { r.result0 = items; r.result1 = r.checksum = uint64_t(items)*(items-1)/2; }
    else if (kind == 3) {
        r.result0 = items; r.result1 = r.checksum = uint64_t(items)*(items+1)/2; r.output[0] = r.result1;
    } else if (kind <= 5) {
        r.result0 = split; r.result1 = other;
        r.checksum = uint64_t(split)*(split-1)/2 + uint64_t(other)*(other-1)/2;
    } else if (kind <= 7) {
        r.units0 = items; r.units1 = workers == 2 ? items : 0;
        for (unsigned i = 0; i < items; ++i) r.checksum += uint32_t((seed ^ (i*0x1021u)) + 0x9e3779b9u);
        r.checksum *= workers;
        r.result0 = kind == 6 ? uint32_t((seed ^ ((items-1)*0x1021u)) + 0x9e3779b9u) ^ 0xa57e6u : items;
        r.result1 = kind == 6 ? ~r.result0 : items;
    } else {
        for (unsigned i = 0; i < items; ++i) {
            uint32_t value = seed ^ (i*0x1021u);
            for (unsigned round = 0; round < rounds; ++round) {
                value += 0x9e3779b9u + round; value ^= value >> 16; value *= 0x7feb352du;
                value ^= value >> 15; value *= 0x846ca68bu; value ^= value >> 16;
            }
            r.output[i] = value; r.checksum += value;
        }
        r.result0 = r.output.front(); r.result1 = r.output.back();
    }
    return r;
}
struct CoherentRecord { std::string name; unsigned kind; std::map<std::string,uint64_t> n; };
inline CoherentRecord validate_coherent_record(std::string line) {
    coherent_require(line.size() <= 8192 && !line.empty() && line.back() == '\n' &&
        line.find('\n') == line.size()-1 && line.find('\r') == std::string::npos, "oversized/incomplete/multiple v4 records");
    line.pop_back();
    coherent_require(line.rfind("ASTERBENCH,", 0) == 0, "missing v4 record prefix");
    line.erase(0, 11);
    coherent_require(!line.empty() && line.back() != ',', "empty field");
    std::map<std::string,std::string> fields;
    std::istringstream input(line); std::string field;
    while (std::getline(input, field, ',')) {
        auto equals = field.find('=');
        coherent_require(equals != std::string::npos && equals != 0 && equals+1 < field.size(), "malformed field");
        coherent_require(fields.emplace(field.substr(0,equals),field.substr(equals+1)).second, "duplicate field");
    }
    std::set<std::string> numeric = {"version", "items", "rounds", "jobs", "job", "base_seed", "seed", "harts", "workers",
        "h0_units", "h1_units", "result0", "result1", "checksum", "errors", "clock_hz", "l1", "sync_memory",
        "line_words", "line_count", "memory_wait", "counter0_addr", "counter1_addr"};
    std::set<std::string> wide;
    for (unsigned h = 0; h < 2; ++h) for (const auto& event : coherent_events) {
        auto key = "h"+std::to_string(h)+"_"+event; numeric.insert(key); wide.insert(key);
    }
    coherent_require(fields.size() == numeric.size()+3 && fields.count("name") && fields.count("status") && fields.count("window"),
                     "missing/unknown v4 fields");
    CoherentRecord r{fields.at("name"), 9, {}};
    for (unsigned i = 0; i < coherent_names.size(); ++i) if (coherent_names[i] == r.name) r.kind = i;
    coherent_require(r.kind < 9 && fields.at("status") == "PASS" && fields.at("window") == "dispatch_work_join", "invalid workload/status/window");
    for (const auto& key : numeric) {
        coherent_require(fields.count(key), "missing numeric field: "+key);
        r.n[key] = coherent_number(fields.at(key));
        coherent_require(wide.count(key) || r.n[key] <= UINT32_MAX, "field exceeds 32 bits");
    }
    auto& n = r.n;
    coherent_require(n.at("version") == 4 && n.at("jobs") >= 1 && n.at("jobs") <= 16 && n.at("job") >= 1 && n.at("job") <= n.at("jobs"), "wrong version/job");
    coherent_require(n.at("harts") >= 1 && n.at("harts") <= 2 && n.at("workers") <= n.at("harts"), "invalid topology");
    coherent_require(n.at("seed") == uint32_t(n.at("base_seed") ^ uint32_t(n.at("job")*0x9e3779b9u)), "wrong job seed");
    coherent_require(n.at("l1") <= 1 && n.at("sync_memory") <= 1 && n.at("memory_wait") >= n.at("sync_memory") && n.at("memory_wait") <= 1024 && n.at("clock_hz") > 0, "invalid memory/clock config");
    for (auto key : {"line_words", "line_count"}) {
        auto value = n.at(key); coherent_require(value >= (n.at("l1") ? 2u : 1u) && value <= 1024 && !(value & (value-1)), "invalid geometry");
    }
    const auto expected = coherent_reference(r.kind, n.at("items"), n.at("rounds"), n.at("workers"), n.at("seed"));
    coherent_require(n.at("result0") == expected.result0 && n.at("result1") == expected.result1 && n.at("checksum") == expected.checksum &&
        n.at("h0_units") == expected.units0 && n.at("h1_units") == expected.units1 && n.at("errors") == 0, "independent result mismatch");
    if (r.kind == 4 || r.kind == 5) {
        const auto a = n.at("counter0_addr"), b = n.at("counter1_addr");
        coherent_require(a >= 0x10000000 && b < 0x10008000 && !(a & 4095) && b == a+(r.kind == 4 ? 4 : 4096), "invalid sharing addresses");
    } else coherent_require(!n.at("counter0_addr") && !n.at("counter1_addr"), "irrelevant counter addresses");
    coherent_require(n.at("h0_cycles") && n.at("h0_cycles") == n.at("h1_cycles"), "counter windows differ");
    for (unsigned h = 0; h < 2; ++h) {
        const auto prefix = "h"+std::to_string(h)+"_";
        for (const auto& event : coherent_events) coherent_require(n.at(prefix+event) <= n.at(prefix+"cycles"), "event exceeds one per clock");
        coherent_require(n.at(prefix+"sc_success") <= n.at(prefix+"atomic") && n.at(prefix+"sc_failure") <= n.at(prefix+"atomic")-n.at(prefix+"sc_success"), "SC exceeds A completions");
        coherent_require(n.at(prefix+"i_miss") <= n.at(prefix+"i_access") && n.at(prefix+"d_miss") <= n.at(prefix+"d_access"), "misses exceed accesses");
        if (h >= n.at("workers")) for (unsigned i = 1; i < coherent_events.size(); ++i)
            coherent_require(!n.at(prefix+coherent_events[i]), "inactive worker has counted activity");
        else coherent_require(n.at(prefix+"retired") > n.at(prefix+"units") && n.at(prefix+"memory") > 0, "active worker has no work");
        if (!n.at("l1")) for (auto event : {"i_access", "i_miss", "d_access", "d_miss", "intervention", "invalidation", "writeback"})
            coherent_require(!n.at(prefix+event), "cache-off has coherent/cache events");
    }
    return r;
}
#endif
