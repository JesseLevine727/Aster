#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static void require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}

static std::map<std::string, std::string> parse(const std::string& line) {
    std::map<std::string, std::string> fields;
    size_t start = line.find(',');
    while (start != std::string::npos && start + 1 < line.size()) {
        size_t end = line.find(',', start + 1);
        std::string token = line.substr(start + 1, end == std::string::npos ? std::string::npos : end - start - 1);
        size_t equals = token.find('=');
        if (equals != std::string::npos) fields[token.substr(0, equals)] = token.substr(equals + 1);
        if (end == std::string::npos) break;
        start = end;
    }
    return fields;
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
    bool summary_pass = false;

    Bench() {
        d.resetn = 0; d.host_run = 0; d.boot_we = 0; d.boot_addr = 0;
        d.boot_wdata = 0; d.boot_wstrb = 0; d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; tick(); require(d.stopped, "MNIST inference did not reach initial STOPPED state");
        d.host_run = 1;
    }

    void handle(const std::string& text) {
        if (text.rfind("ASTERBENCH,version=9,", 0) == 0) {
            auto fields = parse(text);
            require(fields.count("status") && fields["status"] == "PASS", "v9 record is not PASS");
            require(fields.count("class") && fields.count("expected") && fields["class"] == fields["expected"],
                    "v9 record class differs from the expected class");
            require(fields.count("logit_match") && fields["logit_match"] == "1",
                    "v9 record logits differ from the reference");
            std::cout << text << '\n';
            records.push_back(text);
        } else if (text.rfind("MNIST INFER ", 0) == 0) {
            std::cout << text << '\n';
            summary_pass = text.find("MNIST INFER PASS") == 0;
            require(summary_pass, "MNIST inference summary is not PASS");
        } else if (text.find("MNIST INFER") == 0) {
            throw std::runtime_error("MNIST inference firmware reported failure");
        }
    }

    void tick() {
        d.clk = 0; d.uart_tx_ready = (rng() % 7u) != 0u; d.eval();
        require(!d.hart_trap && !d.fault_valid, "MNIST inference trapped on an actual core");
        if (d.backing_valid && d.backing_device && d.backing_ready) {
            require(d.backing_addr >= 0x10000000u && d.backing_addr < 0x10008000u,
                    "MNIST device traffic escaped shared RAM");
            ++device_transactions;
            if (d.npu_busy) ++npu_transactions;
        }
        if (d.uart_tx_valid && d.uart_tx_ready) {
            const char value = char(d.uart_tx_data);
            if (value == '\n') { handle(line); line.clear(); }
            else { line += value; require(line.size() <= 65536u, "MNIST UART line exceeded bound"); }
        }
        d.clk = 1; d.eval();
    }

    void stop_and_dump() {
        d.host_run = 0;
        unsigned cycles = 0;
        while (!d.stopped && cycles++ < 1000000u) tick();
        require(d.stopped && cycles < 1000000u, "MNIST inference STOP deadlocked");
        require(!d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations,
                "MNIST inference did not quiesce");
        if (ram_dump.empty()) return;
        std::ofstream output(ram_dump, std::ios::binary | std::ios::trunc);
        require(output.good(), "could not open MNIST RAM dump");
        for (std::uint32_t address = 0; address < 65536u; address += 4u) {
            d.host_ram_addr = static_cast<std::uint16_t>(address);
            tick(); tick();
            const std::uint32_t value = d.host_ram_rdata;
            output.put(char(value)); output.put(char(value >> 8));
            output.put(char(value >> 16)); output.put(char(value >> 24));
        }
        require(output.good(), "MNIST RAM dump was incomplete");
    }
};

int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc, argv);
        Bench bench;
        for (int i = 1; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument.rfind("+ram_dump=", 0) == 0) bench.ram_dump = argument.substr(10u);
        }
        for (unsigned cycle = 0; cycle < 400000000u && !bench.summary_pass; ++cycle) bench.tick();
        require(bench.summary_pass, "MNIST inference did not emit a PASS summary");
        require(bench.records.size() == 32u, "MNIST inference did not emit 32 image records");
        require(bench.npu_transactions > 0u && bench.device_transactions > 0u,
                "MNIST inference observed no NPU coherent device traffic");
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
