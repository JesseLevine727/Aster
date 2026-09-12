#include "Vaster_multicore.h"
#include "verilated.h"
#include "../common/parallel_record.h"
#include <algorithm>
#include <iostream>
#include <limits>
#include <set>

static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const std::set<std::string> keys = {"kernel-start", "kernel-end", "jobs", "words", "rounds",
            "workers", "harts", "seed", "l1", "sync-memory", "memory-wait", "line-words", "line-count"};
        std::map<std::string, uint64_t> opts;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg.rfind("+", 0) == 0) continue;
            require(arg.rfind("--", 0) == 0 && i+1 < argc, "bad harness option");
            const auto key = arg.substr(2);
            require(keys.count(key) && !opts.count(key), "unknown/duplicate harness option");
            std::size_t end = 0;
            const std::string value = argv[++i];
            opts[key] = std::stoull(value, &end, 0);
            require(end == value.size(), "malformed harness option");
        }
        require(opts.size() == keys.size(), "missing harness option");
        require(opts["kernel-start"] < opts["kernel-end"] && opts["kernel-end"] <= 65536, "bad kernel symbol range");
        Vaster_multicore d;
        d.uart_tx_ready = 1;
        d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = 0;
        for (unsigned boot = 0; boot != 2; ++boot) {
            d.rst_n = 0;
            for (unsigned i = 0; i != 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
            require(!d.hart_run && !d.hart_trap && !d.uart_tx_valid, "reset not clean");
            d.rst_n = 1;
            bool running = false;
            unsigned starts = 0, freezes = 0, records = 0, quiet = 0;
            std::array<std::array<uint64_t, 8>, 2> counters{};
            std::array<std::vector<uint64_t>, 2> kernel_times;
            std::string line;
            for (uint64_t cycle = 0; cycle != 1000000000ull; ++cycle) {
                d.clk = 0; d.eval();
                require(!d.hart_trap, "hardware hart trapped");
                require(!d.perf_resume, "unexpected benchmark resume command");
                if (d.perf_start) {
                    require(!running && starts == freezes && records == freezes, "overlapping/missing measurement window");
                    ++starts; counters = {}; kernel_times = {}; running = true;
                } else if (d.perf_freeze) {
                    require(running, "freeze without active job");
                    running = false; ++freezes;
                } else if (running) {
                    for (unsigned h = 0; h != 2; ++h) {
                        ++counters[h][0];
                        counters[h][1] += (d.retired >> h) & 1;
                        counters[h][2] += (d.memory_events >> h) & 1;
                        counters[h][3] += (d.cache_access_events >> h) & 1;
                        counters[h][4] += (d.cache_miss_events >> h) & 1;
                        counters[h][7] += (d.backing_events >> h) & 1;
                        if ((d.retired & (1u << h)) && d.retired_pc[h] >= opts["kernel-start"] &&
                            d.retired_pc[h] < opts["kernel-end"]) kernel_times[h].push_back(counters[h][0]);
                    }
                }
                if (d.uart_tx_valid) {
                    require(records < opts["jobs"], "trailing serial bytes");
                    line += char(d.uart_tx_data);
                    require(line.size() <= 4096, "oversized parallel record");
                    if (line.back() == '\n') {
                        const auto n = validate_parallel_record(line);
                        require(!running && freezes == records+1 && starts == freezes, "UART record outside frozen job");
                        require(n.at("job") == records+1 && n.at("jobs") == opts["jobs"], "missing/reordered job");
                        for (const auto& pair : std::map<std::string,std::string>{{"rounds","rounds"},{"workers","workers"},
                            {"harts","harts"},{"base_seed","seed"},{"l1","l1"},{"sync_memory","sync-memory"},
                            {"memory_wait","memory-wait"},{"line_words","line-words"},{"line_count","line-count"}})
                            require(n.at(pair.first) == opts[pair.second], "firmware/RTL configuration mismatch");
                        require(n.at("bytes") == opts["words"]*4, "wrong workload size");
                        const auto sums = parallel_reference(opts["words"], opts["rounds"], n.at("seed"), opts["workers"]);
                        for (unsigned h = 0; h != 2; ++h) {
                            const auto prefix = "h"+std::to_string(h)+"_";
                            require(n.at(prefix+"checksum") == sums[h], "independent C++ result oracle disagrees");
                            for (unsigned i = 0; i != 8; ++i)
                                require(n.at(prefix+parallel_events[i]) == counters[h][i], "RTL event scoreboard differs from emitted counters");
                            require(kernel_times[h].size() >= n.at(prefix+"words")*opts["rounds"], "missing real per-hart kernel retirement");
                            if (h >= opts["workers"]) require(kernel_times[h].empty(), "inactive worker executed kernel");
                        }
                        uint64_t overlap = 0;
                        if (opts["workers"] == 2) {
                            auto begin = std::max(kernel_times[0].front(), kernel_times[1].front());
                            auto end = std::min(kernel_times[0].back(), kernel_times[1].back());
                            if (end >= begin) overlap = end-begin+1;
                            if (opts["words"] >= 64 && opts["rounds"] >= 4)
                                require(overlap > 0, "substantial two-worker kernel did not overlap in time");
                        }
                        std::cout << line;
                        std::cerr << "ASTEROBS,{\"boot\":" << boot << ",\"job\":" << records+1
                                  << ",\"cycles\":" << counters[0][0] << ",\"kernel_overlap_cycles\":" << overlap;
                        for (unsigned h = 0; h != 2; ++h) {
                            std::cerr << ",\"h" << h << "_kernel_retired\":" << kernel_times[h].size()
                                << ",\"h" << h << "_kernel_first\":" << (kernel_times[h].empty() ? 0 : kernel_times[h].front())
                                << ",\"h" << h << "_kernel_last\":" << (kernel_times[h].empty() ? 0 : kernel_times[h].back());
                        }
                        std::cerr << "}\n";
                        line.clear(); ++records;
                    }
                }
                d.clk = 1; d.eval();
                if (records == opts["jobs"] && ++quiet == 1000) break;
            }
            require(records == opts["jobs"] && quiet == 1000, "parallel firmware timeout");
        }
        std::cerr << "PASS: parallel jobs, independent kernel retirement and exact RTL counter scoreboard (2 boots)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
