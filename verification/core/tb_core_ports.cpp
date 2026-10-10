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
// With +long_stall as well, about one access in sixteen is answered 16-63
// cycles late (the long waits of cache misses behind a slow memory, 18.6).
//
// The L1 model (+cache_model; the Aster core with its caches, 18.6): the lines
// each cache holds, kept from its misses and the policy (docs/cpu.md §9, 18.6:
// direct-mapped 16-byte lines, 256 per cache at 4 KiB, the shell's CACHE_BYTES / 16 (20.5); the data cache write-through
// with no write-allocate, sc and the AMOs invalidating their line; fence.i
// invalidating the instruction cache), checked against every lookup's hit and
// every memory-side access the caches make: a miss refills exactly its line's
// four words, a store, atomic or I/O access goes to the memory side exactly
// once and unchanged, a hit or an error not at all — else CACHE_MISMATCH. The
// shell's memory is the cacheable main memory (a whole number of lines); its
// devices are the I/O windows. fetch_errors then also counts the fetches the
// instruction cache answers with an error itself (outside the memory). The
// protocol checks below then see the caches' memory side, and run again on the
// core's side (shell_aster_ports.sv's chk_core_* outputs); the load check
// follows the core's loads into the data cache (its lookups) instead of the
// refills.
//
// Stall mode (+stall_seed) adds random ready-low cycles and 0-2 extra response
// cycles per access, per port, keeping responses in order. Response data is
// garbage outside a response.
//
// Checks, besides the trace: each retired store must match, in word address,
// byte mask and data, the oldest data-port write the memory accepted and not
// yet matched (so a store whose bus write differs from its RVFI record fails,
// and so does a store performed twice or one younger than a trap), and no
// accepted write may be left unmatched at the end (a pass, or a DUT that
// stops on a trap). Loads likewise: each retired load must match, in word
// address and byte mask, the oldest load the memory accepted (without an
// error) and not yet matched, and none may be left over at the end — so a
// load performed twice, such as an I/O load repeated after an interrupt
// (docs/cpu.md §4 forbids it), fails (LOAD_MISMATCH, STRAY_READ). A CPU kernel's
// run ends mid-program, so up to three younger loads (in M1, M2 and W) may be
// accepted and not yet retired then. The run ends when the store to tohost retires, or when a
// DUT that stops on a trap (PicoRV32; the Aster core takes its traps) raises
// `trap`.
//
// Instruction fetches see a data-port write only once the memory has answered
// it (docs/cpu.md §5 orders accesses within a port, not across the two): a
// fetch accepted before that answer has been taken — up to and including the
// edge at which the core takes it — reads the word as it was. So code that
// rewrites instructions must use fence.i, and fence.i must wait for the
// writes' answers (18.4).
//
// Atomics (18.4; d_req_op 2 lr, 3 sc, 4-12 amoswap, add, xor, and, or, min,
// max, minu, maxu): each is one indivisible operation on the word. lr reads it
// and reserves it; sc writes only if the reservation holds that word, answers
// 0 (written) or 1, and always ends the reservation; an AMO reads the old
// value, writes the new one and answers the old. The reservation follows
// Spike's: the hart's own stores do not end it, an exception does (Spike ends
// its instruction step at one), an interrupt does not. Spike also ends it
// at its own step boundaries (every 5,000 instructions), which the core cannot
// know, so a run that compares with Spike passes Spike's sc outcomes
// (+sc_outcomes=<file>: S or F per sc, in program order; every sc the memory
// accepts is a right-path one, in order): the shell then answers each sc as
// Spike did, and fails (SC_MISMATCH) if Spike's sc succeeded where the shell's
// reservation did not hold, or if the counts differ.
//
// Interrupts (Aster core): the shell drives meip, mtip and msip, low unless a
// program uses the interrupt device (+irq_device): a word at 0x3000_0000 whose
// store sets the three lines, in mip's layout (bit 11 MEIP, 7 MTIP, 3 MSIP),
// `value >> 16` cycles after the cycle following its acceptance, and whose
// load returns them. With +irq_random=<seed> (which implies the device) the
// shell also raises MEIP or MSIP at random, about once per +irq_period cycles
// (default 20) while neither is high; a handler clears them with a store of 0.
// The shell's machine timer, as a SoC's: mtime counts once per cycle from 0
// (once per +timer_divider cycles, for ACT4's timer tests, which assume a timer
// slower than the core) and
// drives the core's mtime input, which its time and timeh read (docs/cpu.md
// §3). With +timer its registers are also mapped, in the CLINT layout Sail uses
// (for ACT4) — mtimecmp at 0x0200_4000, mtime at 0x0200_BFF8, 64 bits each as
// two words, mtimecmp starting at all ones — and MTIP is high while mtime >=
// mtimecmp, or while the interrupt device holds it. A write to the timer takes
// effect in the cycle it is answered (18.6), the latest a device may — or
// before a later access to the timer — so a core that reads `time` or enables
// interrupts before the write is answered sees the old state.
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
//   changes the responses and readiness;
// - PC_WDATA_MISMATCH: each retired record's rvfi_pc_wdata must be the next
//   record's rvfi_pc_rdata (riscv-formal's pc_fwd; the trace itself carries
//   only pc_rdata, so this is where the reported next PC is checked) — a trap
//   record's is its handler's address — except into a record marked
//   rvfi_intr after a record that is not a trap: an interrupt's handler entry,
//   counted as `interrupts`.
//
// Plusargs: +bin=<file> +tohost=<hex> [+trace=<file>] [+max_cycles=<n>]
//           [+stall_seed=<n> [+long_stall]] [+mem_bytes=<hex>] [+latency=<1|2>] [+max_inflight=<n>]
//           [+cache_model]                              (the Aster core with its L1, above)
//           [+cache_model_ignore_fencei] (self-test: the model keeps its lines through
//                                   fence.i; the cache model must fail)
//           [+signature=<file> +sig_begin=<hex> +sig_end=<hex>]
//           [+corrupt_write=<n>]  (self-test: the n-th accepted write reaches
//                                   memory with bit 0 of each byte flipped, as a
//                                   core whose bus write differs from its RVFI
//                                   record would; the store check must fail)
//           [+duplicate_tohost_write] (self-test: the write to tohost is
//                                   accepted twice, as a core that issues a store
//                                   twice would; the stray-write check must fail)
//           [+duplicate_read=<n>] (self-test: the n-th accepted load — with the
//                                   caches, the n-th looked up — is recorded
//                                   twice, as a core that performs a load twice
//                                   would; the load check must fail)
//           [+skip_load_check]     (self-test only: isolates the in-flight check,
//                                   which a core presenting accepted loads again
//                                   would otherwise fail on the load check first)
//
//           [+selftest=<n>]      (self-test: drives the DUT's selftest input, which
//                                   makes the PicoRV32 adapter break one protocol
//                                   rule — 1 a store's data changes while waiting,
//                                   2 a fetch is withdrawn while waiting, 3 an RVFI
//                                   output follows an input combinationally, 4
//                                   malformed byte enables, 5 more than two data
//                                   requests in flight, 6 a wrong rvfi_pc_wdata)
//           [+io_page] [+console=<file>] [+kernel_end] [+v1_devices]   (v1's devices, above)
//           [+remote_mp=<seed> | +remote_sb=<seed>] [+remote_area=<hex>] [+lazy_snoops]   (another master, above)
//           [+retire_log=<file>]  (debugging: "order cycle pc" per retirement)
//           [+bus_error_traps]     (a data access outside memory is answered with
//                                   d_rsp_error and the run continues: the DUT must
//                                   trap on it; by default the run stops, BUS_ERROR)
//           [+irq_device] [+irq_random=<seed>] [+irq_period=<n>] [+timer] [+timer_divider=<n>]   (above)
//           [+sc_outcomes=<file>]  (atomics, above)
//
// Memory regions, console, and the measurement window: shell_common.h.
// The DUT's RVFI outputs are sampled after the rising edge, so they must be
// registered (as riscv-formal requires), not combinational.
// Trace: one line per RVFI record, "order pc insn trap rd rd_wdata mem_addr
// rmask wmask rdata wdata", then a token c<csr>=<value> (hex) for each CSR the
// record writes (the DUT's rvfi_csr_wvalid/wdata, in the order of kCsrAddress)
// and "intr" if the record is marked rvfi_intr.
//
// Output: "SHELL <status> cycles=<n> retired=<n> [window_cycles=<n>
// window_retired=<n>] fetch_errors=<n> interrupts=<n>" (fetches answered with
// i_rsp_error; interrupt handler entries), where status is PASS,
// FAIL test=<n>, FAIL (partial tohost store), FAIL (kernel record), TRAP, TIMEOUT, BUS_ERROR,
// STORE_MISMATCH, STRAY_WRITE, UNSUPPORTED_OP, I_REQ_UNSTABLE, D_REQ_UNSTABLE,
// D_REQ_MALFORMED, D_INFLIGHT, RVFI_COMBINATIONAL, PC_WDATA_MISMATCH, LOAD_MISMATCH,
// STRAY_READ, SC_MISMATCH or CACHE_MISMATCH.
#include "Vcore_ports.h"
#include "verilated.h"

#include <algorithm>
#include <array>
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
constexpr int kLoad = 0, kStore = 1, kLr = 2, kSc = 3, kAmoFirst = 4, kAmoLast = 12;

// An AMO's new value (d_req_op 4-12).
std::uint32_t amo_value(int op, std::uint32_t old, std::uint32_t operand) {
    const std::int32_t a = std::int32_t(old), b = std::int32_t(operand);
    switch (op) {
        case 5: return old + operand;
        case 6: return old ^ operand;
        case 7: return old & operand;
        case 8: return old | operand;
        case 9: return a < b ? old : operand;
        case 10: return a > b ? old : operand;
        case 11: return old < operand ? old : operand;
        case 12: return old > operand ? old : operand;
        default: return operand;                       // 4: amoswap
    }
}
constexpr std::uint32_t kIrqDevice = 0x30000000u;
// The CSR of each rvfi_csr_wvalid/wdata entry (shell_aster_ports.sv).
constexpr std::uint32_t kCsrAddress[15] = {0x300, 0x310, 0x304, 0x344, 0x305, 0x340, 0x341, 0x342,
                                           0x343, 0x320, 0xb00, 0xb80, 0xb02, 0xb82, 0x301};

// The interrupt lines (meip, mtip, msip in mip's bit positions) and the
// program-visible device that sets them.
struct IrqLines {
    bool device = false, random = false;
    std::mt19937 rng{0};
    std::uint32_t period = 20;
    std::uint32_t level = 0, next_level = 0;
    long delay = -1;                         // cycles until next_level applies; -1 none pending
    void store(std::uint32_t value) { next_level = value & 0x888u; delay = long(value >> 16); }
    // At each edge, after this cycle's accesses.
    void edge() {
        if (delay == 0) level = next_level;
        if (delay >= 0) --delay;
        if (random && !(level & 0x808u) && rng() % period == 0) level |= rng() & 1u ? 0x800u : 0x008u;
    }
};

constexpr std::uint32_t kMtimecmp = 0x02004000u, kMtime = 0x0200BFF8u;

struct Timer {
    bool enabled = false;
    std::uint64_t mtime = 0, mtimecmp = ~std::uint64_t(0);
    std::uint32_t divider = 1, phase = 0;
    void tick() { if (++phase >= divider) { phase = 0; ++mtime; } }
    bool decodes(std::uint32_t address) const {   // its registers are mapped with +timer
        const std::uint32_t word = address & ~3u;
        return enabled && (word == kMtimecmp || word == kMtimecmp + 4 || word == kMtime || word == kMtime + 4);
    }
    std::uint64_t& reg(std::uint32_t word) { return word - (word & 4u) == kMtime ? mtime : mtimecmp; }
    std::uint32_t load(std::uint32_t address) {
        const std::uint32_t word = address & ~3u;
        return std::uint32_t(reg(word) >> (word & 4u ? 32 : 0));
    }
    void store(std::uint32_t address, std::uint32_t data, std::uint32_t be) {
        const std::uint32_t word = address & ~3u;
        std::uint64_t& r = reg(word);
        for (int lane = 0; lane < 4; ++lane)
            if (be & (1u << lane)) {
                const int shift = (word & 4u ? 32 : 0) + 8 * lane;
                r = (r & ~(std::uint64_t(0xff) << shift)) | (std::uint64_t((data >> (8 * lane)) & 0xffu) << shift);
            }
    }
    bool mtip() const { return enabled && mtime >= mtimecmp; }
};

// v1's SoC devices (+v1_devices, with +io_page; 18.6's firmware regression),
// for v1 firmware built with the ported runtime, modelled on
// rtl/peripherals/aster_timer.sv, rtl/peripherals/aster_interrupt_controller.sv
// and the hart-control page (docs/phase5.md):
// - the timer at 0x2000_1000: TIME counts every cycle from reset; COMPARE;
//   CONTROL (lane 0: bit 0 enables, bit 1 clears the pending flag); STATUS
//   {enabled, pending}; ABI 1; CLOCK_HZ 31,250,000. Pending is set at the edge
//   where an enabled TIME reaches COMPARE; a clear at that edge wins.
// - hart control at 0x2000_2000, one hart: ID 0, HART_COUNT 1, STATUS bit 0
//   (hart 0 running), the two mailboxes; SECONDARY_RUN reads 0.
// - the interrupt controller at 0x2000_4000: PENDING latches the rising edges
//   of its sources (bit 0 the timer's pending flag); a store to RAISE sets bit 3
//   and one to PENDING clears the bits written (after the edges are latched);
//   ENABLE0/1; ACTIVE0/1 = PENDING & ENABLE0/1; ABI 1; SOURCES 4. Hart 0's line
//   (PENDING & ENABLE0) drives meip.
// - the coherent SoC's performance block (rtl/peripherals/aster_coherent_perf.sv)
//   at 0x2000_3000: counter 0 (0x00/0x04) counts cycles while running, the
//   others read 0; a store of 1 to 0x80 clears the counters and starts them,
//   2 freezes them, 4 resumes them; 0x80 reads running, 0x84 the ABI 4, 0x88
//   the clock.
// Register writes take effect at the edge, as in the RTL; like the interrupt
// device's, its accesses are checked against RVFI.
struct V1Devices {
    bool enabled = false;
    std::uint64_t time = 0, compare = 0;
    bool timer_on = false, timer_pending = false, source_q = false, perf_running = false;
    std::uint64_t perf_cycles = 0;
    std::uint32_t perf_command = 0;
    std::uint32_t pending = 0, enable0 = 0, enable1 = 0, mailbox[2] = {0, 0};
    // This cycle's register writes, applied at the edge.
    std::uint64_t next_compare = 0;
    bool command = false, command_on = false, command_clear = false, raise = false;
    std::uint32_t clear = 0;
    static constexpr std::uint32_t kClockHz = 31250000u;
    bool decodes(std::uint32_t address) const {
        const std::uint32_t word = address & ~3u;
        return enabled && ((word >= 0x20001000u && word < 0x20001020u) || (word >= 0x20002000u && word < 0x20002018u)
                           || (word >= 0x20004000u && word < 0x20004020u) || (word >= 0x20003000u && word < 0x20003070u)
                           || word == 0x20003080u || word == 0x20003084u || word == 0x20003088u);
    }
    std::uint32_t load(std::uint32_t address) const {
        switch (address & ~3u) {
            case 0x20001000u: return std::uint32_t(time);
            case 0x20001004u: return std::uint32_t(time >> 32);
            case 0x20001008u: return std::uint32_t(compare);
            case 0x2000100cu: return std::uint32_t(compare >> 32);
            case 0x20001014u: return (timer_on ? 2u : 0u) | (timer_pending ? 1u : 0u);
            case 0x20001018u: return 1u;
            case 0x2000101cu: return kClockHz;
            case 0x20002008u: return 1u;                       // HART_COUNT; ID (0x00) reads 0
            case 0x2000200cu: return 1u;                       // STATUS: hart 0 running
            case 0x20002010u: return mailbox[0];
            case 0x20002014u: return mailbox[1];
            case 0x20003000u: return std::uint32_t(perf_cycles);
            case 0x20003004u: return std::uint32_t(perf_cycles >> 32);
            case 0x20003080u: return perf_running ? 1u : 0u;
            case 0x20003084u: return 4u;
            case 0x20003088u: return kClockHz;
            case 0x20004000u: return enable0;
            case 0x20004004u: return enable1;
            case 0x20004008u: return pending;
            case 0x2000400cu: return pending & enable0;
            case 0x20004010u: return pending & enable1;
            case 0x20004018u: return 1u;
            case 0x2000401cu: return 4u;
            default: return 0u;
        }
    }
    void store(std::uint32_t address, std::uint32_t data, std::uint32_t be) {
        const std::uint32_t word = address & ~3u;
        auto merge = [&](std::uint32_t old) {
            for (int lane = 0; lane < 4; ++lane)
                if (be & (1u << lane)) old = (old & ~(0xffu << (8 * lane))) | (data & (0xffu << (8 * lane)));
            return old;
        };
        switch (word) {
            case 0x20001008u: next_compare = (next_compare & ~0xffffffffull) | merge(std::uint32_t(next_compare)); break;
            case 0x2000100cu: next_compare = (next_compare & 0xffffffffull)
                                             | (std::uint64_t(merge(std::uint32_t(next_compare >> 32))) << 32); break;
            case 0x20001010u: if (be & 1u) { command = true; command_on = data & 1u; command_clear = data & 2u; } break;
            case 0x20002010u: mailbox[0] = merge(mailbox[0]); break;
            case 0x20002014u: mailbox[1] = merge(mailbox[1]); break;
            case 0x20004000u: if (be & 1u) enable0 = data & 15u; break;
            case 0x20004004u: if (be & 1u) enable1 = data & 15u; break;
            case 0x20004008u: if (be & 1u) clear |= data & 15u; break;
            case 0x20004014u: if (be & 1u) raise = true; break;
            case 0x20003080u: if (be & 1u) perf_command = data & 0xffu; break;
            default: break;                                    // read-only or unused: ignored
        }
    }
    // At each edge, after this cycle's accesses.
    void edge() {
        if (!enabled) return;
        std::uint32_t next = pending | (timer_pending && !source_q ? 1u : 0u);
        if (raise) next |= 8u;
        next &= ~clear;
        source_q = timer_pending;
        pending = next;
        if (timer_on && time + 1 == compare) timer_pending = true;
        if (command) { timer_on = command_on; if (command_clear) timer_pending = false; }
        compare = next_compare;
        ++time;
        if (perf_command == 1u) { perf_cycles = 0; perf_running = true; }
        else if (perf_command == 2u) perf_running = false;
        else if (perf_command == 4u) perf_running = true;
        else if (perf_running) ++perf_cycles;
        command = raise = false;
        clear = perf_command = 0;
    }
    bool irq0() const { return (pending & enable0) != 0; }
};

// Another master (+remote_mp=<seed>, with +remote_area=<hex>; 18.6's coherence
// test, verification/core/coherence): it writes a message into the program's
// 16-word remote_area again and again — data words 1-15 set to the message's
// number n, one a cycle, then the flag word 0 set to n — after a random gap of
// 20-219 cycles before each, up to message 1,000. With the caches each write's
// line is presented on the data cache's snoop input in the cycle of the write
// (docs/cpu.md §9, 18.6), and the cache model applies it there. The memory
// performs the write in that cycle: a read accepted then or later sees it.
struct RemoteMaster {
    bool enabled = false;
    std::uint32_t area = 0, n = 1, word = 1;     // the next write: word `word` of message n
    long gap = 0;
    std::mt19937 rng{1};
    bool step(std::uint32_t& addr, std::uint32_t& value) {   // this cycle's write, if any
        if (!enabled || n > 1000) return false;
        if (gap > 0) { --gap; return false; }
        addr = area + 4 * word;
        value = n;
        if (word == 0) { ++n; word = 1; gap = 20 + long(rng() % 200); }
        else if (++word == 16) word = 0;
        return true;
    }
};

// Another master in a store-buffering litmus test (+remote_sb=<seed>, with
// +remote_area=<hex>; verification/core/coherence/remote_sb.S). The area's
// words: 0 GO and 4 X (written by the program), 8 Y, 12 DONE and 13 R2
// (written here), each group in its own line. Round k: when GO reads k, wait
// 0-23 cycles, write Y = k, read X in the next cycle (the write before the
// read, as a fence orders them), write R2 = the X read, then DONE = k. The
// program writes X = k, fences, reads Y, and fails if both reads missed the
// other's write (Y and X both below k), which RVWMO forbids. Each write's line
// is snooped in its cycle, as RemoteMaster's.
struct RemoteSb {
    bool enabled = false;
    std::uint32_t area = 0, k = 1, x = 0;
    int phase = 0;                               // 0 wait for GO, 1 delay, 2 Y, 3 read X, 4 R2, 5 DONE
    long delay = 0;
    std::mt19937 rng{1};
    // snoops_owed: its writes' snoops not yet presented; it reads X only once
    // they have been (it treats its write of Y as performed then).
    bool step(shell::Memory& memory, bool snoops_owed, std::uint32_t& addr, std::uint32_t& value) {
        if (!enabled) return false;
        switch (phase) {
            case 0: if (memory.read(area) == k) { phase = 1; delay = long(rng() % 24); } return false;
            case 1: if (delay-- > 0) return false; phase = 2; [[fallthrough]];
            case 2: phase = 3; addr = area + 32; value = k; return true;
            case 3: if (snoops_owed) return false; phase = 4; x = memory.read(area + 16); return false;
            case 4: phase = 5; addr = area + 52; value = x; return true;
            default: phase = 0; addr = area + 48; value = k++; return true;
        }
    }
};

// The L1 model (+cache_model, above).
struct L1Model {
    struct Access {
        std::uint32_t op, addr, be, data;
        bool refill = false;
    };
    bool enabled = false;
    bool ignore_fencei = false;                  // self-test: the model misses fence.i's invalidation
    std::uint32_t base = 0x80000000u, bytes = 0;
    // (20.5: each cache's capacity, the shell's CACHE_BYTES: 2048, 4096 by default, or 8192)
    std::uint32_t lines = 256, index_bits = 12;   // the lines, and the tag's lowest address bit
    std::vector<std::int64_t> itag, dtag;        // the line's tag, or -1
    std::deque<std::uint32_t> i_expect;          // refill fetches expected, by address
    std::deque<Access> d_expect;                 // memory-side data accesses expected
    std::uint64_t i_hits = 0, i_misses = 0, d_hits = 0, d_misses = 0;
    std::uint64_t i_errors = 0;                  // fetches the instruction cache answers with an error
    // The instruction cache's refill in progress: installed when its fourth word
    // arrives, unless fence.i invalidated the cache after the cycle of its miss
    // (a refill that starts after an invalidation reads the memory as fence.i
    // left it, and is installed).
    struct Refill {
        bool active = false, poisoned = false;
        std::uint32_t line = 0, answered = 0;
        std::uint64_t cycle = 0;
    } refill;
    // The data cache's refill in progress likewise: the line it replaces is gone
    // at its miss; its own is installed with its fourth word unless another
    // master's write to it was snooped after its miss (that word's cycle
    // included; a snoop in the miss's own cycle comes before its lookup here,
    // as in the cache, where the lookup already misses and the refill's reads
    // follow the write). d_answers: the memory side's data requests accepted and
    // not yet answered, true for a refill read.
    Refill drefill;
    std::deque<bool> d_answers;
    std::string error;
    void size(std::uint32_t cache_bytes) {
        lines = cache_bytes / 16u;
        index_bits = 0;
        while ((1u << index_bits) < cache_bytes) ++index_bits;
        itag.assign(lines, -1); dtag.assign(lines, -1);
    }
    L1Model() { size(4096u); }
    bool cacheable(std::uint32_t a) const { return a - base < bytes; }
    bool performs(std::uint32_t a) const { return cacheable(a) || io(a); }   // not an error
    static bool io(std::uint32_t a) {
        const std::uint32_t word = a & ~3u;
        return (a & ~0xFFFFu) == 0x20000000u || word == 0x30000000u || (a & ~7u) == 0x02004000u
            || (a & ~7u) == 0x0200BFF8u;
    }
    void fail(const std::string& what) { if (error.empty()) error = what; }
    void ilookup(std::uint32_t a, bool hit, std::uint64_t cycle) {
        if (!cacheable(a)) { ++i_errors; return; }   // answered with an error, nothing fetched
        const std::uint32_t index = (a >> 4) & (lines - 1u), tag = a >> index_bits;
        const bool resident = itag[index] == std::int64_t(tag);
        if (hit != resident) fail("instruction lookup " + hex(a) + (hit ? " hit, the line is absent" : " missed, the line is resident"));
        if (resident) { ++i_hits; return; }
        ++i_misses;
        for (std::uint32_t w = 0; w < 4; ++w) i_expect.push_back((a & ~15u) + 4 * w);
        refill = {true, false, a & ~15u, 0, cycle};  // the next lookup comes after the refill
    }
    void ianswer() {                                 // a refill word arrives
        if (!refill.active || ++refill.answered < 4) return;
        if (!refill.poisoned) itag[(refill.line >> 4) & (lines - 1u)] = refill.line >> index_bits;
        refill.active = false;
    }
    void dlookup(std::uint32_t op, std::uint32_t a, std::uint32_t be, std::uint32_t data, bool hit,
                 std::uint64_t cycle) {
        const std::uint32_t index = (a >> 4) & (lines - 1u), tag = a >> index_bits;
        if (!cacheable(a)) {
            if (hit) fail("data lookup " + hex(a) + " hit outside the cacheable memory");
            if (io(a)) d_expect.push_back({op, a, be, data});
            return;                                  // an error reaches nothing
        }
        const bool resident = dtag[index] == std::int64_t(tag);
        if (hit != resident) fail("data lookup " + hex(a) + (hit ? " hit, the line is absent" : " missed, the line is resident"));
        if (op == 0) {                               // a load: a hit, or a refill of its line
            if (resident) { ++d_hits; return; }
            ++d_misses;
            for (std::uint32_t w = 0; w < 4; ++w) d_expect.push_back({0, (a & ~15u) + 4 * w, 0xFu, 0, true});
            dtag[index] = -1;                        // replaced; installed with the last word
            drefill = {true, false, a & ~15u, 0, cycle};
            return;
        }
        d_expect.push_back({op, a, be, data});      // a store (no allocate) or an atomic
        if (op != 1 && op != 2) dtag[index] = -1;    // sc and the AMOs invalidate their line
    }
    void ifetch(std::uint32_t a) {
        if (i_expect.empty() || i_expect.front() != a) {
            fail("unexpected refill fetch " + hex(a) + (i_expect.empty() ? "" : ", expected " + hex(i_expect.front())));
            return;
        }
        i_expect.pop_front();
    }
    void daccess(std::uint32_t op, std::uint32_t a, std::uint32_t be, std::uint32_t data) {
        if (d_expect.empty()) { fail("unexpected memory-side access at " + hex(a)); return; }
        const Access want = d_expect.front();
        d_expect.pop_front();
        const std::uint32_t lanes = lane_mask(be);
        const bool writes = op != 0 && op != 2;      // store data, or an sc's or AMO's operand
        if (op != want.op || a != want.addr || be != want.be || (writes && (data & lanes) != (want.data & lanes)))
            fail("memory-side access op " + std::to_string(op) + " at " + hex(a) + ", expected op "
                 + std::to_string(want.op) + " at " + hex(want.addr));
        d_answers.push_back(want.refill);
    }
    void danswer() {                                 // the memory side answers a data request
        if (d_answers.empty()) { fail("a memory-side answer with nothing in flight"); return; }
        const bool refill_word = d_answers.front();
        d_answers.pop_front();
        if (!refill_word || !drefill.active || ++drefill.answered < 4) return;
        if (!drefill.poisoned) dtag[(drefill.line >> 4) & (lines - 1u)] = drefill.line >> index_bits;
        drefill.active = false;
    }
    void dsnoop(std::uint32_t line) {                // another master wrote this line
        const std::uint32_t index = (line >> 4) & (lines - 1u);
        if (dtag[index] == std::int64_t(line >> index_bits)) dtag[index] = -1;
        if (drefill.active && drefill.line == line) drefill.poisoned = true;
    }
    void fencei(std::uint64_t cycle) {
        if (ignore_fencei) return;
        std::fill(itag.begin(), itag.end(), -1);
        if (refill.active && cycle != refill.cycle) refill.poisoned = true;
    }
    static std::string hex(std::uint32_t v) {
        char text[16];
        std::snprintf(text, sizeof text, "0x%08x", v);
        return text;
    }
    static std::uint32_t lane_mask(std::uint32_t be) {
        std::uint32_t m = 0;
        for (int lane = 0; lane < 4; ++lane) if (be & (1u << lane)) m |= 0xffu << (8 * lane);
        return m;
    }
};

struct Write {
    std::uint32_t word;
    std::uint32_t mask;
    std::uint32_t data;
};

struct Read {
    std::uint32_t word;
    std::uint32_t mask;
};

// A data-port write not yet answered: what the fetch port still sees there.
struct Unanswered {
    std::uint64_t sequence;                  // the access's place in the data port's answer order
    std::uint32_t word;
    std::uint32_t mask;
    std::uint32_t old;
};

std::uint32_t lane_bytes(std::uint32_t mask) {
    std::uint32_t bytes = 0;
    for (int lane = 0; lane < 4; ++lane)
        if (mask & (1u << lane)) bytes |= 0xffu << (8 * lane);
    return bytes;
}

// The RVFI outputs the shell samples, for the registered-output check.
struct Rvfi {
    std::uint64_t order;
    std::uint32_t valid, insn, trap, pc, pc_next, rd, rd_wdata, addr, rmask, wmask, rdata, wdata, intr, csr_valid;
    std::array<std::uint32_t, 15> csr;
    auto tied() const {
        return std::tie(order, valid, insn, trap, pc, pc_next, rd, rd_wdata, addr, rmask, wmask, rdata, wdata, intr,
                        csr_valid, csr);
    }
    bool operator==(const Rvfi& other) const { return tied() == other.tied(); }
};

Rvfi sample(const Vcore_ports& d) {
    Rvfi r{d.rvfi_order, d.rvfi_valid, d.rvfi_insn, d.rvfi_trap, d.rvfi_pc_rdata, d.rvfi_pc_wdata, d.rvfi_rd_addr,
           d.rvfi_rd_wdata, d.rvfi_mem_addr, d.rvfi_mem_rmask, d.rvfi_mem_wmask, d.rvfi_mem_rdata,
           d.rvfi_mem_wdata, d.rvfi_intr, d.rvfi_csr_wvalid, {}};
    for (int i = 0; i < 15; ++i) r.csr[i] = d.rvfi_csr_wdata[i];
    return r;
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
    const bool long_stall = shell::plusflag("long_stall");
    if (long_stall && !stall) { std::cerr << "+long_stall needs +stall_seed\n"; return 2; }
    std::mt19937 rng(stall ? std::stoul(plusarg("stall_seed")) : 0u);
    std::mt19937 garbage(0x5eed1234u);
    const std::uint32_t mem_bytes =
        plusarg("mem_bytes").empty() ? shell::kDefaultBytes : std::stoul(plusarg("mem_bytes"), nullptr, 16);
    if (mem_bytes == 0 || mem_bytes % 4) { std::cerr << "+mem_bytes must be a nonzero multiple of 4\n"; return 2; }
    L1Model l1;
    l1.enabled = shell::plusflag("cache_model");
    l1.ignore_fencei = shell::plusflag("cache_model_ignore_fencei");
    l1.bytes = mem_bytes;
    if (l1.enabled && mem_bytes % 16) { std::cerr << "+cache_model needs whole 16-byte lines of memory\n"; return 2; }
    const bool duplicate_tohost_write = Verilated::commandArgsPlusMatch("duplicate_tohost_write")[0] != '\0';
    const bool bus_error_traps = shell::plusflag("bus_error_traps");
    const int latency = plusarg("latency").empty() ? 2 : std::stoi(plusarg("latency"));
    const std::size_t max_inflight = plusarg("max_inflight").empty() ? 2 : std::stoul(plusarg("max_inflight"));
    if (latency < 1 || latency > 2 || max_inflight < 1) {
        std::cerr << "+latency must be 1 or 2 and +max_inflight at least 1\n";
        return 2;
    }

    IrqLines irq;
    irq.random = !plusarg("irq_random").empty();
    irq.device = irq.random || shell::plusflag("irq_device");
    Timer timer;
    timer.enabled = shell::plusflag("timer");
    V1Devices v1;
    v1.enabled = shell::plusflag("v1_devices");
    RemoteMaster remote;
    RemoteSb remote_sb;
    struct Snoop { std::uint32_t line; long wait; };
    std::deque<Snoop> snoop_queue;                       // snoops owed to the data cache
    const bool lazy_snoops = shell::plusflag("lazy_snoops");
    std::mt19937 snoop_rng(7);
    if (!plusarg("remote_sb").empty()) {
        if (plusarg("remote_area").empty()) { std::cerr << "+remote_sb needs +remote_area\n"; return 2; }
        remote_sb.enabled = true;
        remote_sb.rng.seed(std::stoul(plusarg("remote_sb")));
        remote_sb.area = std::uint32_t(std::stoul(plusarg("remote_area"), nullptr, 16));
    }
    if (!plusarg("remote_mp").empty()) {
        if (plusarg("remote_area").empty()) { std::cerr << "+remote_mp needs +remote_area\n"; return 2; }
        remote.enabled = true;
        remote.rng.seed(std::stoul(plusarg("remote_mp")));
        remote.area = std::uint32_t(std::stoul(plusarg("remote_area"), nullptr, 16));
    }
    if (!plusarg("timer_divider").empty()) timer.divider = std::max(1ul, std::stoul(plusarg("timer_divider")));
    if (irq.random) irq.rng.seed(std::stoul(plusarg("irq_random")));
    if (!plusarg("irq_period").empty()) irq.period = std::max(1ul, std::stoul(plusarg("irq_period")));

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
    d.meip = 0; d.mtip = 0; d.msip = 0;
    for (int i = 0; i < 8; ++i) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
    d.clk = 0; d.eval();
    d.resetn = 1;
    if (l1.enabled) {                       // (20.5) the model's caches are the shell's: 2, 4 or 8 KiB
        if (d.cache_bytes != 2048u && d.cache_bytes != 4096u && d.cache_bytes != 8192u) {
            std::cerr << "+cache_model needs a shell with 2, 4 or 8 KiB caches (cache_bytes " << d.cache_bytes << ")\n";
            return 2;
        }
        l1.size(d.cache_bytes);
    }

    const std::uint64_t corrupt_write =
        plusarg("corrupt_write").empty() ? 0 : std::stoull(plusarg("corrupt_write"));
    const std::uint64_t duplicate_read =
        plusarg("duplicate_read").empty() ? 0 : std::stoull(plusarg("duplicate_read"));
    // A cache's refills are not the program's loads; the L1 model checks them.
    const bool load_check = !shell::plusflag("skip_load_check");
    std::uint64_t accepted_reads = 0;
    std::deque<Read> reads;                 // accepted loads not yet matched to a retired load
    // A load the memory side performs, for the load check — without the caches
    // (with them the check follows the data cache's lookups, below).
    auto memory_read = [&](std::uint32_t address, std::uint32_t be) {
        if (!l1.enabled) reads.push_back({address & ~3u, be});
    };
    std::deque<Unanswered> unanswered;      // data writes the fetch port does not see yet
    std::uint64_t data_accepted = 0, data_answered = 0;
    // The machine timer's writes take effect when they are answered (a device
    // has performed a write when it answers it: the latest it may), or before a
    // later access to the timer (a port's accesses take effect in order).
    struct TimerWrite { std::uint64_t sequence; std::uint32_t addr, data, be; };
    std::deque<TimerWrite> timer_writes;
    auto timer_catch_up = [&](std::uint64_t answered) {
        while (!timer_writes.empty() && timer_writes.front().sequence < answered) {
            timer.store(timer_writes.front().addr, timer_writes.front().data, timer_writes.front().be);
            timer_writes.pop_front();
        }
    };
    // A write performed at acceptance, kept from the fetch port until answered
    // (only writes to memory the core can fetch from: an io-page read has effects).
    auto write_data = [&](std::uint32_t address, std::uint32_t data, std::uint32_t mask) {
        if (memory.executable(address))
            unanswered.push_back({data_accepted, address & ~3u, mask, memory.read(address)});
        memory.write(address, data, mask);
    };
    auto fetch_word = [&](std::uint32_t address) {
        std::uint32_t value = memory.read(address);
        for (auto it = unanswered.rbegin(); it != unanswered.rend(); ++it)   // the oldest last
            if (it->word == (address & ~3u))
                value = (value & ~lane_bytes(it->mask)) | (it->old & lane_bytes(it->mask));
        return value;
    };
    bool reserved = false;                  // the lr reservation, and its word
    std::uint32_t reserved_word = 0;
    std::string sc_outcomes;                // Spike's sc outcomes (+sc_outcomes), and the next one
    std::size_t sc_next = 0;
    const bool sc_oracle = !plusarg("sc_outcomes").empty();
    if (sc_oracle) {
        std::FILE* file = std::fopen(plusarg("sc_outcomes").c_str(), "r");
        if (!file) { std::cerr << "cannot read " << plusarg("sc_outcomes") << "\n"; return 2; }
        for (int c; (c = std::fgetc(file)) != EOF;) if (c == 'S' || c == 'F') sc_outcomes += char(c);
        std::fclose(file);
    }
    std::uint64_t accepted_writes = 0, fetch_errors = 0, interrupts = 0, div_waits = 0;
    std::uint64_t m1_error_waits = 0, div_kills = 0;
    shell::Port iport, dport;
    bool d_error_next = false;              // d_rsp_error for the request accepted at the last edge
    bool d_error_cycle = false;             // a data request was accepted at the last edge
    std::deque<Write> writes;               // accepted data-port writes not yet matched to a retired store
    std::uint64_t cycles = 0, retired = 0;
    std::string result = "TIMEOUT";
    bool stop = false;
    shell::StableCheck i_stable, d_stable;
    shell::StableCheck core_i_stable, core_d_stable;   // the core's side, behind the caches
    std::size_t core_d_inflight = 0;
    std::uint64_t core_d_waits = 0;                    // cycles the core's data request waited
    bool have_previous = false, previous_trap = false;
    std::uint32_t previous_pc_wdata = 0;
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
        // Another master's write this cycle (+remote_mp), snooped with the caches.
        std::uint32_t remote_addr = 0, remote_value = 0;
        const bool remote_write = remote.step(remote_addr, remote_value)
                                  || remote_sb.step(memory, !snoop_queue.empty(), remote_addr, remote_value);
        if (remote_write) memory.write(remote_addr, remote_value, 0xFu);
        // Its snoop: now, or (+lazy_snoops) late, within the contract —
        // queued until a random 0-15 cycles pass or the data cache presents a
        // memory-side request (whose acceptance then waits for the queue to
        // empty, one snoop a cycle).
        if (remote_write && l1.enabled) snoop_queue.push_back({remote_addr & ~15u, lazy_snoops ? long(snoop_rng() % 16) : 0});
        bool snoop_now = false;
        std::uint32_t snoop_line = 0;
        if (!snoop_queue.empty() && (snoop_queue.front().wait <= 0 || d.d_req_valid)) {
            snoop_now = true;
            snoop_line = snoop_queue.front().line;
            snoop_queue.pop_front();
        }
        for (auto& queued : snoop_queue) --queued.wait;
        d.snoop_valid = snoop_now;
        d.snoop_line = snoop_line >> 4;
        d.meip = ((irq.level >> 11) & 1u) | v1.irq0();
        timer_catch_up(data_answered + (dport.responding() ? 1 : 0));   // writes answered this cycle
        d.mtip = ((irq.level >> 7) & 1u) | timer.mtip();
        d.mtime = timer.mtime;
        d.cacheable_bytes = mem_bytes;
        d.msip = (irq.level >> 3) & 1u;
        d.i_req_ready = iport.remaining() < max_inflight && !(stall && rng() % 4 == 0);
        d.d_req_ready = dport.remaining() < max_inflight && !(stall && rng() % 4 == 0) && snoop_queue.empty();
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
        if (l1.enabled) {
            // The same checks on the core's side of the caches (shell_aster_ports.sv).
            const std::uint32_t c_addr = d.chk_core_d_req_addr, c_be = d.chk_core_d_req_be;
            const std::string ci = core_i_stable.cycle(
                {bool(d.chk_core_i_req_valid), std::uint32_t(d.chk_core_i_req_addr) << 2, 0, 0, 0},
                d.chk_core_i_req_ready, d.chk_core_i_redirect);
            const std::string cd = core_d_stable.cycle(
                {bool(d.chk_core_d_req_valid), c_addr, int(d.chk_core_d_req_op), std::uint32_t(d.chk_core_d_req_wdata), c_be},
                d.chk_core_d_req_ready, false);
            if (!ci.empty()) { result = "I_REQ_UNSTABLE"; std::cerr << "the core's instruction request " << ci << "\n"; break; }
            if (!cd.empty()) { result = "D_REQ_UNSTABLE"; std::cerr << "the core's data request " << cd << "\n"; break; }
            if (d.chk_core_d_req_valid && !shell::well_formed(c_addr, c_be)) { result = "D_REQ_MALFORMED"; break; }
            const bool c_accept = d.chk_core_d_req_valid && d.chk_core_d_req_ready;
            if (d.chk_core_d_rsp_valid && core_d_inflight == 0) { result = "D_RSP_UNEXPECTED"; break; }
            if (c_accept && core_d_inflight - (d.chk_core_d_rsp_valid ? 1 : 0) >= 2) { result = "D_INFLIGHT"; break; }
            core_d_inflight += (c_accept ? 1 : 0);
            core_d_inflight -= (d.chk_core_d_rsp_valid ? 1 : 0);
            core_d_waits += d.chk_core_d_req_valid && !d.chk_core_d_req_ready;
        }
        if (l1.enabled) {
            // This cycle's snoop (a lookup in its cycle already misses its line),
            // then the lookups (each against the lines before this edge), then
            // the memory-side accesses and answers, then fence.i's invalidation
            // at the edge.
            if (snoop_now) l1.dsnoop(snoop_line);
            if (d.chk_ic_lookup) l1.ilookup(std::uint32_t(d.chk_ic_addr) << 2, d.chk_ic_hit, cycles);
            if (d.chk_dc_lookup)
                l1.dlookup(d.chk_dc_op, d.chk_dc_addr, d.chk_dc_be, d.chk_dc_wdata, d.chk_dc_hit, cycles);
            if (i_accept) l1.ifetch(i_addr);
            if (d_accept) l1.daccess(d_op, d_addr, d_be, d_wdata);
            if (iport.responding()) l1.ianswer();
            if (dport.responding()) l1.danswer();
            if (d.chk_fencei) l1.fencei(cycles);
            // With the caches the load check follows the core's loads into the
            // data cache (its lookups, in order) instead of the memory side's
            // refills: each retired load must match the oldest looked up and
            // not yet matched (an lr included, an error excepted).
            if (d.chk_dc_lookup && (d.chk_dc_op == kLoad || d.chk_dc_op == kLr) && l1.performs(d.chk_dc_addr)) {
                const Read read{std::uint32_t(d.chk_dc_addr) & ~3u, std::uint32_t(d.chk_dc_be)};
                reads.push_back(read);
                if (++accepted_reads == duplicate_read) reads.push_back(read);   // the self-test's
            }
            if (!l1.error.empty()) { result = "CACHE_MISMATCH"; std::cerr << "L1: " << l1.error << "\n"; break; }
        }
        // The core's own cover points (with or without the caches; 0 on PicoRV32).
        div_waits += d.chk_div_wait;
        m1_error_waits += d.chk_m1_err_wait;
        div_kills += d.chk_div_kill;

        d.clk = 1; d.eval();
        ++cycles;
        registered = sample(d);
        sampled = true;
        // Responses delivered in the cycle before this edge are consumed; others age.
        iport.advance();
        if (dport.responding()) ++data_answered;
        dport.advance();
        d_error_next = false;
        d_error_cycle = d_accept;
        if (i_accept) {
            const bool error = !memory.executable(i_addr);
            fetch_errors += error;
            iport.accept(latency, stall ? int(rng() % 3) + (long_stall && rng() % 16 == 0 ? 16 + int(rng() % 48) : 0) : 0,
                         error ? std::uint32_t(garbage()) : fetch_word(i_addr), error);
        }
        // The fetch above, accepted at the answer's edge, still read the old word.
        while (!unanswered.empty() && unanswered.front().sequence < data_answered) unanswered.pop_front();
        if (d_accept) {
            const bool device = irq.device && (d_addr & ~3u) == kIrqDevice;
            const bool timed = timer.decodes(d_addr) || v1.decodes(d_addr);
            const bool error = !device && !timed && !memory.contains(d_addr);
            std::uint32_t rdata = std::uint32_t(garbage());
            d_error_next = error;
            if (d_op < kLoad || d_op > kAmoLast || ((device || timed) && d_op > kStore)) {
                result = "UNSUPPORTED_OP";
                stop = true;
            }
            else if (timed) {
                // The machine timer or v1's devices: like the interrupt device,
                // checked against RVFI.
                if (d_op == kStore) {
                    ++accepted_writes;
                    if (v1.decodes(d_addr)) v1.store(d_addr, d_wdata, d_be);
                    else timer_writes.push_back({data_accepted, d_addr, d_wdata, d_be});
                    writes.push_back({d_addr & ~3u, d_be, d_wdata});
                } else {
                    if (!v1.decodes(d_addr)) timer_catch_up(~std::uint64_t(0));   // older writes first
                    rdata = v1.decodes(d_addr) ? v1.load(d_addr) : timer.load(d_addr);
                    memory_read(d_addr, d_be);
                }
            }
            else if (device) {
                // The interrupt device: its stores are checked against RVFI like any other.
                if (d_op == kStore) {
                    ++accepted_writes;
                    irq.store(d_wdata);
                    writes.push_back({d_addr & ~3u, d_be, d_wdata});
                } else {
                    rdata = irq.level;
                    memory_read(d_addr, d_be);
                }
            }
            else if (error) {
                // Not performed; answered with d_rsp_error. By default the run
                // stops here; with +bus_error_traps the DUT must trap on it.
                if (!bus_error_traps) { result = "BUS_ERROR"; stop = true; }
                if (d_op == kSc) reserved = false;
            }
            else if (d_op == kLr) {
                rdata = memory.read(d_addr);
                memory_read(d_addr, d_be);
                reserved = true;
                reserved_word = d_addr & ~3u;
            }
            else if (d_op == kSc) {
                const bool held = reserved && reserved_word == (d_addr & ~3u);
                bool success = held;
                reserved = false;
                if (sc_oracle) {
                    if (sc_next >= sc_outcomes.size()) { result = "SC_MISMATCH"; stop = true; }
                    else {
                        success = sc_outcomes[sc_next++] == 'S';
                        if (success && !held) { result = "SC_MISMATCH"; stop = true; }
                    }
                }
                if (success) {
                    ++accepted_writes;
                    write_data(d_addr, d_wdata, d_be);
                    writes.push_back({d_addr & ~3u, d_be, d_wdata});
                }
                rdata = success ? 0u : 1u;
            }
            else if (d_op >= kAmoFirst) {
                rdata = memory.read(d_addr);
                const std::uint32_t written = amo_value(d_op, rdata, d_wdata);
                ++accepted_writes;
                write_data(d_addr, written, d_be);
                writes.push_back({d_addr & ~3u, d_be, written});
            }
            else if (d_op == kStore) {
                const std::uint32_t written = ++accepted_writes == corrupt_write ? d_wdata ^ 0x01010101u : d_wdata;
                write_data(d_addr, written, d_be);
                writes.push_back({d_addr & ~3u, d_be, written});
                if (duplicate_tohost_write && (d_addr & ~3u) == tohost) writes.push_back({d_addr & ~3u, d_be, written});
            } else {
                rdata = memory.read(d_addr);
                memory_read(d_addr, d_be);
                if (!l1.enabled && ++accepted_reads == duplicate_read) memory_read(d_addr, d_be);
            }
            dport.accept(latency, stall ? int(rng() % 3) + (long_stall && rng() % 16 == 0 ? 16 + int(rng() % 48) : 0) : 0,
                         rdata, error);
            ++data_accepted;
        }
        irq.edge();
        timer.tick();
        v1.edge();
        if (d.rvfi_valid && !stop && d.rvfi_trap) reserved = false;   // an exception ends the reservation
        if (d.rvfi_valid && !stop) {
            // Each record's pc_wdata must be the next record's pc_rdata
            // (riscv-formal's pc_fwd; a trap record's is its handler's
            // address), except into an interrupt's handler.
            const bool interrupt = have_previous && d.rvfi_intr && !previous_trap;
            interrupts += interrupt;
            if (have_previous && !interrupt && d.rvfi_pc_rdata != previous_pc_wdata) {
                result = "PC_WDATA_MISMATCH";
                stop = true;
            }
            have_previous = true;
            previous_trap = d.rvfi_trap;
            previous_pc_wdata = d.rvfi_pc_wdata;
            if (!d.rvfi_trap) ++retired;
            if (retire_log)
                std::fprintf(retire_log, "%llu %llu %08x\n", (unsigned long long)d.rvfi_order,
                             (unsigned long long)cycles, d.rvfi_pc_rdata);
            if (trace) {
                std::fprintf(trace, "%llu %08x %08x %u %u %08x %08x %x %x %08x %08x",
                             (unsigned long long)d.rvfi_order, d.rvfi_pc_rdata, d.rvfi_insn, d.rvfi_trap,
                             d.rvfi_rd_addr, d.rvfi_rd_wdata, d.rvfi_mem_addr, d.rvfi_mem_rmask,
                             d.rvfi_mem_wmask, d.rvfi_mem_rdata, d.rvfi_mem_wdata);
                for (int i = 0; i < 15; ++i)
                    if (d.rvfi_csr_wvalid >> i & 1u) std::fprintf(trace, " c%03x=%08x", kCsrAddress[i], d.rvfi_csr_wdata[i]);
                std::fputs(d.rvfi_intr ? " intr\n" : "\n", trace);
            }
            if (load_check && !d.rvfi_trap && d.rvfi_mem_rmask && !d.rvfi_mem_wmask && !stop) {
                if (reads.empty() || reads.front().word != (d.rvfi_mem_addr & ~3u) ||
                    reads.front().mask != d.rvfi_mem_rmask) {
                    result = "LOAD_MISMATCH";
                    stop = true;
                } else {
                    reads.pop_front();
                }
            }
            if (!d.rvfi_trap && d.rvfi_mem_wmask && !stop) {
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
    if (load_check && (result == "PASS" || result == "TRAP") && reads.size() > (observer.end_at_record ? 3u : 0u))
        result = "STRAY_READ";
    if (sc_oracle && result == "PASS" && sc_next != sc_outcomes.size()) result = "SC_MISMATCH";
    if (trace) std::fclose(trace);
    if (retire_log) std::fclose(retire_log);
    if (observer.console) std::fclose(observer.console);
    if (result == "PASS") {
        if (const std::string error = shell::dump_signature(memory); !error.empty()) { std::cerr << error << "\n"; return 2; }
    }
    std::cout << "SHELL " << result << " cycles=" << cycles << " retired=" << retired << observer.window()
              << " fetch_errors=" << fetch_errors + l1.i_errors << " interrupts=" << interrupts;
    if (div_waits) std::cout << " div_waits=" << div_waits;   // cycles a finished division waited in Execute
    if (core_d_waits) std::cout << " core_d_waits=" << core_d_waits;   // the core's data request waited (caches)
    if (m1_error_waits) std::cout << " m1_error_waits=" << m1_error_waits;   // a data-port error waited in M1
    if (div_kills) std::cout << " div_kills=" << div_kills;   // traps taken while a division ran
    if (l1.enabled)
        std::cout << " icache_hits=" << l1.i_hits << " icache_misses=" << l1.i_misses
                  << " dcache_hits=" << l1.d_hits << " dcache_misses=" << l1.d_misses;
    std::cout << "\n";
    return result == "PASS" ? 0 : 1;
}
