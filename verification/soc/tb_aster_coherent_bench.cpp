#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include "../common/coherent_record.h"
#include <fstream>
#include <iostream>
#include <random>

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        const std::set<std::string> keys = {"kernel-start", "kernel-end", "result-addr", "output-addr", "kind",
            "items", "rounds", "workers", "harts", "jobs", "seed", "l1", "sync-memory", "memory-wait",
            "line-words", "line-count", "boots", "uart-seed"};
        std::map<std::string, uint64_t> opts;
        std::string ram_prefix;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg.rfind("+", 0) == 0) continue;
            coherent_require(arg.rfind("--", 0) == 0 && i+1 < argc, "invalid harness option");
            auto key = arg.substr(2); const std::string value = argv[++i];
            if (key == "ram-prefix") { coherent_require(ram_prefix.empty(), "duplicate snapshot path"); ram_prefix = value; }
            else {
                coherent_require(keys.count(key) && !opts.count(key), "unknown/duplicate harness option");
                opts[key] = coherent_number(value);
            }
        }
        coherent_require(opts.size() == keys.size() && !ram_prefix.empty(), "missing harness options");
        coherent_require(opts.at("kernel-start") < opts.at("kernel-end") && opts.at("kernel-end") <= 65536, "kernel outside ROM");
        coherent_require(opts.at("boots") >= 1 && opts.at("boots") <= 16 && opts.at("jobs") >= 1 && opts.at("jobs") <= 16, "bad run length");
        coherent_require(opts.at("result-addr") >= 0x10008000 && opts.at("result-addr")+opts.at("jobs")*32 <= 0x1000b000 &&
            opts.at("output-addr") >= 0x10000000 && opts.at("output-addr")+opts.at("items")*4 <= 0x10008000 &&
            !(opts.at("result-addr") & 3) && !(opts.at("output-addr") & 3), "bad RAM symbol range");
        coherent_reference(opts.at("kind"), opts.at("items"), opts.at("rounds"), opts.at("workers"), opts.at("seed"));
        Vaster_coherent_soc d;
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; d.clk = 0; d.eval(); d.clk = 1; d.eval();
        coherent_require(d.stopped, "not stopped after POR");
        std::array<uint32_t, 16384> memory; memory.fill(0xa5a5a5a5);
        std::mt19937 rng(opts.at("uart-seed"));
        unsigned records_total = 0;
        for (unsigned boot = 1; boot <= opts.at("boots"); ++boot) {
            std::cout << "ASTERBOOT " << boot << '\n';
            unsigned records = 0, starts = 0, freezes = 0, stores = 0, primary_entries = 0, secondary_stops = 0;
            unsigned quiet = 0;
            bool running = false;
            std::array<std::array<uint64_t, 14>, 2> counters{};
            std::array<std::uint32_t, 2> events_q{};
            std::array<uint64_t, 2> kernel_count{}, first{}, last{}, lifetime{};
            std::string line;
            auto edge = [&](bool check_uart) {
                d.clk = 0;
                d.uart_tx_ready = opts.at("uart-seed") == 0 || rng()%4 != 0;
                d.eval();
                coherent_require(!d.hart_trap && !d.fault_valid, "real coherent core trapped");
                coherent_require(!d.perf_resume, "unexpected RESUME");
                if (d.perf_start) {
                    coherent_require(!running && starts == freezes && records == freezes, "overlapping measurement window");
                    running = true; ++starts; counters = {}; kernel_count = {}; first = {}; last = {};
                } else if (d.perf_freeze) {
                    coherent_require(running, "FREEZE without START"); running = false; ++freezes;
                } else if (running) {
                    for (unsigned h = 0; h < 2; ++h) {
                        // Independent software accumulation of observed events,
                        // not reads of the register counters being validated.
                        for (unsigned i = 0; i < 14; ++i) counters[h][i] += (events_q[h] >> i) & 1;
                        coherent_require(((d.perf_events[h] >> 1)&1) == ((d.retired >> h)&1), "retirement event misrouted");
                        if ((d.retired & (1u << h)) && d.retired_pc[h] >= opts.at("kernel-start") && d.retired_pc[h] < opts.at("kernel-end")) {
                            if (!kernel_count[h]++) first[h] = counters[h][0];
                            last[h] = counters[h][0];
                        }
                    }
                }
                for (unsigned h = 0; h < 2; ++h) events_q[h] = d.perf_events[h];
                for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) ++lifetime[h];
                if ((d.retired & 1) && d.retired_pc[0] == 0) ++primary_entries;
                if (d.stop_commit == 2) ++secondary_stops;
                if (d.store_commit) {
                    coherent_require(d.store_addr >= 0x10000000 && d.store_addr < 0x10010000 && d.store_mask, "invalid architectural store");
                    auto& value = memory[(d.store_addr-0x10000000)/4];
                    for (unsigned b = 0; b < 4; ++b) if (d.store_mask & (1u << b))
                        value = (value & ~(255u << (8*b))) | (d.store_data & (255u << (8*b)));
                    ++stores;
                }
                if (check_uart && d.uart_tx_valid && d.uart_tx_ready) {
                    coherent_require(records < opts.at("jobs"), "trailing UART");
                    line += char(d.uart_tx_data); coherent_require(line.size() <= 8192, "oversized UART line");
                    if (line.back() == '\n') {
                        const auto record = validate_coherent_record(line); const auto& n = record.n;
                        coherent_require(!running && starts == freezes && freezes == records+1, "record outside frozen job");
                        coherent_require(record.kind == opts.at("kind") && n.at("job") == records+1, "wrong workload/job order");
                        for (const auto& pair : std::map<std::string,std::string>{{"items","items"}, {"rounds","rounds"}, {"jobs","jobs"},
                            {"workers","workers"},{"harts","harts"},{"base_seed","seed"},{"l1","l1"},
                            {"sync_memory","sync-memory"},{"memory_wait","memory-wait"},{"line_words","line-words"},{"line_count","line-count"}})
                            coherent_require(n.at(pair.first) == opts.at(pair.second), "firmware/RTL configuration mismatch: "+pair.first);
                        coherent_require(n.at("clock_hz") == 31250000, "unexpected clock metadata");
                        for (unsigned h = 0; h < 2; ++h) {
                            auto prefix = "h"+std::to_string(h)+"_";
                            for (unsigned i = 0; i < 14; ++i)
                                coherent_require(n.at(prefix+coherent_events[i]) == counters[h][i], "actual RTL event scoreboard differs: "+prefix+coherent_events[i]);
                            coherent_require(h < opts.at("workers") ? kernel_count[h] >= n.at(prefix+"units") : kernel_count[h] == 0,
                                             "missing/unexpected actual per-hart kernel execution");
                        }
                        const auto ref = coherent_reference(record.kind, n.at("items"), n.at("rounds"), n.at("workers"), n.at("seed"));
                        const unsigned result_index = (opts.at("result-addr")-0x10000000)/4+records*8;
                        const std::array<uint32_t, 8> expected = {uint32_t(records+1), uint32_t(n.at("seed")), ref.result0, ref.result1,
                            ref.checksum, 0, ref.units0, ref.units1};
                        for (unsigned i = 0; i < 8; ++i) coherent_require(memory[result_index+i] == expected[i], "RAM-published result differs from oracle");
                        const unsigned output_index = (opts.at("output-addr")-0x10000000)/4;
                        for (unsigned i = 0; i < ref.output.size(); ++i)
                            coherent_require(memory[output_index+i] == ref.output[i], "complete RAM output differs from oracle");
                        std::cout << line << "COHERENT_OBS {\"boot\":" << boot << ",\"job\":" << records+1 << ",\"counters\":[";
                        for (unsigned h = 0; h < 2; ++h) {
                            std::cout << (h ? ",[" : "[");
                            for (unsigned i = 0; i < 14; ++i) std::cout << (i ? "," : "") << counters[h][i];
                            std::cout << ']';
                        }
                        std::cout << "],\"kernel_retired\":[" << kernel_count[0] << ',' << kernel_count[1]
                                  << "],\"kernel_first\":[" << first[0] << ',' << first[1]
                                  << "],\"kernel_last\":[" << last[0] << ',' << last[1] << "]}\n";
                        ++records; ++records_total; line.clear();
                    }
                }
                d.clk = 1; d.eval();
            };
            d.host_run = 1;
            uint64_t cycles = 0;
            for (; cycles < 1000000000ull && quiet < 512; ++cycles) {
                edge(true); if (records == opts.at("jobs")) ++quiet;
            }
            coherent_require(quiet == 512 && line.empty() && !running, "coherent benchmark timed out/incomplete UART");
            coherent_require(primary_entries == 1 && secondary_stops == (opts.at("workers") == 2 ? opts.at("jobs") : 0), "unexpected core lifecycle");
            d.host_run = 0;
            for (unsigned i = 0; !d.stopped && i < 1000000; ++i) edge(false);
            coherent_require(d.stopped && !d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations, "warm stop did not drain/flush");
            std::ofstream snapshot(ram_prefix+".boot"+std::to_string(boot)+".ram", std::ios::binary | std::ios::trunc);
            coherent_require(bool(snapshot), "cannot write RAM evidence");
            for (unsigned i = 0; i < memory.size(); ++i) {
                d.host_ram_addr = i*4; edge(false); edge(false);
                coherent_require(d.host_ram_rdata == memory[i], "warm stop lost/invented acknowledged RAM data");
                for (unsigned b = 0; b < 4; ++b) snapshot.put(char(d.host_ram_rdata >> (8*b)));
            }
            snapshot.close(); coherent_require(bool(snapshot), "RAM evidence write failed");
            std::cout << "ASTERSTOP {\"boot\":" << boot << ",\"jobs\":" << records << ",\"ram_bytes\":65536,\"stores\":" << stores
                      << ",\"lifetime_retired\":[" << lifetime[0] << ',' << lifetime[1] << "]}\n";
        }
        std::cout << "PASS: coherent benchmark boots=" << opts.at("boots") << " jobs=" << records_total
                  << "; exact 14-counter/hart windows, independent full outputs, retained RAM\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
