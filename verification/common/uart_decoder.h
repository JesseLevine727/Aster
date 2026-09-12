#pragma once

#include <cstdint>
#include <string>

// Independent midpoint sampler for 8-N-1 serial output. One call per clock.
class SerialDecoder {
public:
    explicit SerialDecoder(unsigned clocks_per_bit) : period_(clocks_per_bit) {}
    void sample(bool level) {
        if (state_ == Idle) {
            if (!level) {
                state_ = Data;
                remaining_ = period_ + period_ / 2;
                bit_ = 0;
                byte_ = 0;
            }
        } else if (--remaining_ == 0) {
            if (state_ == Stop) {
                if (!level) failed_ = true;
                text_ += static_cast<char>(byte_);
                state_ = Idle;
            } else {
                if (level) byte_ |= static_cast<std::uint8_t>(1u << bit_);
                if (++bit_ == 8) state_ = Stop;
            }
            remaining_ = period_;
        }
    }
    bool failed() const { return failed_; }
    const std::string& text() const { return text_; }
private:
    enum { Idle, Data, Stop } state_ = Idle;
    unsigned period_, remaining_ = 0, bit_ = 0;
    std::uint8_t byte_ = 0;
    bool failed_ = false;
    std::string text_;
};
