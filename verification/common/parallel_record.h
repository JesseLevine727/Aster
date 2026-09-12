#pragma once
#include <array>
#include <cstdint>
#include <map>
#include <regex>
#include <stdexcept>
#include <string>
#include <vector>

inline const std::array<std::string, 8> parallel_events = {"cycles", "retired", "memory_transactions",
    "cache_accesses", "cache_misses", "dma_bytes", "accelerator_cycles", "backing_transactions"};

inline std::map<std::string, uint64_t> validate_parallel_record(const std::string& line) {
    auto check = [](bool ok) { if (!ok) throw std::runtime_error("invalid AsterBench v3 record"); };
    std::map<std::string, char> schema;
    for (auto key : {"version", "bytes", "rounds", "jobs", "job", "harts", "workers", "h0_words", "h1_words",
                    "clock_hz", "l1", "sync_memory", "line_words", "line_count", "memory_wait"}) schema[key] = 'd';
    for (auto key : {"base_seed", "seed", "checksum", "h0_checksum", "h1_checksum"}) schema[key] = 'h';
    schema["cycles"] = 'q'; schema["name"] = schema["status"] = 's';
    for (unsigned h = 0; h != 2; ++h) for (const auto& event : parallel_events)
        schema["h"+std::to_string(h)+"_"+event] = 'q';
    check(line.size() <= 4096 && line.rfind("ASTERBENCH,", 0) == 0 && line.back() == '\n');
    check(line.find('\n') == line.size()-1);
    std::map<std::string, std::string> fields;
    std::map<std::string, uint64_t> n;
    std::size_t start = 11;
    while (start < line.size()-1) {
        auto end = line.find(',', start);
        if (end == std::string::npos) end = line.size()-1;
        const auto field = line.substr(start, end-start);
        const auto eq = field.find('=');
        check(eq != std::string::npos && field.find('=', eq+1) == std::string::npos);
        const auto key = field.substr(0, eq), value = field.substr(eq+1);
        check(schema.count(key) && !fields.count(key));
        fields[key] = value;
        const char type = schema.at(key);
        if (type != 's') {
            check(std::regex_match(value, std::regex(type == 'd' ? "(0|[1-9][0-9]{0,9})" :
                type == 'h' ? "0x[0-9a-fA-F]{8}" : "0x[0-9a-fA-F]{16}")));
            n[key] = std::stoull(value, nullptr, type == 'd' ? 10 : 16);
            if (type == 'd') check(n[key] <= 0xffffffffull);
        }
        check(end != line.size()-2);
        start = end+1;
    }
    check(fields.size() == schema.size());
    check(n["version"] == 3 && fields["name"] == "parallel_mix" && fields["status"] == "PASS");
    check(n["bytes"] >= 8 && n["bytes"] <= 4096 && n["bytes"] % 4 == 0);
    check(n["rounds"] >= 1 && n["rounds"] <= 64 && n["job"] >= 1 && n["job"] <= n["jobs"] && n["jobs"] <= 16);
    check(n["harts"] >= 1 && n["harts"] <= 2 && n["workers"] >= 1 && n["workers"] <= n["harts"]);
    const auto words = n["bytes"]/4, split = n["workers"] == 2 ? (words+1)/2 : words;
    check(n["h0_words"] == split && n["h1_words"] == words-split);
    check(n["seed"] == (n["base_seed"] ^ ((n["job"]*0x9e3779b9) & 0xffffffffull)));
    check(n["checksum"] == ((n["h0_checksum"]+n["h1_checksum"]) & 0xffffffffull));
    check(n["clock_hz"] > 0 && n["l1"] <= 1 && n["sync_memory"] <= 1);
    for (auto key : {"line_words", "line_count"}) {
        auto v = n[key]; check(v >= 2 && v <= 1024 && !(v & (v-1)));
    }
    check(n["memory_wait"] >= n["sync_memory"] && n["memory_wait"] <= 1024 && n["cycles"] > 0);
    for (unsigned h = 0; h != 2; ++h) {
        const auto prefix = "h"+std::to_string(h)+"_";
        auto v = [&](const std::string& event) { return n[prefix+event]; };
        check(v("cycles") == n["cycles"]);
        for (unsigned i = 1; i != parallel_events.size(); ++i) check(v(parallel_events[i]) <= n["cycles"]);
        check(v("dma_bytes") == 0 && v("accelerator_cycles") == 0);
        check(v("cache_accesses") <= v("memory_transactions"));
        if (!n["l1"]) check(v("cache_accesses") == 0 && v("cache_misses") == 0);
        if (h < n["workers"]) check(v("retired") > 0 && v("memory_transactions") > 0 && v("backing_transactions") > 0);
        else {
            for (unsigned i = 1; i != parallel_events.size(); ++i) check(v(parallel_events[i]) == 0);
            check(n[prefix+"checksum"] == 0);
        }
    }
    check(n["h0_backing_transactions"] <= n["cycles"] - n["h1_backing_transactions"]);
    return n;
}

inline std::array<uint32_t, 2> parallel_reference(unsigned words, unsigned rounds, uint32_t seed, unsigned workers) {
    std::array<uint32_t, 2> sums{};
    const unsigned split = workers == 2 ? (words+1)/2 : words;
    for (unsigned i = 0; i != words; ++i) {
        uint32_t value = seed ^ (i*0x1021u);
        for (unsigned r = 0; r != rounds; ++r) {
            value += 0x9e3779b9u + r;
            value ^= value >> 16; value *= 0x7feb352du;
            value ^= value >> 15; value *= 0x846ca68bu;
            value ^= value >> 16;
        }
        sums[i >= split] += value ^ ((i+1u)*0x9e3779b9u);
    }
    return sums;
}
