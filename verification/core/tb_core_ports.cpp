// Phase 18 two-port CPU shell: a CPU with the Aster core's port protocol
// (docs/cpu.md §5) on one unified synchronous memory at 0x8000_0000, with an
// RVFI trace for lockstep against Spike (scripts/lockstep.py). Any top built
// with Verilator `--prefix Vcore_ports` that has these ports can be the DUT:
// the Aster core through shell_aster_ports.sv, or PicoRV32 through
// shell_picorv32_ports.sv — clk, resetn, a selftest[3:0] input (tied off
// unless the DUT has self-test mutants), trap, chk_i_redirect, the two ports,
// and the RVFI fields sampled below.
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
// and no accepted write may be left unmatched at the end (a pass, or a trap:
// no store younger than a trapping instruction may reach memory). The run
// ends when the store to tohost retires, or at a trap.
//
// Protocol checks (docs/cpu.md §4-§5; shell_ports.h):
// - I_REQ_UNSTABLE: a fetch presented and not accepted must be presented
//   unchanged in the next cycle unless the DUT raises chk_i_redirect in that
//   next cycle — the cycle whose request is a redirect's target, or in which
//   fetching has stopped (never the cycle in which an Execute redirect
//   resolves: its target is presented the cycle after);
// - D_REQ_UNSTABLE: a data request presented and not accepted must be
//   presented unchanged in the next cycle, always. §4 lets a flush withdraw
//   one, but a core that presents only when M1 can take never needs to: after
//   a request waits, M1 is empty, so no trap or interrupt is taken there;
// - D_REQ_MALFORMED: a data request's byte enables must be an aligned byte,
//   halfword or word consistent with its address;
// - D_INFLIGHT: never more than two data requests in flight — accepted and
//   not yet answered, stores included (visible only when +max_inflight is
//   above 2 and answers are late, +stall_seed);
// - RVFI_COMBINATIONAL: the RVFI outputs must be registered: a value sampled
//   after a rising edge may not change before the next one, when the shell
//   changes the responses and readiness.
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
//           [+selftest=<n>]      (self-test: drives the DUT's selftest input, which
//                                   makes the PicoRV32 adapter break one protocol
//                                   rule — 1 a store's data changes while waiting,
//                                   2 a fetch is withdrawn while waiting, 3 an RVFI
//                                   output follows an input combinationally, 4
//                                   malformed byte enables, 5 more than two data
//                                   requests in flight)
//           [+io_page] [+console=<file>] [+kernel_end]
//           [+retire_log=<file>]  (debugging: "order cycle pc" per retirement)
//           [+bus_error_traps]     (a data access outside memory is answered with
//                                   d_rsp_error and the run continues: the DUT must
//                                   trap on it; by default the run stops, BUS_ERROR)
//
// Memory regions, console, and the measurement window: shell_common.h.
// The DUT's RVFI outputs are sampled after the rising edge, so they must be
// registered (as riscv-formal requires), not combinational.
// Output: "SHELL <status> cycles=<n> retired=<n> [window_cycles=<n>
// window_retired=<n>] fetch_errors=<n>" (fetches answered with i_rsp_error),
// where status is PASS,
// FAIL test=<n>, FAIL (partial tohost store), FAIL (kernel record), TRAP, TIMEOUT, BUS_ERROR,
// STORE_MISMATCH, STRAY_WRITE, UNSUPPORTED_OP, I_REQ_UNSTABLE, D_REQ_UNSTABLE,
// D_REQ_MALFORMED, D_INFLIGHT or RVFI_COMBINATIONAL.
#include "Vcore_ports.h"
#include "verilated.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <iostream>
#include <random>
#include <string>
#include <tuple>
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

// The RVFI outputs the shell samples, for the registered-output check.
struct Rvfi {
    std::uint64_t order;
    std::uint32_t valid, insn, trap, pc, rd, rd_wdata, addr, rmask, wmask, rdata, wdata;
    auto tied() const { return std::tie(order, valid, insn, trap, pc, rd, rd_wdata, addr, rmask, wmask, rdata, wdata); }
    bool operator==(const Rvfi& other) const { return tied() == other.tied(); }
};

Rvfi sample(const Vcore_ports& d) {
    return {d.rvfi_order, d.rvfi_valid, d.rvfi_insn, d.rvfi_trap, d.rvfi_pc_rdata, d.rvfi_rd_addr,
            d.rvfi_rd_wdata, d.rvfi_mem_addr, d.rvfi_mem_rmask, d.rvfi_mem_wmask, d.rvfi_mem_rdata,
            d.rvfi_mem_wdata};
}
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
    const bool bus_error_traps = shell::plusflag("bus_error_traps");
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
    std::FILE* retire_log = nullptr;     // debugging aid: "order cycle pc" per retirement
    if (!plusarg("retire_log").empty() && !(retire_log = std::fopen(plusarg("retire_log").c_str(), "w"))) {
        std::cerr << "cannot write retire log " << plusarg("retire_log") << "\n";
        return 2;
    }

    Vcore_ports d;
    d.selftest = plusarg("selftest").empty() ? 0 : std::stoul(plusarg("selftest"));
    d.clk = 0; d.resetn = 0;
    d.i_req_ready = 0; d.i_rsp_valid = 0; d.i_rsp_data = 0; d.i_rsp_error = 0;
    d.d_req_ready = 0; d.d_rsp_valid = 0; d.d_rsp_rdata = 0; d.d_rsp_error = 0;
    for (int i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    d.clk = 0; d.eval();
    d.resetn = 1;

    const std::uint64_t corrupt_write =
        plusarg("corrupt_write").empty() ? 0 : std::stoull(plusarg("corrupt_write"));
    std::uint64_t accepted_writes = 0, fetch_errors = 0;
    shell::Port iport, dport;
    bool d_error_next = false;              // d_rsp_error for the request accepted at the last edge
    bool d_error_cycle = false;             // a data request was accepted at the last edge
    std::deque<Write> writes;               // accepted data-port writes not yet matched to a retired store
    std::uint64_t cycles = 0, retired = 0;
    std::string result = "TIMEOUT";
    bool stop = false;
    shell::StableCheck i_stable, d_stable;
    Rvfi registered{};
    bool sampled = false;
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

        // Protocol checks, on this cycle's settled request side.
        if (sampled && !(sample(d) == registered)) { result = "RVFI_COMBINATIONAL"; break; }
        const std::string i_violation =
            i_stable.cycle({bool(d.i_req_valid), i_addr, 0, 0, 0}, d.i_req_ready, d.chk_i_redirect);
        const std::string d_violation =
            d_stable.cycle({bool(d.d_req_valid), d_addr, d_op, d_wdata, d_be}, d.d_req_ready, false);
        if (!i_violation.empty()) { result = "I_REQ_UNSTABLE"; std::cerr << "instruction request " << i_violation << "\n"; break; }
        if (!d_violation.empty()) { result = "D_REQ_UNSTABLE"; std::cerr << "data request " << d_violation << "\n"; break; }
        if (d.d_req_valid && !shell::well_formed(d_addr, d_be)) { result = "D_REQ_MALFORMED"; break; }
        if (d_accept && dport.remaining() >= 2) { result = "D_INFLIGHT"; break; }

        d.clk = 1; d.eval();
        ++cycles;
        registered = sample(d);
        sampled = true;
        // Responses delivered in the cycle before this edge are consumed; others age.
        iport.advance();
        dport.advance();
        d_error_next = false;
        d_error_cycle = d_accept;
        if (i_accept) {
            const bool error = !memory.executable(i_addr);
            fetch_errors += error;
            iport.accept(latency, stall ? int(rng() % 3) : 0,
                         error ? std::uint32_t(garbage()) : memory.read(i_addr), error);
        }
        if (d_accept) {
            const bool error = !memory.contains(d_addr);
            std::uint32_t rdata = std::uint32_t(garbage());
            d_error_next = error;
            if (d_op != kLoad && d_op != kStore) { result = "UNSUPPORTED_OP"; stop = true; }
            else if (error) {
                // Not performed; answered with d_rsp_error. By default the run
                // stops here; with +bus_error_traps the DUT must trap on it.
                if (!bus_error_traps) { result = "BUS_ERROR"; stop = true; }
            }
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
            if (retire_log)
                std::fprintf(retire_log, "%llu %llu %08x\n", (unsigned long long)d.rvfi_order,
                             (unsigned long long)cycles, d.rvfi_pc_rdata);
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
    // A run that ends in a trap must not have let a younger store reach memory.
    if ((result == "PASS" || result == "TRAP") && !writes.empty()) result = "STRAY_WRITE";
    if (trace) std::fclose(trace);
    if (retire_log) std::fclose(retire_log);
    if (observer.console) std::fclose(observer.console);
    if (result == "PASS") {
        if (const std::string error = shell::dump_signature(memory); !error.empty()) { std::cerr << error << "\n"; return 2; }
    }
    std::cout << "SHELL " << result << " cycles=" << cycles << " retired=" << retired << observer.window()
              << " fetch_errors=" << fetch_errors << "\n";
    return result == "PASS" ? 0 : 1;
}
