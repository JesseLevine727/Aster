#pragma once

#include <cstdint>
#include <map>
#include <regex>
#include <stdexcept>
#include <string>

// Mirrored by scripts/asterbench.py; the same valid/malformed fixture corpus
// is run against both parsers by verification/host/test_asterbench.py.
inline std::map<std::string, std::uint64_t> validate_bench_record(const std::string& line) {
    const std::map<std::string, char> schema = {
        {"version", 'd'}, {"name", 's'}, {"bytes", 'd'}, {"repetitions", 'd'},
        {"seed", 'h'}, {"status", 's'}, {"checksum", 'h'}, {"clock_hz", 'd'},
        {"l1", 'd'}, {"sync_memory", 'd'}, {"line_words", 'd'}, {"line_count", 'd'},
        {"memory_wait", 'd'}, {"cycles", 'q'}, {"retired", 'q'},
        {"memory_transactions", 'q'}, {"backing_transactions", 'q'},
        {"cache_accesses", 'q'}, {"cache_misses", 'q'}, {"dma_bytes", 'q'},
        {"accelerator_cycles", 'q'}
    };
    auto check = [](bool ok) { if (!ok) throw std::runtime_error("invalid AsterBench v2 record"); };
    check(line.size() <= 4096 && line.rfind("ASTERBENCH,", 0) == 0 && line.back() == '\n');
    check(line.find('\n') == line.size()-1);
    std::map<std::string, std::string> fields;
    std::map<std::string, std::uint64_t> numbers;
    std::size_t start = 11;
    while (start < line.size()-1) {
        auto end = line.find(',', start);
        if (end == std::string::npos) end = line.size()-1;
        const auto field = line.substr(start, end-start);
        const auto equal = field.find('=');
        check(equal != std::string::npos && field.find('=', equal+1) == std::string::npos);
        const auto key = field.substr(0, equal), value = field.substr(equal+1);
        check(schema.count(key) && !fields.count(key));
        fields[key] = value;
        const char type = schema.at(key);
        if (type != 's') {
            const char* pattern = type == 'd' ? "(0|[1-9][0-9]{0,9})" :
                                  type == 'h' ? "0x[0-9a-fA-F]{8}" : "0x[0-9a-fA-F]{16}";
            check(std::regex_match(value, std::regex(pattern)));
            numbers[key] = std::stoull(value, nullptr, type == 'd' ? 10 : 16);
            if (type == 'd') check(numbers[key] <= 0xffffffffull);
        }
        check(end != line.size()-2); // no trailing comma
        start = end+1;
    }
    check(fields.size() == schema.size());
    check(numbers["version"] == 2 && std::regex_match(fields["name"], std::regex("[a-z][a-z0-9_]{0,31}")));
    check(fields["status"] == "PASS");
    check(numbers["bytes"] > 0 && numbers["bytes"] <= 65536 && numbers["bytes"] % 4 == 0);
    check(numbers["repetitions"] > 0 && numbers["clock_hz"] > 0);
    check(numbers["l1"] <= 1 && numbers["sync_memory"] <= 1);
    for (const auto* key : {"line_words", "line_count"}) {
        const auto value = numbers[key];
        check(value >= 2 && value <= 1024 && (value & (value-1)) == 0);
    }
    check(numbers["memory_wait"] >= numbers["sync_memory"] && numbers["memory_wait"] <= 1024);
    check(numbers["cycles"] > 0 && numbers["retired"] > 0 && numbers["retired"] <= numbers["cycles"]);
    check(numbers["memory_transactions"] > 0 && numbers["backing_transactions"] > 0);
    for (const auto* key : {"memory_transactions", "backing_transactions", "cache_accesses"})
        check(numbers[key] <= numbers["cycles"]);
    check(numbers["cache_misses"] <= numbers["cache_accesses"]);
    if (!numbers["l1"]) check(!numbers["cache_accesses"] && !numbers["cache_misses"]);
    check(!numbers["dma_bytes"] && !numbers["accelerator_cycles"]);
    return numbers;
}
