// Simulation of the Aster core's board design (rtl/soc/aster_core_pynq.sv;
// milestone 18.7's feasibility run) driven through its AXI4-Lite port as the
// ARM side drives it on the PYNQ-Z1 (scripts/aster_board.py): the program is
// written into main memory while the core is held in reset, the tohost
// address set, the core started, and the run followed until the first
// full-word store to tohost or, for a CPU kernel, until its record line ends
// on the console after its window closed; then the counters are read. One
// status line, as the board's, for scripts/aster_board.py to compare with the
// CPU shell's run of the same program:
//   BOARD <PASS|FAIL ...|TIMEOUT> cycles=<n> retired=<n> window_cycles=<n>
//         window_retired=<n> tohost=<hex> tohost_cycles=<n> tohost_retired=<n>
//         console_bytes=<n>
//
//     tb_aster_core_pynq +bin=<file> [+tohost=<hex>] [+kernel] [+console=<file>] [+max_cycles=<n>]
#include "Vaster_core_pynq.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

Vaster_core_pynq* dut = nullptr;
std::uint64_t ticks = 0;

std::string plusarg(const char* name) {
    const std::string match = Verilated::commandArgsPlusMatch(name);
    const std::string prefix = std::string("+") + name + "=";
    return match.rfind(prefix, 0) == 0 ? match.substr(prefix.size()) : (match.empty() ? "" : "1");
}

void tick() {
    dut->aclk = 0; dut->eval();
    dut->aclk = 1; dut->eval();
    ++ticks;
}

// One AXI4-Lite write, address and data presented together.
void axi_write(std::uint32_t address, std::uint32_t data) {
    dut->s_axi_awaddr = address; dut->s_axi_awvalid = 1;
    dut->s_axi_wdata = data; dut->s_axi_wstrb = 0xF; dut->s_axi_wvalid = 1;
    dut->s_axi_bready = 1;
    bool aw = false, w = false;
    for (int guard = 0; guard < 1000; ++guard) {
        dut->aclk = 0; dut->eval();
        const bool aw_now = dut->s_axi_awvalid && dut->s_axi_awready;
        const bool w_now = dut->s_axi_wvalid && dut->s_axi_wready;
        const bool b_now = dut->s_axi_bvalid;
        dut->aclk = 1; dut->eval(); ++ticks;
        if (aw_now) { aw = true; dut->s_axi_awvalid = 0; }
        if (w_now) { w = true; dut->s_axi_wvalid = 0; }
        if (aw && w && b_now) { dut->s_axi_bready = 0; return; }
    }
    throw std::runtime_error("AXI write timed out");
}

std::uint32_t axi_read(std::uint32_t address) {
    dut->s_axi_araddr = address; dut->s_axi_arvalid = 1; dut->s_axi_rready = 1;
    for (int guard = 0; guard < 1000; ++guard) {
        dut->aclk = 0; dut->eval();
        const bool ar_now = dut->s_axi_arvalid && dut->s_axi_arready;
        const bool r_now = dut->s_axi_rvalid;
        const std::uint32_t data = dut->s_axi_rdata;
        dut->aclk = 1; dut->eval(); ++ticks;
        if (ar_now) dut->s_axi_arvalid = 0;
        if (r_now) { dut->s_axi_rready = 0; return data; }
    }
    throw std::runtime_error("AXI read timed out");
}

std::uint64_t read64(std::uint32_t address) {
    return axi_read(address) | (std::uint64_t(axi_read(address + 4)) << 32);
}

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (plusarg("bin").empty()) { std::cerr << "+bin=<file> required\n"; return 2; }
    std::ifstream file(plusarg("bin"), std::ios::binary);
    std::vector<char> image((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (image.empty() || image.size() > 128 * 1024) { std::cerr << "bad image\n"; return 2; }
    const std::uint32_t tohost = plusarg("tohost").empty() ? 0 : std::uint32_t(std::stoul(plusarg("tohost"), nullptr, 16));
    const bool kernel = !plusarg("kernel").empty();
    const std::uint64_t max_cycles = plusarg("max_cycles").empty() ? 30'000'000 : std::stoull(plusarg("max_cycles"));

    dut = new Vaster_core_pynq;
    dut->aresetn = 0;
    for (int i = 0; i < 8; ++i) tick();
    dut->aresetn = 1;
    tick();
    if (axi_read(0x3F040) != 0x41535452u) { std::cerr << "bad magic\n"; return 2; }
    // Load the image (zero-padded to a word) and set tohost.
    image.resize((image.size() + 3) & ~std::size_t(3), 0);
    for (std::size_t i = 0; i < image.size(); i += 4) {
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        axi_write(std::uint32_t(i), word);
    }
    for (std::size_t i = 0; i < image.size(); i += 4) {   // read back a few words
        if (i % 4096 != 0) continue;
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        if (axi_read(std::uint32_t(i)) != word) { std::cerr << "image read-back mismatch at " << i << "\n"; return 2; }
    }
    axi_write(0x3F030, tohost);
    axi_write(0x3F000, 1);

    // Follow the run, as the board script polls it.
    std::string console;
    std::string result = "TIMEOUT";
    std::uint32_t seen = 0;
    while (true) {
        for (int i = 0; i < 2000; ++i) tick();
        const std::uint32_t status = axi_read(0x3F004);
        const std::uint32_t count = axi_read(0x3F008);
        while (seen < count) {
            const std::uint32_t word = axi_read(0x30000 + (seen & 0xFFC));
            console += char((word >> (8 * (seen & 3))) & 0xFF);
            ++seen;
        }
        if (status & 8u) {
            const std::uint32_t value = axi_read(0x3F034);
            result = axi_read(0x3F04C) != 0xFu ? "FAIL (partial tohost store)"
                     : value == 1u ? "PASS" : "FAIL test=" + std::to_string(value >> 1);
            break;
        }
        if (kernel && (status & 4u)) {
            const std::size_t at = console.find("\nA");
            const std::size_t line = console[0] == 'A' ? 0 : (at == std::string::npos ? std::string::npos : at + 1);
            if (line != std::string::npos && console.find('\n', line) != std::string::npos) {
                const std::string record = console.substr(line, console.find('\n', line) - line);
                result = record.find(",status=PASS,") != std::string::npos ? "PASS" : "FAIL (kernel record)";
                break;
            }
        }
        if (read64(0x3F010) > max_cycles) break;
    }
    const std::uint64_t cycles = read64(0x3F010), retired = read64(0x3F018);
    const std::uint64_t window_cycles = read64(0x3F020), window_retired = read64(0x3F028);
    const std::uint32_t value = axi_read(0x3F034);
    const std::uint64_t tohost_cycles = read64(0x3F038), tohost_retired = read64(0x3F050);
    axi_write(0x3F000, 0);
    if (!plusarg("console").empty()) std::ofstream(plusarg("console")) << console;
    char hex[16];
    std::snprintf(hex, sizeof hex, "%x", value);
    std::cout << "BOARD " << result << " cycles=" << cycles << " retired=" << retired
              << " window_cycles=" << window_cycles << " window_retired=" << window_retired
              << " tohost=" << hex << " tohost_cycles=" << tohost_cycles << " tohost_retired=" << tohost_retired
              << " console_bytes=" << console.size() << "\n";
    delete dut;
    return result == "PASS" ? 0 : 1;
}
