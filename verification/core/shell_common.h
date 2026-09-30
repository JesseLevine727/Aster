// Shared parts of the Phase 18 CPU shells (tb_core_shell.cpp, tb_core_ports.cpp):
// the memory, the observer of retired stores (tohost, console, measurement
// window), and the signature dump. Both shells use this file, so a program is
// measured the same way on PicoRV32 and on the Aster core.
//
// Memory: the main region at 0x8000_0000 (+mem_bytes, default 96 KiB) and,
// with +io_page, the SoC's 64 KiB register page at 0x2000_0000 as plain memory
// (the CPU kernels of docs/cpu.md §7 run unchanged against it; Spike maps the
// same page with -m). In that page the performance counter's cycle word is a
// deterministic stand-in, the same as Spike's aster_clock device
// (verification/core/spike/aster_clock.cc): each 32-bit read of 0x2000_3000
// returns the previous value plus 1,000,000 (starting at 1,000,000), and
// 0x2000_3004 reads 0, so the kernels' timers behave identically in both.
// Only word loads of those two words are defined: a sub-word load reads the
// page's plain bytes in Spike but applies the rule here, and lockstep reports
// the difference.
//
// Observer (fed each retired, non-trapping store from the RVFI stream):
// - tohost: a full-word store ends the run, 1 = pass, (test << 1) | 1 = fail;
// - console (+console=<file>): each store to the UART TX word 0x2000_0000
//   appends its low byte to the file;
// - measurement window: a store of 1 to a window-control word (the SoC's
//   performance-counter control 0x2000_3038 or the coherent-counter control
//   0x2000_3080) opens the window and a store of 2 closes it; the cycle and
//   retired counts at those two retirements are reported as window_cycles and
//   window_retired;
// - kernel end (+kernel_end): a CPU kernel prints its AsterBench record after
//   closing its window and then spins; the run ends at the retirement of the
//   console store of the newline that ends the first line beginning with 'A'
//   after the window closed, as PASS if that line holds ",status=PASS," and
//   as "FAIL (kernel record)" otherwise (the runner checks the record in full).
//   scripts/lockstep.py cuts Spike's stream at the same store.
#ifndef ASTER_SHELL_COMMON_H
#define ASTER_SHELL_COMMON_H

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "verilated.h"

namespace shell {

constexpr std::uint32_t kBase = 0x80000000u;
constexpr std::uint32_t kDefaultBytes = 0x18000u;
constexpr std::uint32_t kIoBase = 0x20000000u;
constexpr std::uint32_t kIoBytes = 0x10000u;
constexpr std::uint32_t kConsole = 0x20000000u;
constexpr std::uint32_t kClockLow = 0x20003000u, kClockHigh = 0x20003004u, kClockStep = 1000000u;
constexpr std::uint32_t kWindowControls[] = {0x20003038u, 0x20003080u};

inline std::string plusarg(const char* name) {
    const char* value = Verilated::commandArgsPlusMatch(name);
    std::string text = value ? value : "";
    const std::string prefix = std::string("+") + name + "=";
    return text.rfind(prefix, 0) == 0 ? text.substr(prefix.size()) : "";
}

inline bool plusflag(const char* name) { return Verilated::commandArgsPlusMatch(name)[0] != '\0'; }

struct Memory {
    Memory(std::uint32_t size, bool io_page) : main(size, 0), io(io_page ? kIoBytes : 0, 0) {}
    std::vector<std::uint8_t> main;
    std::vector<std::uint8_t> io;
    std::uint32_t clock = 0;

    std::uint8_t* byte(std::uint32_t address) {
        if (address >= kBase && address - kBase < main.size()) return &main[address - kBase];
        if (address >= kIoBase && address - kIoBase < io.size()) return &io[address - kIoBase];
        return nullptr;
    }
    bool contains(std::uint32_t address) { return byte(address & ~3u) != nullptr; }
    // Instructions are fetched from the main region only (never the io page).
    bool executable(std::uint32_t address) { return address >= kBase && address - kBase < main.size(); }
    // One read per load: the clock words advance on every read.
    std::uint32_t read(std::uint32_t address) {
        if (!io.empty() && (address & ~3u) == kClockLow) return clock += kClockStep;
        if (!io.empty() && (address & ~3u) == kClockHigh) return 0;
        std::uint8_t* b = byte(address & ~3u);
        return b[0] | (b[1] << 8) | (b[2] << 16) | (std::uint32_t(b[3]) << 24);
    }
    void write(std::uint32_t address, std::uint32_t data, std::uint32_t mask) {
        std::uint8_t* b = byte(address & ~3u);
        for (int lane = 0; lane < 4; ++lane)
            if (mask & (1u << lane)) b[lane] = std::uint8_t(data >> (8 * lane));
    }
};

// Reads the flat binary into the main region; returns an error message or "".
inline std::string load_image(Memory& memory, const std::string& path) {
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (!file) return "cannot read " + path;
    std::vector<std::uint8_t> data;
    std::uint8_t buffer[65536];
    for (std::size_t n; (n = std::fread(buffer, 1, sizeof buffer, file)) > 0;) data.insert(data.end(), buffer, buffer + n);
    std::fclose(file);
    if (data.size() > memory.main.size()) return "image exceeds shell memory";
    std::copy(data.begin(), data.end(), memory.main.begin());
    return "";
}

struct Observer {
    std::uint32_t tohost = 0;
    std::FILE* console = nullptr;
    bool end_at_record = false;
    bool line_start = true, record_line = false;
    std::string record;
    bool window_open = false, window_closed = false;
    std::uint64_t open_cycles = 0, open_retired = 0, close_cycles = 0, close_retired = 0;

    // A retired store; returns true when it ends the run, with `result` set.
    bool store(std::uint32_t address, std::uint32_t mask, std::uint32_t data, std::uint64_t cycles,
               std::uint64_t retired, std::string& result) {
        const std::uint32_t word = address & ~3u;
        if (word == kConsole && (mask & 1u)) {
            const char character = char(data & 0xffu);
            if (console) std::fputc(character, console);
            if (line_start) record_line = window_closed && character == 'A';
            line_start = character == '\n';
            if (record_line) record += character;
            if (end_at_record && record_line && character == '\n') {
                result = record.find(",status=PASS,") != std::string::npos ? "PASS" : "FAIL (kernel record)";
                return true;
            }
        }
        for (std::uint32_t control : kWindowControls) {
            if (word != control || mask != 0xfu) continue;
            if (data == 1u && !window_open) { window_open = true; open_cycles = cycles; open_retired = retired; }
            if (data == 2u && window_open && !window_closed) {
                window_closed = true; close_cycles = cycles; close_retired = retired;
            }
        }
        if (word != tohost) return false;
        result = mask != 0xfu ? "FAIL (partial tohost store)"
                 : data == 1u  ? "PASS"
                               : "FAIL test=" + std::to_string(data >> 1);
        return true;
    }

    std::string window() const {
        if (!window_closed) return "";
        return " window_cycles=" + std::to_string(close_cycles - open_cycles) +
               " window_retired=" + std::to_string(close_retired - open_retired);
    }
};

// With +signature, writes begin_signature..end_signature in Spike's
// +signature-granularity=4 format; returns an error message or "".
inline std::string dump_signature(Memory& memory) {
    if (plusarg("signature").empty()) return "";
    const std::uint32_t begin = std::stoul(plusarg("sig_begin"), nullptr, 16);
    const std::uint32_t end = std::stoul(plusarg("sig_end"), nullptr, 16);
    if (!memory.contains(begin) || (end > begin && !memory.contains(end - 1))) return "signature outside shell memory";
    std::FILE* signature = std::fopen(plusarg("signature").c_str(), "w");
    if (!signature) return "cannot write signature " + plusarg("signature");
    for (std::uint32_t a = begin; a < end; a += 4) {
        std::uint32_t word = 0;  // bytes past end_signature print as zero, as in Spike
        for (std::uint32_t lane = 0; lane < 4 && a + lane < end; ++lane) word |= std::uint32_t(*memory.byte(a + lane)) << (8 * lane);
        std::fprintf(signature, "%08x\n", word);
    }
    std::fclose(signature);
    return "";
}

}  // namespace shell

#endif
