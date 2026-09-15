#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}

class Bench {
public:
    Vaster_coherent_soc d;
    std::mt19937 rng{0x7a5e9u};
    std::string line;
    std::vector<std::string> records;
    std::string ram_dump;
    std::uint64_t device_transactions = 0;
    std::uint64_t npu_transactions = 0;

    Bench() {
        d.resetn = 0; d.host_run = 0; d.boot_we = 0; d.boot_addr = 0;
        d.boot_wdata = 0; d.boot_wstrb = 0; d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; tick(); require(d.stopped, "Phase 10 benchmark did not reach initial STOPPED state");
        d.host_run = 1;
    }

    void tick() {
        d.clk = 0; d.uart_tx_ready = (rng() % 7u) != 0u; d.eval();
        require(!d.hart_trap && !d.fault_valid, "Phase 10 benchmark trapped on an actual core");
        if (d.backing_valid && d.backing_device && d.backing_ready) {
            require(d.backing_addr >= 0x10000000u && d.backing_addr < 0x10008000u,
                    "benchmark device traffic escaped shared RAM");
            ++device_transactions;
            if (d.npu_busy) ++npu_transactions;
        }
        if (d.uart_tx_valid && d.uart_tx_ready) {
            const char value = char(d.uart_tx_data);
            if (value == '\n') {
                if (line.rfind("ASTERBENCH,version=8,", 0) == 0) {
                    std::cout << line << '\n';
                    records.push_back(line);
                } else if (line.find("XE BENCH") == 0) {
                    throw std::runtime_error("benchmark firmware reported failure");
                }
                line.clear();
            } else {
                line += value;
                require(line.size() <= 262144u, "AsterBench v8 UART record exceeded bound");
            }
        }
        d.clk = 1; d.eval();
    }

    std::uint32_t read_word(std::uint16_t address) {
        d.host_ram_addr = address; tick(); tick();
        require(d.stopped && !d.backing_valid && !d.store_commit, "RAM dump raced STOPPED state");
        return d.host_ram_rdata;
    }

    void stop_and_dump() {
        d.host_run = 0;
        unsigned cycles = 0;
        while (!d.stopped && cycles++ < 1000000u) tick();
        require(d.stopped && cycles < 1000000u, "Phase 10 benchmark STOP deadlocked");
        require(!d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations,
                "Phase 10 benchmark did not quiesce all ownership state");
        if (ram_dump.empty()) return;
        std::ofstream output(ram_dump, std::ios::binary | std::ios::trunc);
        require(output.good(), "could not open requested Phase 10 RAM dump");
        for (std::uint32_t address = 0; address < 65536u; address += 4u) {
            const std::uint32_t value = read_word(static_cast<std::uint16_t>(address));
            output.put(char(value)); output.put(char(value >> 8));
            output.put(char(value >> 16)); output.put(char(value >> 24));
        }
        require(output.good(), "Phase 10 RAM dump was incomplete");
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Bench bench;
        std::string method;
        unsigned expected_records = 0;
        for (int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument.rfind("+ram_dump=", 0) == 0) bench.ram_dump = argument.substr(10u);
            else if (argument == "--method" && i + 1 < argc) method = argv[++i];
            else if (argument == "--records" && i + 1 < argc) expected_records = unsigned(std::stoul(argv[++i]));
        }
        require(!method.empty() && expected_records >= 1u, "missing --method/--records options");
        for (unsigned cycle = 0; cycle < 400000000u && bench.records.size() < expected_records; ++cycle) bench.tick();
        require(bench.records.size() == expected_records, "AsterBench v8 did not emit the requested records");
        if (method == "npu")
            require(bench.npu_transactions > 0u && bench.device_transactions > 0u,
                    "AsterBench v8 NPU method observed no coherent device traffic");
        else
            require(bench.device_transactions == 0u, "AsterBench v8 observed unexpected device traffic");
        bench.stop_and_dump();
        std::cout << "ASTERSTOP,records=" << bench.records.size()
                  << ",device_transactions=" << bench.device_transactions
                  << ",npu_transactions=" << bench.npu_transactions << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
