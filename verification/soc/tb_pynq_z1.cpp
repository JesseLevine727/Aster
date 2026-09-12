#include "Vaster_pynq_z1.h"
#include "../common/uart_decoder.h"

#include <cstdint>
#include <iostream>
#include <string>

#include "verilated.h"

namespace {
// The DUT UART runs at 31.25 MHz, while this testbench samples the top-level
// 125 MHz board clock. Four board-clock cycles correspond to one core-clock
// cycle, so each serial bit spans 271 * 4 board-clock samples.
constexpr unsigned kBaudDivisor = 271 * 4;
constexpr unsigned kMaxCycles = 20000000;

} // namespace

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    std::string expected = "Hello from Aster\n";
    bool benchmark = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--stress") {
            expected = "UART STRESS BEGIN\n";
            for (unsigned index = 0; index < 1024; ++index)
                expected += static_cast<char>('!' + index % 90);
            expected += "\nUART STRESS PASS\n";
        } else if (std::string(argv[i]) == "--bench") {
            benchmark = true;
            expected.clear();
        }
    }
    auto top = std::make_unique<Vaster_pynq_z1>();

    for (unsigned boot = 0; boot < 2; ++boot) {
    SerialDecoder decoder(kBaudDivisor);

    top->sysclk = 0;
    top->reset_btn = 1;
    top->eval();
    for (unsigned cycle = 0; cycle < 10; ++cycle) {
        top->sysclk = 1;
        top->eval();
        top->sysclk = 0;
        top->eval();
    }
    top->reset_btn = 0;

    unsigned completed_cycle = 0;
    for (unsigned cycle = 0; cycle < kMaxCycles; ++cycle) {
        top->sysclk = 1;
        top->eval();
        top->sysclk = 0;
        top->eval();
        decoder.sample(top->uart_tx);
        const bool complete = benchmark ? decoder.text().find('\n') != std::string::npos
                                        : decoder.text().size() >= expected.size();
        if (complete && completed_cycle == 0) completed_cycle = cycle;
        // Drain a full extra frame to catch duplicated/trailing output.
        if (completed_cycle && cycle > completed_cycle + kBaudDivisor * 12) break;
    }

    const bool match = benchmark
        ? decoder.text().rfind("ASTERBENCH,", 0) == 0 &&
          decoder.text().find(",status=PASS,") != std::string::npos &&
          decoder.text().find(",accelerator_cycles=0x0000000000000000\n") != std::string::npos
        : decoder.text() == expected;
    if (decoder.failed() || !match) {
        std::cerr << "UART decode mismatch: '" << decoder.text() << "'\n";
        return 1;
    }

    std::cout << "PASS: PYNQ-Z1 UART serial decode (" << decoder.text().size() << " bytes)\n";
    }
    return 0;
}
