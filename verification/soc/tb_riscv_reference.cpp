#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include "../common/coherent_record.h"
#include <iostream>
#include <map>
#include <set>

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        std::map<std::string,uint64_t> opts;
        std::map<unsigned,uint32_t> cases;
        std::string test;
        for (int i = 1; i < argc; ++i) {
            const std::string key = argv[i];
            if (key.rfind("+", 0) == 0) continue;
            coherent_require(i+1 < argc, "missing reference option"); const std::string value = argv[++i];
            if (key == "--case") {
                auto colon = value.find(':'); coherent_require(colon != std::string::npos, "bad case PC");
                auto number = coherent_number(value.substr(0,colon)), pc = coherent_number(value.substr(colon+1));
                coherent_require(number >= 2 && number <= UINT32_MAX && pc <= 65532 && !(pc & 3), "invalid case number/PC");
                coherent_require(cases.emplace(number, pc).second, "duplicate case");
            } else if (key == "--test") { coherent_require(test.empty(), "duplicate test"); test = value; }
            else coherent_require(opts.emplace(key, coherent_number(value)).second, "duplicate option");
        }
        coherent_require(opts.size() == 5 && opts.count("--hart") && opts.count("--status") && opts.count("--begin") &&
            opts.count("--end") && opts.count("--halt") && !cases.empty(), "missing/unknown reference options");
        const unsigned hart = opts.at("--hart");
        coherent_require(opts.at("--hart") < 2 && opts.at("--status") <= UINT32_MAX && !(opts.at("--status") & 3), "invalid physical hart/signature");
        coherent_require(opts.at("--begin") < opts.at("--end") && opts.at("--end") <= 65536 &&
                         opts.at("--halt") <= 65532 && !(opts.at("--halt") & 3), "invalid reference code bounds");
        for (const auto& c : cases) coherent_require(c.second >= opts.at("--begin") && c.second < opts.at("--end"), "case outside original test body");
        const unsigned signature = opts.at("--status"), signature_index = (signature-0x10000000)/4;
        coherent_require(signature >= 0x10008000+hart*0x4000 && uint64_t(signature)+16 <= 0x1000c000+hart*0x4000, "bad signature address");
        const std::map<std::string,unsigned> operations = {{"amoadd_w",0},{"amoswap_w",1},{"amoxor_w",4},{"amoor_w",8},
            {"amoand_w",12},{"amomin_w",16},{"amomax_w",20},{"amominu_w",24},{"amomaxu_w",28},{"lrsc",31}};
        coherent_require(operations.count(test), "unknown upstream test");
        Vaster_coherent_soc d;
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; d.clk = 0; d.eval(); d.clk = 1; d.eval();
        coherent_require(d.stopped, "reference initial stop failed");
        std::array<uint32_t, 16384> memory; memory.fill(0xa5a5a5a5);
        for (unsigned boot = 1; boot <= 2; ++boot) {
            std::map<unsigned,unsigned> visited, atomic;
            std::array<unsigned,2> body_retired{};
            bool cleared = false, completed = false;
            unsigned halted = 0, stores = 0;
            auto edge = [&]() {
                d.clk = 0; d.eval();
                coherent_require(!d.hart_trap && !d.fault_valid && !d.uart_tx_valid, "unexpected reference trap/UART");
                for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) {
                    const auto pc = d.retired_pc[h];
                    if (pc >= opts.at("--begin") && pc < opts.at("--end")) {
                        ++body_retired[h]; coherent_require(h == hart, "wrong physical hart ran upstream body");
                    }
                    if (h == hart) {
                        for (const auto& c : cases) if (pc == c.second) ++visited[c.first];
                        if ((d.retired_insn[h] & 127) == 0x2f) ++atomic[d.retired_insn[h] >> 27];
                        if (completed && pc == opts.at("--halt")) ++halted;
                    }
                }
                if (d.store_commit) {
                    coherent_require(d.store_addr >= 0x10000000 && d.store_addr < 0x10010000 && d.store_mask, "invalid observed store");
                    auto& value = memory[(d.store_addr-0x10000000)/4];
                    for (unsigned b = 0; b < 4; ++b) if (d.store_mask & (1u << b))
                        value = (value & ~(255u << (8*b))) | (d.store_data & (255u << (8*b)));
                    ++stores;
                    if (d.store_addr == signature) {
                        coherent_require(d.store_owner == hart && d.store_mask == 15 && !completed, "misrouted/duplicate completion");
                        if (value == 0) { coherent_require(!cleared, "duplicate signature initialization"); cleared = true; }
                        else {
                            coherent_require(cleared, "stale/uninitialized PASS signature");
                            coherent_require(value == 1, "upstream case failed: "+std::to_string(value >> 1));
                            completed = true;
                        }
                    }
                }
                d.clk = 1; d.eval();
            };
            d.host_run = 1;
            unsigned cycle = 0;
            for (; cycle < 10000000 && halted < 8; ++cycle) edge();
            coherent_require(completed && halted == 8 && body_retired[hart] > cases.size(), "upstream test timed out");
            for (const auto& c : cases) coherent_require(visited[c.first] == 1, "skipped/duplicate original test case retirement");
            coherent_require(memory[signature_index+1] == cases.rbegin()->first && memory[signature_index+2] == hart &&
                memory[signature_index+3] == 0xa57e6001, "wrong final upstream case/hart/signature");
            if (test == "lrsc") coherent_require(atomic[0] == 2 && atomic[2] >= 1025 && atomic[3] >= 1028, "missing actual upstream LR/SC execution");
            else coherent_require(atomic.size() == 1 && atomic[operations.at(test)] >= 2, "missing actual upstream AMO execution");
            d.host_run = 0;
            for (unsigned i = 0; !d.stopped && i < 1000000; ++i) edge();
            coherent_require(d.stopped && !d.reservations && !d.fabric_busy && !d.flush_active, "unsafe reference warm stop");
            for (unsigned i = 0; i < memory.size(); ++i) {
                d.host_ram_addr = i*4; edge(); edge();
                coherent_require(d.host_ram_rdata == memory[i], "upstream warm-stop RAM disagrees with architectural history");
            }
            std::cout << "PASS: pinned RV32UA test=" << test << " hart=" << hart << " boot=" << boot
                      << " cases=" << cases.size() << " body_retired=" << body_retired[hart] << " cycles=" << cycle
                      << " stores=" << stores << " retained_ram=65536\n";
        }
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
