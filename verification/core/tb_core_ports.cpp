// Phase 18 two-port CPU shell: a CPU with the Aster core's port protocol
// (docs/cpu.md §5) on one unified synchronous memory at 0x8000_0000, with an
// RVFI trace for lockstep against Spike (scripts/lockstep.py). Any top built
// with Verilator `--prefix Vcore_ports` that has these ports can be the DUT:
// the Aster core, or PicoRV32 through shell_picorv32_ports.sv.
//
// Memory (docs/cpu.md §5): each port accepts a request at a clock edge when
// valid && ready and answers it `+latency` cycles later (1 or 2; default 2, the
// Aster core's two-stage memory), valid for one cycle, in order; separate
// instruction and data banks serve a fetch and a data access in the same
// cycle. Requests are pipelined: up to `+max_inflight` (default 2) per port are
// in flight, and ready is low when that many would remain after this cycle's
// response. A data-port error is signalled on d_rsp_error in the cycle after
// the request's acceptance, whatever its data latency (the address decode is
// registered at acceptance); i_rsp_error travels with the instruction word.
// Outside those cycles both error signals carry garbage, so a core that
// samples them at the wrong time fails. Instruction fetches are served from
// the main region only: a fetch from anywhere else, the io page included,
// answers with i_rsp_error and does not read memory.
// Stall mode (+stall_seed) adds random ready-low cycles and 0-2 extra response
// cycles per access, per port, keeping responses in order. Response data is
// garbage outside a response.
//
// Checks, besides the trace: each retired store must match, in word address,
// byte mask and data, the oldest data-port write the memory accepted and not
// yet matched (so a store whose bus write differs from its RVFI record fails),
// and no accepted write may be left unmatched at the end. The run ends when
// the store to tohost retires.
//
// Plusargs: +bin=<file> +tohost=<hex> [+trace=<file>] [+max_cycles=<n>]
//           [+stall_seed=<n>] [+mem_bytes=<hex>] [+latency=<1|2>] [+max_inflight=<n>]
//           [+signature=<file> +sig_begin=<hex> +sig_end=<hex>]
//           [+corrupt_write=<n>]  (self-test: the n-th accepted write reaches
//                                   memory with bit 0 of each byte flipped, as a
//                                   core whose bus write differs from its RVFI
//                                   record would; the store check must fail)
//           [+duplicate_tohost_write] (self-test: the write to tohost is
//                                   accepted twice, as a core that issues a store
//                                   twice would; the stray-write check must fail)
//
//           [+io_page] [+console=<file>] [+kernel_end]
//
// Memory regions, console, and the measurement window: shell_common.h.
// The DUT's RVFI outputs are sampled after the rising edge, so they must be
// registered (as riscv-formal requires), not combinational.
// Output: "SHELL <status> cycles=<n> retired=<n> [window_cycles=<n>
// window_retired=<n>]", where status is PASS,
// FAIL test=<n>, FAIL (partial tohost store), FAIL (kernel record), TRAP, TIMEOUT, BUS_ERROR,
// STORE_MISMATCH, STRAY_WRITE or UNSUPPORTED_OP.
#include "Vcore_ports.h"
#include "verilated.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <iostream>
#include <random>
#include <string>
#include <vector>

#include "shell_common.h"
#include "shell_ports.h"

using shell::plusarg;

namespace {
constexpr int kLoad = 0, kStore = 1;

struct Write {
    std::uint32_t word;
    std::uint32_t mask;
    std::uint32_t data;
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
    const bool stall = !plusarg("stall_seed").empty();
    std::mt19937 rng(stall ? std::stoul(plusarg("stall_seed")) : 0u);
    std::mt19937 garbage(0x5eed1234u);
    const std::uint32_t mem_bytes =
        plusarg("mem_bytes").empty() ? shell::kDefaultBytes : std::stoul(plusarg("mem_bytes"), nullptr, 16);
    if (mem_bytes == 0 || mem_bytes % 4) { std::cerr << "+mem_bytes must be a nonzero multiple of 4\n"; return 2; }
    const bool duplicate_tohost_write = Verilated::commandArgsPlusMatch("duplicate_tohost_write")[0] != '\0';
    const int latency = plusarg("latency").empty() ? 2 : std::stoi(plusarg("latency"));
    const std::size_t max_inflight = plusarg("max_inflight").empty() ? 2 : std::stoul(plusarg("max_inflight"));
    if (latency < 1 || latency > 2 || max_inflight < 1) {
        std::cerr << "+latency must be 1 or 2 and +max_inflight at least 1\n";
        return 2;
    }

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

    Vcore_ports d;
    d.clk = 0; d.resetn = 0;
    d.i_req_ready = 0; d.i_rsp_valid = 0; d.i_rsp_data = 0; d.i_rsp_error = 0;
    d.d_req_ready = 0; d.d_rsp_valid = 0; d.d_rsp_rdata = 0; d.d_rsp_error = 0;
    for (int i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    d.clk = 0; d.eval();
    d.resetn = 1;

    const std::uint64_t corrupt_write =
        plusarg("corrupt_write").empty() ? 0 : std::stoull(plusarg("corrupt_write"));
    std::uint64_t accepted_writes = 0;
    shell::Port iport, dport;
    bool d_error_next = false;              // d_rsp_error for the request accepted at the last edge
    bool d_error_cycle = false;             // a data request was accepted at the last edge
    std::deque<Write> writes;               // accepted data-port writes not yet matched to a retired store
    std::uint64_t cycles = 0, retired = 0;
    std::string result = "TIMEOUT";
    bool stop = false;
    while (!stop && cycles < max_cycles) {
        // Low phase: drive responses and readiness for this cycle.
        d.i_rsp_valid = iport.responding();
        d.i_rsp_data = iport.responding() ? iport.owed.front().data : std::uint32_t(garbage());
        d.i_rsp_error = iport.responding() ? iport.owed.front().error : garbage() & 1u;
        d.d_rsp_valid = dport.responding();
        d.d_rsp_rdata = dport.responding() ? dport.owed.front().data : std::uint32_t(garbage());
        d.d_rsp_error = d_error_cycle ? d_error_next : garbage() & 1u;
        d.i_req_ready = iport.remaining() < max_inflight && !(stall && rng() % 4 == 0);
        d.d_req_ready = dport.remaining() < max_inflight && !(stall && rng() % 4 == 0);
        d.eval();
        const bool i_accept = d.i_req_valid && d.i_req_ready;
        const bool d_accept = d.d_req_valid && d.d_req_ready;
        const std::uint32_t i_addr = std::uint32_t(d.i_req_addr) << 2;
        const std::uint32_t d_addr = d.d_req_addr, d_wdata = d.d_req_wdata, d_be = d.d_req_be;
        const int d_op = d.d_req_op;

        d.clk = 1; d.eval();
        ++cycles;
        // Responses delivered in the cycle before this edge are consumed; others age.
        iport.advance();
        dport.advance();
        d_error_next = false;
        d_error_cycle = d_accept;
        if (i_accept) {
            const bool error = !memory.executable(i_addr);
            iport.accept(latency, stall ? int(rng() % 3) : 0,
                         error ? std::uint32_t(garbage()) : memory.read(i_addr), error);
        }
        if (d_accept) {
            const bool error = !memory.contains(d_addr);
            std::uint32_t rdata = std::uint32_t(garbage());
            d_error_next = error;
            if (d_op != kLoad && d_op != kStore) { result = "UNSUPPORTED_OP"; stop = true; }
            else if (error) { result = "BUS_ERROR"; stop = true; }
            else if (d_op == kStore) {
                const std::uint32_t written = ++accepted_writes == corrupt_write ? d_wdata ^ 0x01010101u : d_wdata;
                memory.write(d_addr, written, d_be);
                writes.push_back({d_addr & ~3u, d_be, written});
                if (duplicate_tohost_write && (d_addr & ~3u) == tohost) writes.push_back({d_addr & ~3u, d_be, written});
            } else {
                rdata = memory.read(d_addr);
            }
            dport.accept(latency, stall ? int(rng() % 3) : 0, rdata, error);
        }
        if (d.rvfi_valid && !stop) {
            if (!d.rvfi_trap) ++retired;
            if (trace)
                std::fprintf(trace, "%llu %08x %08x %u %u %08x %08x %x %x %08x %08x\n",
                             (unsigned long long)d.rvfi_order, d.rvfi_pc_rdata, d.rvfi_insn, d.rvfi_trap,
                             d.rvfi_rd_addr, d.rvfi_rd_wdata, d.rvfi_mem_addr, d.rvfi_mem_rmask,
                             d.rvfi_mem_wmask, d.rvfi_mem_rdata, d.rvfi_mem_wdata);
            if (!d.rvfi_trap && d.rvfi_mem_wmask) {
                const std::uint32_t mask = d.rvfi_mem_wmask;
                std::uint32_t bytes = 0;
                for (int lane = 0; lane < 4; ++lane)
                    if (mask & (1u << lane)) bytes |= 0xffu << (8 * lane);
                if (writes.empty() || writes.front().word != (d.rvfi_mem_addr & ~3u) ||
                    writes.front().mask != mask || ((writes.front().data ^ d.rvfi_mem_wdata) & bytes)) {
                    result = "STORE_MISMATCH";
                    stop = true;
                } else {
                    writes.pop_front();
                }
                if (!stop && observer.store(d.rvfi_mem_addr, mask, d.rvfi_mem_wdata, cycles, retired, result))
                    stop = true;
            }
        }
        if (d.trap && !stop) { result = "TRAP"; stop = true; }
        d.clk = 0; d.eval();
    }
    if (result == "PASS" && !writes.empty()) result = "STRAY_WRITE";
    if (trace) std::fclose(trace);
    if (observer.console) std::fclose(observer.console);
    if (result == "PASS") {
        if (const std::string error = shell::dump_signature(memory); !error.empty()) { std::cerr << error << "\n"; return 2; }
    }
    std::cout << "SHELL " << result << " cycles=" << cycles << " retired=" << retired << observer.window() << "\n";
    return result == "PASS" ? 0 : 1;
}
