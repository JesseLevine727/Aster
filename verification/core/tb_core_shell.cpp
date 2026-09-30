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
//           [+io_page] [+console=<file>] [+kernel_end]
// With +signature, a passing run writes the words from sig_begin to sig_end in
// Spike's +signature-granularity=4 format (one little-endian word per line).
// Memory, console, and the measurement window: shell_common.h. PicoRV32's
// look-ahead port does not say whether a read is a fetch, so this shell cannot
// refuse fetches from the io page as the two-port shell does; a jump into the
// clock words would read differently here than in Spike, where they are not
// executable, and lockstep would report it.
// Output: one line "SHELL <status> cycles=<n> retired=<n>
// [window_cycles=<n> window_retired=<n>]", where status is PASS,
// FAIL test=<n>, FAIL (kernel record), TRAP, TIMEOUT, BUS_ERROR or LA_MISMATCH.
#include "Vshell_picorv32.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <iostream>
#include <random>
#include <string>

#include "shell_common.h"

using shell::plusarg;

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
        plusarg("mem_bytes").empty() ? shell::kDefaultBytes : std::stoul(plusarg("mem_bytes"), nullptr, 16);
    if (mem_bytes == 0 || mem_bytes % 4) { std::cerr << "+mem_bytes must be a nonzero multiple of 4\n"; return 2; }
    shell::Memory memory(mem_bytes, shell::plusflag("io_page"));
    if (const std::string error = shell::load_image(memory, bin); !error.empty()) { std::cerr << error << "\n"; return 2; }
    shell::Observer observer;
    observer.tohost = tohost;
    observer.end_at_record = shell::plusflag("kernel_end");
    if (!plusarg("console").empty() && !(observer.console = std::fopen(plusarg("console").c_str(), "w"))) {
        std::cerr << "cannot write console " << plusarg("console") << "\n";
        return 2;
    }

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
            if (!d.rvfi_trap && d.rvfi_mem_wmask &&
                observer.store(d.rvfi_mem_addr, d.rvfi_mem_wmask, d.rvfi_mem_wdata, cycles, retired, result))
                stop = true;
        }
        if (d.trap && !stop) { result = "TRAP"; stop = true; }
        d.clk = 0; d.eval();
    }
    if (trace) std::fclose(trace);
    if (observer.console) std::fclose(observer.console);
    if (result == "PASS") {
        if (const std::string error = shell::dump_signature(memory); !error.empty()) { std::cerr << error << "\n"; return 2; }
    }
    std::cout << "SHELL " << result << " cycles=" << cycles << " retired=" << retired << observer.window() << "\n";
    return result == "PASS" ? 0 : 1;
}
