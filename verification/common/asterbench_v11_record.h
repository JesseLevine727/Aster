#pragma once

#include <cstdint>
#include <map>
#include <regex>
#include <stdexcept>
#include <string>

// Mirrors scripts/asterbench_v11.py. The v11 mutation corpus runs against both.
inline std::map<std::string, std::uint64_t>
validate_asterbench_v11_record(const std::string& line) {
    const std::map<std::string, char> schema = {
        {"version", 'd'}, {"name", 's'}, {"category", 's'}, {"status", 's'},
        {"size", 'd'}, {"iterations", 'd'}, {"param", 'd'}, {"seed", 'h'},
        {"checksum", 'h'}, {"clock_hz", 'd'}, {"harts", 'd'}, {"workers", 'd'},
        {"l1", 'd'}, {"sync_memory", 'd'}, {"line_words", 'd'},
        {"line_count", 'd'}, {"memory_wait", 'd'},
        {"cycles", 'q'}, {"retired", 'q'}, {"memory_transactions", 'q'},
        {"backing_transactions", 'q'}, {"cache_accesses", 'q'}, {"cache_misses", 'q'},
        {"dma_jobs", 'd'}, {"dma_completed_jobs", 'd'}, {"dma_aborted_jobs", 'd'},
        {"dma_error_jobs", 'd'}, {"dma_bytes", 'q'}, {"dma_job_cycles", 'q'},
        {"h0_dot8_accept", 'q'}, {"h0_dot8_wait", 'q'},
        {"h0_dot8_complete", 'q'}, {"h0_dot8_retire", 'q'},
        {"h1_dot8_accept", 'q'}, {"h1_dot8_wait", 'q'},
        {"h1_dot8_complete", 'q'}, {"h1_dot8_retire", 'q'},
        {"npu_jobs", 'd'}, {"npu_completed_jobs", 'd'},
        {"npu_aborted_jobs", 'd'}, {"npu_error_jobs", 'd'},
        {"npu_bytes_read", 'q'}, {"npu_bytes_written", 'q'}, {"npu_tiles", 'q'},
        {"npu_job_cycles", 'q'}, {"npu_compute_cycles", 'q'},
    };
    auto check = [](bool ok) {
        if (!ok) throw std::runtime_error("invalid AsterBench v11 record");
    };
    check(line.size() <= 8192 && line.rfind("ASTERBENCH,", 0) == 0 &&
          !line.empty() && line.back() == '\n');
    check(line.find('\n') == line.size() - 1 && line.find('\r') == std::string::npos);
    // The loop below stops at the newline, so reject an empty trailing field
    // explicitly (the Python validator rejects it as a field without '=').
    check(line.size() >= 2 && line[line.size() - 2] != ',');

    std::map<std::string, std::string> fields;
    std::map<std::string, std::uint64_t> numbers;
    std::size_t start = 11;
    while (start < line.size() - 1) {
        auto end = line.find(',', start);
        if (end == std::string::npos) end = line.size() - 1;
        const auto token = line.substr(start, end - start);
        const auto equal = token.find('=');
        check(equal != std::string::npos);
        const auto key = token.substr(0, equal);
        const auto value = token.substr(equal + 1);
        check(!key.empty() && !value.empty() && schema.count(key) && !fields.count(key));
        fields[key] = value;

        const char type = schema.at(key);
        if (type != 's') {
            const char* pattern = type == 'd' ? "(0|[1-9][0-9]*)" :
                                  type == 'h' ? "0x[0-9a-f]{8}" : "0x[0-9a-f]{16}";
            check(std::regex_match(value, std::regex(pattern)));
            numbers[key] = std::stoull(value, nullptr, type == 'd' ? 10 : 16);
            if (type == 'd') check(numbers[key] <= 0xffffffffull);
        }
        start = end + 1;
    }
    check(fields.size() == schema.size());

    check(numbers.at("version") == 11);
    check(std::regex_match(fields.at("name"), std::regex("[a-z][a-z0-9_]{0,31}")));
    const auto& category = fields.at("category");
    check(category == "cpu" || category == "memory" || category == "dsp" ||
          category == "ml" || category == "system");
    check(fields.at("status") == "PASS");
    check(numbers.at("size") >= 4 && numbers.at("size") <= 65536 && numbers.at("size") % 4 == 0);
    check(numbers.at("iterations") > 0 && numbers.at("param") > 0 && numbers.at("clock_hz") > 0);
    check((numbers.at("harts") == 1 || numbers.at("harts") == 2) &&
          numbers.at("workers") >= 1 && numbers.at("workers") <= numbers.at("harts"));
    check(numbers.at("l1") <= 1 && numbers.at("sync_memory") <= 1);
    for (const auto* key : {"line_words", "line_count"}) {
        const auto value = numbers.at(key);
        check(value >= 2 && value <= 1024 && (value & (value - 1)) == 0);
    }
    check(numbers.at("memory_wait") >= numbers.at("sync_memory") &&
          numbers.at("memory_wait") <= 1024);

    const auto cycles = numbers.at("cycles");
    check(cycles > 0 && numbers.at("retired") > 0 && numbers.at("retired") <= cycles);
    check(numbers.at("memory_transactions") > 0 && numbers.at("memory_transactions") <= cycles);
    check(numbers.at("backing_transactions") > 0 && numbers.at("backing_transactions") <= cycles);
    check(numbers.at("cache_misses") <= numbers.at("cache_accesses") &&
          numbers.at("cache_accesses") <= cycles);
    for (unsigned hart = 0; hart < 2; ++hart) {
        const std::string prefix = hart == 0 ? "h0_dot8_" : "h1_dot8_";
        for (const auto* event : {"accept", "wait", "complete", "retire"})
            check(numbers.at(prefix + event) <= cycles);
        check(numbers.at(prefix + "accept") == numbers.at(prefix + "complete") &&
              numbers.at(prefix + "complete") == numbers.at(prefix + "retire"));
        if (hart >= numbers.at("harts"))
            for (const auto* event : {"accept", "wait", "complete", "retire"})
                check(numbers.at(prefix + event) == 0);
    }

    check(numbers.at("dma_completed_jobs") + numbers.at("dma_aborted_jobs") +
          numbers.at("dma_error_jobs") == numbers.at("dma_jobs"));
    check(numbers.at("dma_completed_jobs") == numbers.at("dma_jobs") &&
          numbers.at("dma_aborted_jobs") == 0 && numbers.at("dma_error_jobs") == 0);
    if (numbers.at("dma_jobs") == 0)
        check(numbers.at("dma_bytes") == 0 && numbers.at("dma_job_cycles") == 0);
    check(numbers.at("dma_job_cycles") <= cycles);

    check(numbers.at("npu_completed_jobs") + numbers.at("npu_aborted_jobs") +
          numbers.at("npu_error_jobs") == numbers.at("npu_jobs"));
    check(numbers.at("npu_completed_jobs") == numbers.at("npu_jobs") &&
          numbers.at("npu_aborted_jobs") == 0 && numbers.at("npu_error_jobs") == 0);
    check(numbers.at("npu_compute_cycles") <= numbers.at("npu_job_cycles") &&
          numbers.at("npu_job_cycles") <= cycles);
    if (numbers.at("npu_jobs") == 0)
        check(numbers.at("npu_bytes_read") == 0 && numbers.at("npu_bytes_written") == 0 &&
              numbers.at("npu_tiles") == 0 && numbers.at("npu_job_cycles") == 0 &&
              numbers.at("npu_compute_cycles") == 0);
    return numbers;
}
