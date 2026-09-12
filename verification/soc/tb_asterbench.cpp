#include "Vaster_minimal.h"
#include "verilated.h"

#include <cstdlib>
#include <iostream>
#include <string>

static void tick(Vaster_minimal& dut) {
    dut.clk = 0;
    dut.eval();
    dut.clk = 1;
    dut.eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vaster_minimal dut;
    constexpr const char* record_prefix =
        "ASTERBENCH,version=1,name=memcpy,bytes=256,status=PASS,";
    std::string serial_output;

    dut.rst_n = 0;
    for (int cycle = 0; cycle < 3; ++cycle)
        tick(dut);

    dut.rst_n = 1;
    for (int cycle = 0; cycle < 30000; ++cycle) {
        tick(dut);
        if (dut.uart_tx_valid) {
            const char character = static_cast<char>(dut.uart_tx_data);
            serial_output.push_back(character);
            std::cout << character << std::flush;
            const std::size_t record_start = serial_output.find(record_prefix);
            const std::size_t record_end = serial_output.find('\n', record_start);
            if (record_start != std::string::npos &&
                record_end != std::string::npos) {
                const std::string record = serial_output.substr(
                    record_start, record_end - record_start);
                const bool has_cycles =
                    record.find(",cycles=0x0000000000000000") == std::string::npos;
                const bool has_retired =
                    record.find(",retired=0x0000000000000000") == std::string::npos;
                const bool has_memory = record.find(
                    ",memory_transactions=0x0000000000000000") == std::string::npos;
                const bool has_future_zeroes =
                    record.find(",cache_accesses=0x0000000000000000") !=
                        std::string::npos &&
                    record.find(",cache_misses=0x0000000000000000") !=
                        std::string::npos &&
                    record.find(",dma_bytes=0x0000000000000000") !=
                        std::string::npos &&
                    record.find(",accelerator_cycles=0x0000000000000000") !=
                        std::string::npos;
                if (has_cycles && has_retired && has_memory && has_future_zeroes) {
                    std::cout << "PASS: AsterBench emitted comparable counters\n";
                    return EXIT_SUCCESS;
                }
                std::cerr << "\nFAIL: invalid AsterBench counter record: "
                          << record << "\n";
                return EXIT_FAILURE;
            }
        }
        if (dut.trap) {
            std::cerr << "\nFAIL: PicoRV32 trapped during AsterBench\n";
            return EXIT_FAILURE;
        }
    }

    std::cerr << "\nFAIL: timed out waiting for AsterBench; received: "
              << serial_output << "\n";
    return EXIT_FAILURE;
}
