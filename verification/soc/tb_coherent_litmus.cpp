#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include "../common/coherent_record.h"
#include <iostream>
#include <random>

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        std::map<std::string,uint64_t> opts;
        const std::set<std::string> keys = {"--kernel-start", "--kernel-end", "--trial", "--summary", "--epochs", "--steps", "--seed"};
        for (int i = 1; i < argc; ++i) {
            const std::string key = argv[i]; if (key.rfind("+", 0) == 0) continue;
            coherent_require(keys.count(key) && i+1 < argc, "unknown/missing litmus option");
            coherent_require(opts.emplace(key, coherent_number(argv[++i])).second, "duplicate litmus option");
        }
        coherent_require(opts.size() == keys.size(), "missing litmus options");
        const uint64_t epochs = opts.at("--epochs"), steps = opts.at("--steps");
        coherent_require(epochs >= 2 && epochs <= 1024 && steps >= 2 && steps <= 1024 && opts.at("--seed") <= UINT32_MAX, "invalid litmus workload");
        coherent_require(opts.at("--kernel-start") < opts.at("--kernel-end") && opts.at("--kernel-end") <= 65536, "invalid kernel range");
        for (auto key : {"--trial", "--summary"}) coherent_require(opts.at(key) >= 0x10008000 && opts.at(key)+(std::string(key)=="--trial" ? 32u : 2048u) <= 0x1000b000 && !(opts.at(key)&3), "invalid observation address");
        const unsigned trial = (opts.at("--trial")-0x10000000)/4;
        const unsigned summary = (opts.at("--summary")-0x10000000)/4;
        Vaster_coherent_soc d;
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; d.clk = 0; d.eval(); d.clk = 1; d.eval();
        coherent_require(d.stopped, "initial litmus stop missing");
        std::array<uint32_t, 16384> memory; memory.fill(0xa5a5a5a5);
        std::mt19937 random(opts.at("--seed"));
        uint64_t total_trials = 0, all_sc_failures = 0;
        for (unsigned boot = 1; boot <= 2; ++boot) {
            bool running = false;
            unsigned starts = 0, freezes = 0, records = 0, trials = 0, secondary_stops = 0, primary_entries = 0;
            std::array<unsigned,4> histogram{};
            std::array<std::array<uint64_t,14>,2> counters{};
            std::array<unsigned,2> kernel{};
            std::string line;
            auto edge = [&](bool serial) {
                d.clk = 0; d.uart_tx_ready = random()%4 != 0; d.eval();
                coherent_require(!d.hart_trap && !d.fault_valid, "litmus firmware trapped");
                coherent_require(!d.perf_resume, "unexpected litmus RESUME");
                if (d.perf_start) {
                    coherent_require(!running && starts == freezes && records == freezes && records < 8, "invalid litmus START");
                    ++starts; running = true; counters = {}; histogram = {}; kernel = {}; trials = 0;
                } else if (d.perf_freeze) {
                    coherent_require(running && trials == epochs, "FREEZE before all independent trials");
                    ++freezes; running = false;
                } else if (running) {
                    for (unsigned h = 0; h < 2; ++h) {
                        for (unsigned i = 0; i < 14; ++i) counters[h][i] += (d.perf_events[h] >> i)&1;
                        if ((d.retired & (1u << h)) && d.retired_pc[h] >= opts.at("--kernel-start") && d.retired_pc[h] < opts.at("--kernel-end")) ++kernel[h];
                    }
                }
                if ((d.retired&1) && d.retired_pc[0] == 0) ++primary_entries;
                if (d.stop_commit == 2) ++secondary_stops;
                if (d.store_commit) {
                    coherent_require(d.store_addr >= 0x10000000 && d.store_addr < 0x10010000 && d.store_mask, "invalid architectural store");
                    auto& value = memory[(d.store_addr-0x10000000)/4];
                    for (unsigned b = 0; b < 4; ++b) if (d.store_mask & (1u << b))
                        value = (value & ~(255u << (8*b))) | (d.store_data & (255u << (8*b)));
                    if (d.store_addr == opts.at("--trial")+28 && value) {
                        coherent_require(running && d.store_owner == 0 && d.store_mask == 15, "trial publication outside primary window");
                        const unsigned mode = memory[trial], number = memory[trial+1];
                        const auto r0 = memory[trial+2], r1 = memory[trial+3], x = memory[trial+4], y = memory[trial+5];
                        coherent_require(mode == records && number == trials+1 && value == mode*epochs+number && memory[trial+6] == 0, "stale/reordered/failed trial");
                        const uint32_t seed = uint32_t(opts.at("--seed")) ^ uint32_t(value*0x9e3779b9u);
                        unsigned outcome = 0;
                        if (mode <= 3 || mode == 5) {
                            coherent_require(r0 <= 1 && r1 <= 1 && x == 1 && y == 1, "bad binary litmus result");
                            outcome = r0*2+r1;
                            coherent_require((mode == 2 || mode == 3) ? outcome != 3 : outcome != 0, "forbidden memory-ordering outcome");
                        } else if (mode == 4) {
                            uint32_t sum = 0;
                            for (unsigned i = 0; i < 16; ++i) sum += (seed ^ (i*0x1021u)) + 0x9e3779b9u;
                            coherent_require(r0 == sum && r1 == sum && x == 0 && y == 0, "release/acquire published stale data");
                        } else if (mode == 6) {
                            coherent_require(x == steps && y == uint32_t(seed+steps-1) && r0 == steps*(steps-1)/2 && r1 <= steps*steps,
                                             "read-only/unrelated-write LRSC progress result failed");
                        } else coherent_require(x == 2*steps && y == 0 && uint64_t(r0)+r1 == steps*(2*steps-1), "contended LRSC lost/duplicated tickets");
                        ++histogram[outcome]; ++trials; ++total_trials;
                    }
                }
                if (serial && d.uart_tx_valid && d.uart_tx_ready) {
                    line += char(d.uart_tx_data); coherent_require(line.size() < 128, "invalid litmus UART");
                    if (line.back() == '\n') {
                        coherent_require(records < 8 && line == "LITMUS PASS mode="+std::to_string(records)+"\n" &&
                            !running && freezes == records+1 && starts == freezes, "failed/reordered/incomplete phase record");
                        unsigned base = summary+records*64;
                        coherent_require(memory[base] == records && memory[base+1] == epochs && memory[base+2] == opts.at("--seed") && memory[base+7] == 0, "incorrect summary identity");
                        for (unsigned i = 0; i < 4; ++i) coherent_require(memory[base+3+i] == histogram[i], "summary differs from independent trial histogram");
                        for (unsigned h = 0; h < 2; ++h) {
                            coherent_require(kernel[h] >= epochs, "missing actual per-hart litmus execution");
                            for (unsigned i = 0; i < 14; ++i) {
                                uint64_t actual = memory[base+8+h*28+i*2] | (uint64_t(memory[base+9+h*28+i*2]) << 32);
                                coherent_require(actual == counters[h][i], "litmus counter register differs from actual event scoreboard");
                            }
                            const uint64_t successes = (records == 7 || (records == 6 && h == 0)) ? epochs*steps : 0;
                            coherent_require(counters[h][9] == successes, "missing/duplicate SC success");
                            if (records != 7) coherent_require(counters[h][10] == 0, "unrelated traffic/eviction manufactured SC failure");
                            all_sc_failures += counters[h][10];
                        }
                        std::cout << "PASS: coherent litmus boot=" << boot << " mode=" << records << " epochs=" << epochs
                                  << " seed=" << opts.at("--seed") << " histogram=" << histogram[0] << ',' << histogram[1] << ',' << histogram[2] << ',' << histogram[3]
                                  << " sc_failure=" << counters[0][10] << ',' << counters[1][10] << " exact per-hart counters\n";
                        ++records; line.clear();
                    }
                }
                d.clk = 1; d.eval();
            };
            d.host_run = 1;
            unsigned quiet = 0;
            for (uint64_t cycle = 0; cycle < 1000000000ull && quiet < 512; ++cycle) {
                edge(true); if (records == 8 && d.hart_run == 1) ++quiet;
            }
            coherent_require(quiet == 512 && !running && line.empty() && primary_entries == 1 && secondary_stops == 1, "litmus progress/lifecycle timeout");
            d.host_run = 0;
            for (unsigned i = 0; !d.stopped && i < 1000000; ++i) edge(false);
            coherent_require(d.stopped && !d.reservations && !d.fabric_busy && !d.flush_active, "litmus warm stop failed");
            for (unsigned i = 0; i < memory.size(); ++i) {
                d.host_ram_addr = i*4; edge(false); edge(false);
                coherent_require(d.host_ram_rdata == memory[i], "litmus warm stop lost acknowledged RAM");
            }
        }
        std::cout << "PASS: coherent litmus closeout trials=" << total_trials << " contended_sc_failures=" << all_sc_failures
                  << " warm_boots=2 complete_ram=65536; no destructive reset after POR\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
