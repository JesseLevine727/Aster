#include "Vaster_pynq_z1.h"

#include <cstdint>
#include <iostream>
#include <string>

#include "verilated.h"

namespace {
// The DUT UART runs at 31.25 MHz, while this testbench samples the top-level
// 125 MHz board clock. Four board-clock cycles correspond to one core-clock
// cycle, so each serial bit spans 271 * 4 board-clock samples.
constexpr unsigned kBaudDivisor = 271 * 4;
constexpr unsigned kMaxCycles = 300000;

class UartDecoder {
public:
    void sample(bool level) {
        switch (state_) {
        case State::Idle:
            if (!level) {
                state_ = State::Data;
                countdown_ = kBaudDivisor + kBaudDivisor / 2;
                bit_index_ = 0;
                byte_ = 0;
            }
            break;
        case State::Data:
            if (countdown_ != 0) {
                --countdown_;
            } else {
                if (level)
                    byte_ |= static_cast<std::uint8_t>(1u << bit_index_);
                if (bit_index_ == 7) {
                    state_ = State::Stop;
                } else {
                    ++bit_index_;
                }
                countdown_ = kBaudDivisor - 1;
            }
            break;
        case State::Stop:
            if (countdown_ != 0) {
                --countdown_;
            } else {
                if (!level)
                    failed_ = true;
                text_.push_back(static_cast<char>(byte_));
                state_ = State::Idle;
            }
            break;
        }
    }

    bool failed() const { return failed_; }
    const std::string &text() const { return text_; }

private:
    enum class State { Idle, Data, Stop };
    State state_ = State::Idle;
    unsigned countdown_ = 0;
    unsigned bit_index_ = 0;
    std::uint8_t byte_ = 0;
    bool failed_ = false;
    std::string text_;
};
} // namespace

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    auto top = std::make_unique<Vaster_pynq_z1>();
    UartDecoder decoder;

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

    for (unsigned cycle = 0; cycle < kMaxCycles && decoder.text().size() < 17; ++cycle) {
        top->sysclk = 1;
        top->eval();
        top->sysclk = 0;
        top->eval();
        decoder.sample(top->uart_tx);
    }

    if (decoder.failed() || decoder.text() != "Hello from Aster\n") {
        std::cerr << "UART decode mismatch: '" << decoder.text() << "'\n";
        return 1;
    }

    std::cout << "PASS: PYNQ-Z1 UART loopback simulation\n";
    return 0;
}
