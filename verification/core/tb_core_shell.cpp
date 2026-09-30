// Phase 18 CPU shell: one CPU, one unified synchronous SRAM at 0x8000_0000, a
// tohost stop word, and an RVFI retirement trace for lockstep comparison
// against Spike (scripts/lockstep.py).
//
// Memory timing: the SRAM latches an address (and performs a write) at a clock
// edge and returns read data in the following cycle. PicoRV32 drives it from
// its look-ahead port (mem_la_*), which presents the next transfer one cycle
// before mem_valid, so the data is ready in the cycle mem_valid is high: this
// is PicoRV32's best case with a synchronous SRAM (the v1 SoC's sync1 model
// adds a wait state). In stall mode the handshake is delayed by 0-3 random
// cycles. Between responses mem_rdata carries garbage, so a core that samples
// it at the wrong time fails.
//
// The run ends when the store to tohost retires in the RVFI stream, so the
// trace always ends with that store; its value is 1 for pass or
// (test << 1) | 1 for fail.
//
// Plusargs: +bin=<flat binary at the memory base> +tohost=<hex address>
//           [+trace=<file>] [+max_cycles=<n>] [+stall_seed=<n>] [+mem_bytes=<hex>]
//           [+signature=<file> +sig_begin=<hex> +sig_end=<hex>]
// With +signature, a passing run writes the words from sig_begin to sig_end in
// Spike's +signature-granularity=4 format (one little-endian word per line).
// Output: one line "SHELL <status> cycles=<n> retired=<n>", where status is
// PASS, FAIL test=<n>, TRAP, TIMEOUT, BUS_ERROR or LA_MISMATCH.
#include "Vshell_picorv32.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <random>
#include <string>
#include <vector>

namespace {
constexpr std::uint32_t kBase = 0x80000000u;
// 96 KiB by default (the v2 SRAM; env/link.ld and Spike -m0x80000000:0x18000).
// riscv-arch-test programs need more (+mem_bytes=200000, arch_env/link.ld).
constexpr std::uint32_t kDefaultBytes = 0x18000u;

std::string plusarg(const char* name) {
    const char* value = Verilated::commandArgsPlusMatch(name);
    std::string text = value ? value : "";
    const std::string prefix = std::string("+") + name + "=";
    return text.rfind(prefix, 0) == 0 ? text.substr(prefix.size()) : "";
}

struct Memory {
    explicit Memory(std::uint32_t size) : bytes(size, 0) {}
    std::vector<std::uint8_t> bytes;
    bool contains(std::uint32_t address) const { return address >= kBase && address - kBase < bytes.size(); }
    std::uint32_t read(std::uint32_t address) const {
        const std::uint32_t o = (address & ~3u) - kBase;
        return bytes[o] | (bytes[o + 1] << 8) | (bytes[o + 2] << 16) | (std::uint32_t(bytes[o + 3]) << 24);
    }
    void write(std::uint32_t address, std::uint32_t data, std::uint32_t strobe) {
        const std::uint32_t o = (address & ~3u) - kBase;
        for (int lane = 0; lane < 4; ++lane)
            if (strobe & (1u << lane)) bytes[o + lane] = std::uint8_t(data >> (8 * lane));
    }
};
}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const std::string bin = plusarg("bin");
    const std::string tohost_text = plusarg("tohost");
    if (bin.empty() || tohost_text.empty()) {
        std::cerr << "usage: +bin=<file> +tohost=<hex> [+trace=<file>] [+max_cycles=<n>] [+stall_seed=<n>]\n";
        return 2;
    }
    const std::uint32_t tohost = std::stoul(tohost_text, nullptr, 16);
    const std::uint64_t max_cycles = plusarg("max_cycles").empty() ? 50000000ull : std::stoull(plusarg("max_cycles"));
    const std::string stall_text = plusarg("stall_seed");
    const bool stall = !stall_text.empty();
    std::mt19937 rng(stall ? std::stoul(stall_text) : 0u);
    std::mt19937 garbage(0x5eed1234u);

    const std::uint32_t mem_bytes =
        plusarg("mem_bytes").empty() ? kDefaultBytes : std::stoul(plusarg("mem_bytes"), nullptr, 16);
    Memory memory(mem_bytes);
    std::ifstream image(bin, std::ios::binary);
    std::vector<char> data((std::istreambuf_iterator<char>(image)), std::istreambuf_iterator<char>());
    if (!image.good() && !image.eof()) { std::cerr << "cannot read " << bin << "\n"; return 2; }
    if (data.size() > mem_bytes) { std::cerr << "image exceeds shell memory\n"; return 2; }
    std::copy(data.begin(), data.end(), memory.bytes.begin());

    std::FILE* trace = nullptr;
    if (!plusarg("trace").empty() && !(trace = std::fopen(plusarg("trace").c_str(), "w"))) {
        std::cerr << "cannot write trace " << plusarg("trace") << "\n";
        return 2;
    }

    Vshell_picorv32 d;
    d.clk = 0; d.resetn = 0; d.mem_ready = 0; d.mem_rdata = 0;
    for (int i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    d.clk = 0; d.eval();
    d.resetn = 1;

    std::uint64_t cycles = 0, retired = 0;
    std::uint32_t sram_q = 0, sram_addr = 0;  // SRAM output register and the address it holds
    bool pending = false;                      // an SRAM access awaits its mem_valid handshake
    int wait = 0;
    std::string result = "TIMEOUT";
    bool stop = false;
    while (!stop && cycles < max_cycles) {
        // Low phase: answer the transfer whose SRAM access happened at an earlier edge.
        const bool ready = d.mem_valid && pending && wait == 0;
        if (d.mem_valid && pending && wait > 0) --wait;
        if (ready && d.mem_addr != sram_addr) { result = "LA_MISMATCH"; break; }
        d.mem_ready = ready;
        d.mem_rdata = ready && !d.mem_wstrb ? sram_q : std::uint32_t(garbage());
        d.eval();
        const bool la_read = d.mem_la_read, la_write = d.mem_la_write;
        const std::uint32_t la_addr = d.mem_la_addr, la_wdata = d.mem_la_wdata, la_wstrb = d.mem_la_wstrb;

        d.clk = 1; d.eval();
        ++cycles;
        if (ready) pending = false;
        if (la_read || la_write) {  // the SRAM samples the look-ahead port at this edge
            if (!memory.contains(la_addr)) { result = "BUS_ERROR"; break; }
            if (la_write) memory.write(la_addr, la_wdata, la_wstrb);
            else sram_q = memory.read(la_addr);
            sram_addr = la_addr & ~3u;
            pending = true;
            wait = stall ? int(rng() % 4u) : 0;
        }
        if (d.rvfi_valid) {
            if (!d.rvfi_trap) ++retired;
            if (trace)
                std::fprintf(trace, "%llu %08x %08x %u %u %08x %08x %x %x %08x %08x\n",
                             (unsigned long long)d.rvfi_order, d.rvfi_pc_rdata, d.rvfi_insn, d.rvfi_trap,
                             d.rvfi_rd_addr, d.rvfi_rd_wdata, d.rvfi_mem_addr, d.rvfi_mem_rmask,
                             d.rvfi_mem_wmask, d.rvfi_mem_rdata, d.rvfi_mem_wdata);
            if (!d.rvfi_trap && d.rvfi_mem_wmask && (d.rvfi_mem_addr & ~3u) == tohost) {
                // The environments report with a full-word store; anything else is not a pass.
                const std::uint32_t code = d.rvfi_mem_wdata;
                result = d.rvfi_mem_wmask != 0xf ? "FAIL (partial tohost store)"
                         : code == 1             ? "PASS"
                                                 : "FAIL test=" + std::to_string(code >> 1);
                stop = true;
            }
        }
        if (d.trap && !stop) { result = "TRAP"; stop = true; }
        d.clk = 0; d.eval();
    }
    if (trace) std::fclose(trace);
    if (result == "PASS" && !plusarg("signature").empty()) {
        const std::uint32_t begin = std::stoul(plusarg("sig_begin"), nullptr, 16);
        const std::uint32_t end = std::stoul(plusarg("sig_end"), nullptr, 16);
        if (!memory.contains(begin) || (end > begin && !memory.contains(end - 1))) {
            std::cerr << "signature outside shell memory\n";
            return 2;
        }
        std::FILE* signature = std::fopen(plusarg("signature").c_str(), "w");
        if (!signature) {
            std::cerr << "cannot write signature " << plusarg("signature") << "\n";
            return 2;
        }
        for (std::uint32_t a = begin; a < end; a += 4) {
            std::uint32_t word = 0;  // bytes past end_signature print as zero, as in Spike
            for (std::uint32_t lane = 0; lane < 4 && a + lane < end; ++lane)
                word |= std::uint32_t(memory.bytes[a + lane - kBase]) << (8 * lane);
            std::fprintf(signature, "%08x\n", word);
        }
        std::fclose(signature);
    }
    std::cout << "SHELL " << result << " cycles=" << cycles << " retired=" << retired << "\n";
    return result == "PASS" ? 0 : 1;
}
