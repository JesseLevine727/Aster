// Phase 20.2: the two-hart SoC's simulation (sim_soc.sv around
// rtl/soc/aster_soc.sv), driven through its AXI4-Lite port as the ARM side
// drives it on the board: the program loaded into main memory (through the
// fabric's ports W and R) while the harts are held, every word read back, the
// tohost address set, the run started and followed until hart 0 stores
// tohost. Every cycle, independently of the RTL's own checks:
// - the memory checker (verification/fabric/mem_checker.h, soc.md §10.2)
//   follows every write as it takes effect (a store's and a successful sc's
//   acceptance, an AMO's write two edges later, an NPU write's acceptance,
//   the ARM side's writes on port W while the harts are held)
//   and every load of each hart as it is performed (a hit at its data
//   cache's lookup; a miss at the fabric's acceptance of the refill read of
//   its word; an lr at its acceptance; an AMO at its write; an I/O load with
//   the value the device answered), and checks each load the hart retires
//   (RVFI) against the word at its perform point (MEMORY);
// - reservations follow soc.md §4.5 (Spike's rule: an lr sets its hart's; an
//   sc of the hart, another requester's write to its word, an exception the
//   hart retires and the hart's reset end it), and each sc to main memory
//   must succeed exactly when its hart's reservation holds its word at its
//   acceptance (SC_MISMATCH);
// - each write is snooped in the next cycle, on the right port of the right
//   cache and on no other (SNOOP: a data cache's write to the other cache's
//   port 0, the NPU's to both caches' port 1, W's to both caches' port 2);
// - each data-cache answer is owed (ANSWER);
// - the NPU's jobs, as in the Phase 19 SoC's testbench (tb_npu_soc.cpp):
//   each job against npu_model.h's reference (NPU_MISMATCH, NPU_CODE), its
//   writes only to its C and each byte once (STRAY_WRITE, DOUBLE_WRITE,
//   ERROR_JOB_WRITE), starts and ends in turn (JOB_ORDER);
// - a CPU result (SHELL_PAGE builds): a GEMM described on the register page
//   (0x2000_3F00) when the program stores 1 to its check word (CPU_MISMATCH),
//   as tb_npu_soc.cpp.
// Prints one line, and the console to +console=<file>:
//   SOC <PASS|status> cycles=<n> tohost=<hex> npu_jobs=<n> npu_unfinished=<n> cpu_checks=<n> snoops=<n>
//       loads=<n> sc=<n> sc_failed=<n> amos=<n> retired1=<n> resets=<n> resets_owed=<n> resets_amo=<n>
//       resets_refill=<n> resets_resv=<n> releases_early=<n> npu_waits=<n> npu_waits_irefill=<n>
//       npu_waits_daccess=<n> npu_waits_amo=<n> npu_waits_blk=<n> npu_waits_other=<n> hart_waits_npu=<n>
//       dma_reads=<n> dma_writes=<n> npu_config=<n> soc_config=<n>
// (dma_reads: port R's answers checked against memory at their acceptance; dma_writes: port W's writes
// while the harts run, each through the memory checker and snooped)
// (cycles: hart 0's release to its tohost store; loads: the loads the memory
// checker checked; sc, sc_failed, amos: those to main memory; retired1: hart
// 1's instructions retired; resets: hart 1's resets while hart 0 ran, and
// those that caught answers owed to it, its AMO before its write, its data
// cache refilling, its reservation held; releases_early: its releases while
// answers owed before its reset were still due, which the fabric must drop;
// npu_waits: the cycles the NPU's request waited, each by its cause in the
// fabric's own arbitration (its bank's eligible members, picks and port B's
// takes): an instruction cache's refill picked on port B (irefill: an NPU
// read shares port B with the refills); a data cache's access picked on port
// A, or its write holding back the NPU's read of its unit (daccess: an NPU
// write shares port A with the data caches); an AMO in the bank (amo); a unit
// held back for a port-B read (blk); other: none of these; hart_waits_npu:
// the cycles a hart waited behind the NPU, each hart once a cycle (its data
// cache eligible while the NPU's write was picked, or its write refused for
// the unit of the NPU's read held back the cycle before; its refill eligible
// while the NPU's read was picked, or held back by its write) — the bank conflicts
// between the harts and the NPU that soc.md §10.4 expects to change a Phase
// 19 program's cycles).
//
// With +board, the status line is instead 18.7's board simulation's, as
// tb_npu_soc.cpp prints it, for scripts/aster_board.py --design soc:
//   BOARD <PASS|status> cycles=<n> retired=<n> window_cycles=<n> window_retired=<n>
//         tohost=<hex> tohost_cycles=<n> tohost_retired=<n> console_bytes=<n> npu_config=<n>
//
// +trace=<file> and +trace1=<file> write hart 0's and hart 1's RVFI traces
// in the CPU shell's format (tb_core_ports.cpp). +signature=<file> with
// +sig_begin=<hex> and +sig_end=<hex> writes main memory's words from
// sig_begin to sig_end, read over AXI once the harts are held again, one a
// line in hex, as Spike's +signature (scripts/litmus.py reads them).
//
//     soc_sim +bin=<file> [+tohost=<hex>] [+console=<file>] [+max_cycles=<n>] [+board [+kernel]]
//             [+trace=<file>] [+trace1=<file>] [+signature=<file> +sig_begin=<hex> +sig_end=<hex>]
#include "Vsim_soc.h"
#include "verilated.h"

#include "mem_checker.h"
#include "npu_model.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

Vsim_soc* dut = nullptr;
std::uint64_t ticks = 0;
const npu::Profile profile = npu::v2_profile();
npu::Memory main_copy(profile.mem_base, profile.mem_bytes);
fabric::MemoryChecker checker(profile.mem_base, profile.mem_bytes);
std::vector<std::uint8_t> page_copy(16 * 1024, 0);
std::string failure;
std::uint64_t npu_jobs = 0, npu_unfinished = 0, cpu_checks = 0, snoops = 0, sc_count = 0, sc_failed = 0, amo_count = 0;
std::uint32_t job_error = 0;
std::FILE* trace[2] = {nullptr, nullptr};
constexpr std::uint32_t kCsrAddress[15] = {0x300, 0x310, 0x304, 0x344, 0x305, 0x340, 0x341, 0x342,
                                           0x343, 0x320, 0xB00, 0xB80, 0xB02, 0xB82, 0x301};
// The fabric's requesters as fabric_ref.h's writers know them (soc.md §4.1).
constexpr int kD[2] = {1, 3};
constexpr int kN = 4, kW = 6;

// Each hart's data-cache accesses the fabric accepted, in order, until answered.
struct Owed {
    std::uint32_t addr;
    bool io_load;                 // an I/O read: performed with the device's answer
    std::uint64_t accepted;
};
unsigned wait_cycles = 0;                    // the SoC's WAIT (its configuration word)
bool hart1_was_up = false;
std::uint64_t hart1_due = 0;                 // the last answer owed to hart 1 when it was reset: due before this cycle
std::uint64_t resets = 0, resets_owed = 0, resets_amo = 0, resets_refill = 0, resets_resv = 0, releases_early = 0;
std::uint64_t npu_waits = 0, npu_waits_irefill = 0, npu_waits_dwrite = 0, npu_waits_amo = 0, npu_waits_blk = 0;
std::uint64_t npu_waits_other = 0, hart_waits_npu = 0;
std::uint64_t dma_reads = 0, dma_writes = 0;          // port R's answers checked; port W's writes while running
std::deque<std::uint64_t> r_expect;                    // port R's reads owed: memory at their acceptance
int blk_owner[4] = {-1, -1, -1, -1};    // each bank's port-B member held back last cycle (its unit now refused)
std::deque<Owed> owed[2];
bool miss_due[2] = {false, false};           // a load missed: performed at its word's refill read
std::uint32_t miss_word[2] = {0, 0};
std::deque<bool> sc_expected[2];             // each main-memory sc's outcome, to its retirement
std::uint64_t generation[2] = {0, 0};        // counts each hart's resets (an AMO's perform is its hart's)
struct Amo { std::uint64_t due; int hart; std::uint64_t generation; int op; std::uint32_t addr, operand; };
std::vector<Amo> amos;
fabric::Reservation resv[2];
bool snoop_due[2][3] = {};
std::uint32_t snoop_due_line[2][3] = {};

// The NPU job in flight.
bool job_live = false;
npu::Job job;
npu::Memory job_before(profile.mem_base, profile.mem_bytes);
std::vector<std::uint8_t> job_written;

std::string plusarg(const char* name) {
    const char* value = Verilated::commandArgsPlusMatch(name);
    const std::string text = value ? value : "";
    const std::string prefix = std::string("+") + name + "=";
    return text.rfind(prefix, 0) == 0 ? text.substr(prefix.size()) : (text.empty() ? "" : "1");
}

void fail(const std::string& status, const std::string& detail) {
    if (failure.empty()) {
        failure = status;
        std::cerr << status << ": " << detail << " (cycle " << ticks << ")\n";
    }
}

std::string hex(std::uint64_t v) { char text[24]; std::snprintf(text, sizeof text, "0x%llx", (unsigned long long)v); return text; }

// A hart's field of a port packed by hart, and a field of a wide port.
std::uint64_t part(std::uint64_t value, int index, int width) {
    return (value >> (index * width)) & (width == 64 ? ~0ull : (1ull << width) - 1);
}
template <std::size_t N>
std::uint64_t wide(const VlWide<N>& value, int lo, int width) {
    std::uint64_t out = 0;
    for (int b = 0; b < width; ++b)
        if ((value[(lo + b) / 32] >> ((lo + b) % 32)) & 1u) out |= 1ull << b;
    return out;
}

bool in_main(std::uint32_t addr) { return addr - profile.mem_base < profile.mem_bytes; }

std::uint32_t page_word(std::uint32_t index) {
    std::uint32_t word = 0;
    for (int lane = 0; lane < 4; ++lane) word |= std::uint32_t(page_copy[4 * index + lane]) << (8 * lane);
    return word;
}

void check_cpu_result() {
    npu::Job g;
    g.a_base = page_word(0xFC0); g.b_base = page_word(0xFC1); g.c_base = page_word(0xFC2);
    g.a_stride = page_word(0xFC3); g.b_stride = page_word(0xFC4); g.c_stride = page_word(0xFC5);
    g.m = page_word(0xFC6); g.n = page_word(0xFC7); g.k = page_word(0xFC8);
    if (npu::expected_error(profile, g)) { fail("CPU_MISMATCH", "the checked GEMM's descriptor is not a valid job"); return; }
    npu::Memory expected = main_copy;
    npu::apply(g, expected);
    for (std::uint32_t i = 0; i < g.m; ++i)
        for (std::uint32_t j = 0; j < g.n; ++j)
            for (int b = 0; b < 4; ++b) {
                const std::uint32_t at = g.c_base + i * g.c_stride + 4 * j + b;
                if (main_copy.at(at) != expected.at(at)) {
                    fail("CPU_MISMATCH", "C(" + std::to_string(i) + "," + std::to_string(j) + ") of the CPU's GEMM");
                    return;
                }
            }
    ++cpu_checks;
}

void write_trace(int h) {
    std::FILE* f = trace[h];
    std::fprintf(f, "%llu %08x %08x %u %u %08x %08x %x %x %08x %08x",
                 (unsigned long long)wide(dut->rvfi_order, 64 * h, 64), unsigned(part(dut->rvfi_pc_rdata, h, 32)),
                 unsigned(part(dut->rvfi_insn, h, 32)), unsigned(part(dut->rvfi_trap, h, 1)),
                 unsigned(part(dut->rvfi_rd_addr, h, 5)), unsigned(part(dut->rvfi_rd_wdata, h, 32)),
                 unsigned(part(dut->rvfi_mem_addr, h, 32)), unsigned(part(dut->rvfi_mem_rmask, h, 4)),
                 unsigned(part(dut->rvfi_mem_wmask, h, 4)), unsigned(part(dut->rvfi_mem_rdata, h, 32)),
                 unsigned(part(dut->rvfi_mem_wdata, h, 32)));
    for (int i = 0; i < 15; ++i)
        if (part(dut->rvfi_csr_wvalid, 15 * h + i, 1))
            std::fprintf(f, " c%03x=%08x", kCsrAddress[i], unsigned(wide(dut->rvfi_csr_wdata, 32 * (15 * h + i), 32)));
    std::fputs(part(dut->rvfi_intr, h, 1) ? " intr\n" : "\n", f);
}

// One clock cycle, with the checks of the signals before its edge.
void cycle() {
    dut->aclk = 0;
    dut->eval();
    const bool running = dut->aresetn && dut->chk_run;
    // The fabric's reset is synchronous: its grants and bank writes still act in a reset cycle (a stop
    // while the harts run), and only what was in flight is lost at its edge (below).
    const bool fabric_reset = !dut->chk_fabric_rst_n;
    const bool fabric_up = dut->aresetn;
    bool core_up[2];
    for (int h = 0; h < 2; ++h) core_up[h] = dut->aresetn && part(dut->chk_hart_rst_n, h, 1);
    // ---- hart 1's resets and releases while hart 0 runs: what they caught ----
    if (running && core_up[0] && hart1_was_up && !core_up[1]) {
        ++resets;
        hart1_due = 0;
        for (const Owed& o : owed[1]) hart1_due = std::max(hart1_due, o.accepted + 2 + wait_cycles + 1);
        if (!owed[1].empty()) ++resets_owed;
        for (const Amo& a : amos) if (a.hart == 1 && a.due >= ticks) { ++resets_amo; break; }
        if (miss_due[1]) ++resets_refill;
        if (resv[1].valid) ++resets_resv;
    }
    if (running && core_up[0] && !hart1_was_up && core_up[1] && ticks < hart1_due) ++releases_early;
    hart1_was_up = core_up[1];

    // ---- snoops: those due from the last cycle's writes, and no others ----
    if (fabric_up)
        for (int c = 0; c < 2; ++c)
            for (int p = 0; p < 3; ++p) {
                const bool valid = part(dut->chk_snoop_valid, 3 * c + p, 1);
                const std::uint32_t line = std::uint32_t(wide(dut->chk_snoop_line, 28 * (3 * c + p), 28));
                if (valid != snoop_due[c][p])
                    fail("SNOOP", std::string(snoop_due[c][p] ? "a write not snooped" : "a snoop with no write")
                         + " in the next cycle (cache " + std::to_string(c) + ", port " + std::to_string(p) + ")");
                else if (valid && line != snoop_due_line[c][p])
                    fail("SNOOP", "a snoop of " + hex(line << 4) + ", the write's line " + hex(snoop_due_line[c][p] << 4));
                if (valid) ++snoops;
            }

    std::vector<fabric::Write> writes;
    fabric::Reservation lr_set[2];
    bool sc_end[2] = {false, false};
    for (int h = 0; h < 2; ++h) {
        if (!core_up[h]) {                      // a held hart: its performed loads and owed answers are gone
            checker.drop(h);
            miss_due[h] = false;
            sc_expected[h].clear();
            ++generation[h];
            owed[h].clear();
        }
        // ---- performs: a hit at its lookup; a miss noted, for its refill ----
        if (core_up[h] && part(dut->chk_lookup, h, 1) && part(dut->chk_lookup_op, h, 4) == fabric::LOAD) {
            const std::uint32_t addr = std::uint32_t(part(dut->chk_lookup_addr, h, 32));
            if (in_main(addr)) {
                if (part(dut->chk_lookup_hit, h, 1)) checker.performed(h, addr, ticks);
                else { miss_due[h] = true; miss_word[h] = addr & ~3u; }
            }
        }
        // ---- the fabric's acceptances of the D port ----
        if (fabric_up && part(dut->chk_d_valid, h, 1) && part(dut->chk_d_ready, h, 1)) {
            const int op = int(part(dut->chk_d_op, h, 4));
            const std::uint32_t addr = std::uint32_t(part(dut->chk_d_addr, h, 32));
            const std::uint32_t wdata = std::uint32_t(part(dut->chk_d_wdata, h, 32));
            const std::uint32_t be = std::uint32_t(part(dut->chk_d_be, h, 4));
            const bool main = in_main(addr);
            const unsigned shift = (addr & 4u) ? 32 : 0;
            const fabric::Write store{kD[h], addr & ~7u, std::uint8_t(be << (shift / 8)), std::uint64_t(wdata) << shift};
            if (!core_up[h]) fail("HELD_REQUEST", "a request from a hart held in reset");
            if (bool(part(dut->chk_d_main, h, 1)) != main)        // the cache's flag the fabric now trusts (20.2)
                fail("MAIN_FLAG", "a data cache's main-memory flag disagrees with its address " + hex(addr));
            owed[h].push_back({addr, !main && op != fabric::STORE && op != fabric::SC, ticks});
            if (main && op == fabric::STORE) writes.push_back(store);
            else if (main && op == fabric::LOAD) {
                if (miss_due[h] && (addr & ~3u) == miss_word[h]) { checker.performed(h, addr, ticks); miss_due[h] = false; }
            } else if (main && op == fabric::LR) {
                checker.performed(h, addr, ticks);
                lr_set[h] = {true, addr >> 2};
            } else if (main && op == fabric::SC) {
                const bool ok = resv[h].valid && resv[h].word == (addr >> 2);
                sc_expected[h].push_back(ok);
                ++sc_count;
                if (!ok) ++sc_failed;
                if (ok) { writes.push_back(store); writes.back().kind = fabric::BY_SC; }
                sc_end[h] = true;
            } else if (main && fabric::is_amo(op)) {
                amos.push_back({ticks + 2, h, generation[h], op, addr, wdata});
                ++amo_count;
            }
        }
    }
    // ---- AMO writes due at this edge: performed, then written ----
    for (const Amo& a : amos) {
        if (a.due != ticks) continue;
        if (core_up[a.hart] && a.generation == generation[a.hart]) checker.performed(a.hart, a.addr, ticks);
        const std::uint32_t value = fabric::amo_value(a.op, checker.memory.word(a.addr), a.operand);
        writes.push_back({kD[a.hart], a.addr & ~7u, std::uint8_t((a.addr & 4u) ? 0xF0 : 0x0F),
                          std::uint64_t(value) << ((a.addr & 4u) ? 32 : 0), fabric::BY_AMO});
    }
    amos.erase(std::remove_if(amos.begin(), amos.end(), [](const Amo& a) { return a.due == ticks; }), amos.end());
    // ---- the NPU's writes: only C's bytes, each once ----
    if (fabric_up && running && dut->chk_n_accept && dut->chk_n_we) {
        const std::uint32_t addr = std::uint32_t(dut->chk_n_addr) << 2;
        writes.push_back({kN, addr & ~7u, std::uint8_t(dut->chk_n_be), std::uint64_t(dut->chk_n_wdata)});
        if (std::uint32_t(dut->chk_n_be) != (addr & 4u ? 0xF0u : 0x0Fu))
            fail("STRAY_WRITE", "an NPU write's byte enables not its word's");
        const std::uint32_t word_addr = addr & ~3u;
        for (int lane = 0; lane < 4; ++lane) {
            if (!job_live || !npu::in_c(job, word_addr + lane)) { fail("STRAY_WRITE", "an NPU write outside its C"); break; }
            if (job_error) { fail("ERROR_JOB_WRITE", "an NPU write in a job that must end with an error"); break; }
            std::uint8_t& mark = job_written[word_addr + lane - profile.mem_base];
            if (mark) { fail("DOUBLE_WRITE", "a C byte written twice"); break; }
            mark = 1;
        }
    }
    // ---- port W's writes (the DMA's while the harts run, the ARM side's while held): each takes effect at
    // its acceptance, and is snooped as the NPU's are (20.3) ----
    // While the harts run, port W carries only the engine's writes, each inside its copy's destination;
    // while they are held, only the ARM side's.
    if (fabric_up && dut->chk_w_accept) {
        writes.push_back({kW, std::uint32_t(dut->chk_w_addr) & ~7u, std::uint8_t(dut->chk_w_be), std::uint64_t(dut->chk_w_wdata)});
        if (running) {
            ++dma_writes;
            const std::uint64_t a = std::uint32_t(dut->chk_w_addr) & ~7u, dst = std::uint32_t(dut->chk_dma_dst),
                                end = dst + std::uint32_t(dut->chk_dma_len);
            if (!dut->chk_dma_running) fail("DMA_STRAY", "a write on port W with no copy in progress");
            for (int b = 0; b < 8; ++b)
                if (((dut->chk_w_be >> b) & 1) && (a + b < dst || a + b >= end)) { fail("DMA_STRAY", "a DMA write outside its destination"); break; }
        } else if (!dut->chk_arm_v) fail("ARM_ACCESS", "a write on port W while held that the ARM side did not make");
    }
    // ---- port R's reads: each sees main memory as it stands at its acceptance (a write to its unit is
    // never taken in the same cycle), checked when its answer comes, in order ----
    if (fabric_up) {
        if (dut->chk_r_rsp_valid) {
            if (r_expect.empty()) fail("DMA_READ", "port R answered with nothing owed");
            else {
                const std::uint64_t want = r_expect.front();
                r_expect.pop_front();
                if (std::uint64_t(dut->chk_r_rsp_rdata) != want)
                    fail("DMA_READ", "port R's answer is not memory as it stood at its acceptance");
                ++dma_reads;
            }
        }
        if (dut->chk_r_accept) {
            const std::uint32_t a = std::uint32_t(dut->chk_r_addr) & ~7u;
            r_expect.push_back(std::uint64_t(checker.memory.word(a)) | (std::uint64_t(checker.memory.word(a + 4)) << 32));
            // the bank rule: no write to its unit taken in the same cycle (so memory at its acceptance is exact)
            for (const auto& w : writes) if ((w.unit_addr & ~7u) == a) { fail("BANK_RULE", "a read on port R taken with a write of its unit"); break; }
            // the engine's reads inside its copy's source while the harts run; the ARM side's while held
            if (running) {
                const std::uint64_t src = std::uint32_t(dut->chk_dma_src), end = src + std::uint32_t(dut->chk_dma_len);
                if (!dut->chk_dma_running || a + 8 <= src || a >= end) fail("DMA_STRAY", "a DMA read outside its source");
            } else if (!dut->chk_arm_v) fail("ARM_ACCESS", "a read on port R while held that the ARM side did not make");
        }
    }
    // (the fabric's reset, at each start and stop, drops the answers owed)
    if (!fabric_up || !dut->chk_fabric_rst_n) r_expect.clear();
    // ---- the NPU's waits, by cause, and the harts' waits behind the NPU, from the fabric's own
    // arbitration (each bank's members: port A D0 D1 N W, port B I0 I1 R N) ----
    if (fabric_up && running) {
        const std::uint64_t ea = dut->chk_f_elig_a, pa = dut->chk_f_pick_a, eb = dut->chk_f_elig_b,
                            pb = dut->chk_f_pick_b, tb = dut->chk_f_take_b;
        const auto bit = [](std::uint64_t v, unsigned bank, unsigned member) { return ((v >> (4 * bank + member)) & 1u) != 0; };
        if (dut->chk_n_valid && !dut->chk_n_ready) {
            ++npu_waits;
            const unsigned b = (std::uint32_t(dut->chk_n_addr) >> 2) & 3u;     // byte address bits 5:4
            const bool d_picked = bit(pa, b, 0) || bit(pa, b, 1);
            if (dut->chk_n_we) {                                              // port A, member 2
                if (!bit(ea, b, 2)) {
                    if (part(dut->chk_f_amo_wr_now, b, 1) || part(dut->chk_f_amo_busy, b, 1)) ++npu_waits_amo;
                    else if (part(dut->chk_f_blk_v, b, 1)) ++npu_waits_blk;
                    else ++npu_waits_other;
                } else if (d_picked) ++npu_waits_dwrite;
                else ++npu_waits_other;
            } else {                                                          // port B, member 3
                if (!bit(eb, b, 3)) {
                    if (part(dut->chk_f_amo_wr_now, b, 1)) ++npu_waits_amo; else ++npu_waits_other;
                } else if (bit(pb, b, 0) || bit(pb, b, 1)) ++npu_waits_irefill;
                else if (bit(pb, b, 3) && !bit(tb, b, 3) && d_picked) ++npu_waits_dwrite;  // the bank rule
                else ++npu_waits_other;
            }
        }
        // each hart counts once a cycle: its data cache eligible while the NPU's write was picked, or its
        // write refused for the unit of the NPU's read held back last cycle (the starvation rule); its refill
        // eligible while the NPU's read was picked, or picked and held back by the NPU's write of its unit
        for (int h = 0; h < 2; ++h) {
            if (!core_up[h]) continue;                 // (a hart in reset: the fabric ignores its requests)
            bool behind = false;
            if (part(dut->chk_d_valid, h, 1) && !part(dut->chk_d_ready, h, 1) && part(dut->chk_d_main, h, 1)) {
                const std::uint32_t a = std::uint32_t(part(dut->chk_d_addr, h, 32));
                const unsigned b = (a >> 4) & 3u, op = unsigned(part(dut->chk_d_op, h, 4));
                const std::uint32_t index = (((a >> 6) & 0x7FFu) << 1) | ((a >> 3) & 1u);
                if (bit(ea, b, unsigned(h)) && bit(pa, b, 2)) behind = true;
                else if (!bit(ea, b, unsigned(h)) && part(dut->chk_f_blk_v, b, 1) && blk_owner[b] == 3
                         && op != 0 && op != 2 && index == std::uint32_t(part(dut->chk_f_blk_index, b, 12))
                         && !part(dut->chk_f_amo_busy, b, 1) && !part(dut->chk_f_hold, h, 1)) behind = true;
            }
            if (part(dut->chk_i_valid, h, 1) && !part(dut->chk_i_ready, h, 1)) {
                const unsigned b = (std::uint32_t(part(dut->chk_i_addr, h, 30)) >> 2) & 3u;
                if ((bit(eb, b, unsigned(h)) && bit(pb, b, 3))
                    || (bit(pb, b, unsigned(h)) && !bit(tb, b, unsigned(h)) && bit(pa, b, 2))) behind = true;
            }
            if (behind) ++hart_waits_npu;
        }
    }
    // (the member each bank's port B held back this cycle, whose unit port A refuses in the next)
    for (unsigned b = 0; b < 4; ++b) {
        blk_owner[b] = -1;
        if (fabric_up)
            for (unsigned m = 0; m < 4; ++m)
                if (((std::uint64_t(dut->chk_f_pick_b) >> (4 * b + m)) & 1u) && !((std::uint64_t(dut->chk_f_take_b) >> (4 * b + m)) & 1u))
                    blk_owner[b] = int(m);
    }
    // ---- the answers: each owed; an I/O read performed with its value ----
    for (int h = 0; h < 2; ++h)
        if (fabric_up && part(dut->chk_d_rsp_valid, h, 1) && core_up[h]) {
            if (owed[h].empty()) { fail("ANSWER", "hart " + std::to_string(h) + "'s cache answered with nothing owed"); continue; }
            const Owed o = owed[h].front();
            owed[h].pop_front();
            if (o.io_load && core_up[h])
                checker.performed_value(h, o.addr, std::uint32_t(part(dut->chk_d_rsp_rdata, h, 32)), ticks);
        }
    // ---- retirements: loads against their perform points; sc outcomes ----
    for (int h = 0; h < 2; ++h) {
        if (!(running && core_up[h] && part(dut->rvfi_valid, h, 1))) continue;
        if (trace[h]) write_trace(h);
        if (part(dut->rvfi_trap, h, 1)) continue;
        const std::uint32_t insn = std::uint32_t(part(dut->rvfi_insn, h, 32));
        const std::uint32_t mem_addr = std::uint32_t(part(dut->rvfi_mem_addr, h, 32));
        const std::uint32_t rmask = std::uint32_t(part(dut->rvfi_mem_rmask, h, 4));
        if (rmask) {
            const std::string problem = checker.retired(h, mem_addr, std::uint8_t(rmask),
                                                        std::uint32_t(part(dut->rvfi_mem_rdata, h, 32)));
            if (!problem.empty()) fail("MEMORY", problem);
        }
        const bool sc = (insn & 0x7F) == 0x2F && (insn >> 27) == 0x03;
        // (an sc's RVFI address is 0 when it fails: its rs1 names the word)
        if (sc && !sc_expected[h].empty()) {
            const bool expected = sc_expected[h].front();
            sc_expected[h].pop_front();
            const bool succeeded = part(dut->rvfi_mem_wmask, h, 4) != 0;
            if (succeeded != expected)
                fail("SC_MISMATCH", "hart " + std::to_string(h) + "'s sc " + (succeeded ? "succeeded" : "failed")
                     + ", but its reservation " + (expected ? "held" : "did not hold") + " its word");
        }
    }
    // ---- the writes take effect: the checker's memory, the NPU's copy; reservations; next snoops ----
    for (auto& port : snoop_due) for (bool& due : port) due = false;
    for (const fabric::Write& w : writes) {
        checker.write(w);
        for (int b = 0; b < 8; ++b)
            if ((w.be8 >> b) & 1u) main_copy.at(w.unit_addr + b) = std::uint8_t(w.data >> (8 * b));
        for (int c = 0; c < 2; ++c) {
            const int port = w.writer == kN ? 1 : w.writer == kW ? 2 : w.writer == kD[1 - c] ? 0 : -1;
            if (port < 0) continue;
            if (snoop_due[c][port]) fail("SNOOP", "two writes for one snoop port in one cycle");
            snoop_due[c][port] = true;
            snoop_due_line[c][port] = w.unit_addr >> 4;
        }
    }
    for (int h = 0; h < 2; ++h) {
        if (lr_set[h].valid) resv[h] = lr_set[h];
        if (sc_end[h]) resv[h].valid = false;
        for (const fabric::Write& w : writes)
            if (w.writer != kD[h] && resv[h].valid && fabric::touches(w, resv[h].word)) resv[h].valid = false;
        if (part(dut->chk_hart_exception, h, 1)) resv[h].valid = false;
        if (h == 1 && !core_up[1]) resv[1].valid = false;
    }
    if (fabric_reset) {                         // at the reset's edge: nothing in flight survives
        for (int h = 0; h < 2; ++h) { owed[h].clear(); resv[h].valid = false; }
        amos.clear();
        for (auto& port : snoop_due) for (bool& due : port) due = false;
    }
    // ---- the NPU's start, with the memory as it is ----
    if (running && dut->chk_npu_start) {
        if (job_live) fail("JOB_ORDER", "a start while a job is live");
        job = npu::Job{};
        job.a_base = dut->chk_a_base; job.b_base = dut->chk_b_base; job.c_base = dut->chk_c_base;
        job.a_stride = dut->chk_a_stride; job.b_stride = dut->chk_b_stride; job.c_stride = dut->chk_c_stride;
        job.m = dut->chk_m; job.n = dut->chk_n; job.k = dut->chk_k; job.mode = dut->chk_mode;
        job.a_m0 = dut->chk_a_m0; job.a_stride_m1 = dut->chk_a_stride_m1;
        job.a_k0 = dut->chk_a_k0; job.a_stride_k1 = dut->chk_a_stride_k1;
        job_before = main_copy;
        job_written.assign(profile.mem_bytes, 0);
        job_live = true;
        job_error = npu::expected_error(profile, job);
    }
    bool check_word = false;
    if (dut->chk_p_en && dut->chk_p_we) {
        for (int lane = 0; lane < 4; ++lane)
            if ((dut->chk_p_be >> lane) & 1u)
                page_copy[4 * std::uint32_t(dut->chk_p_idx) + lane] = std::uint8_t(std::uint32_t(dut->chk_p_wdata) >> (8 * lane));
        check_word = running && dut->chk_p_idx == 0xFCF && std::uint32_t(dut->chk_p_wdata) == 1u;
    }
    const bool finish = running && dut->chk_npu_finish;
    const bool complete = finish && dut->chk_npu_code == 0 && !dut->chk_npu_aborted;
    dut->aclk = 1;
    dut->eval();
    ++ticks;
    if (check_word) check_cpu_result();
    if (finish) {
        if (!job_live) fail("JOB_ORDER", "an end with no start");
        if (job_live && !dut->chk_npu_aborted && dut->chk_npu_code != npu::expected_error(profile, job))
            fail("NPU_CODE", "ended with code " + std::to_string(int(dut->chk_npu_code)) + ", the reference's "
                 + std::to_string(npu::expected_error(profile, job)));
        if (job_live && !complete) ++npu_unfinished;
        if (complete && job_live) {
            npu::Memory expected = job_before;
            npu::apply(job, expected);
            for (std::uint32_t i = 0; i < job.m && failure.empty(); ++i)
                for (std::uint32_t j = 0; j < job.n; ++j) {
                    const std::uint32_t at = job.c_base + i * job.c_stride + 4 * j;
                    bool same = true;
                    for (int b = 0; b < 4; ++b) same = same && main_copy.at(at + b) == expected.at(at + b);
                    if (!same) { fail("NPU_MISMATCH", "C(" + std::to_string(i) + "," + std::to_string(j) + ")"); break; }
                }
            ++npu_jobs;
        }
        job_live = false;
    }
}

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
        cycle();
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
        cycle();
        if (ar_now) dut->s_axi_arvalid = 0;
        if (r_now) { dut->s_axi_rready = 0; return data; }
    }
    throw std::runtime_error("AXI read timed out");
}

std::uint64_t read64(std::uint32_t address) { return axi_read(address) | (std::uint64_t(axi_read(address + 4)) << 32); }

}  // namespace

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (plusarg("bin").empty()) { std::cerr << "+bin=<file> required\n"; return 2; }
    std::ifstream file(plusarg("bin"), std::ios::binary);
    std::vector<char> image((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (image.empty() || image.size() > profile.mem_bytes) { std::cerr << "bad image\n"; return 2; }
    const std::uint32_t tohost = plusarg("tohost").empty() ? 0 : std::uint32_t(std::stoul(plusarg("tohost"), nullptr, 16));
    const std::uint64_t max_cycles = plusarg("max_cycles").empty() ? 50'000'000 : std::stoull(plusarg("max_cycles"));

    dut = new Vsim_soc;
    dut->aresetn = 0;
    for (int i = 0; i < 8; ++i) cycle();
    dut->aresetn = 1;
    cycle();
    if (axi_read(0x3F040) != 0x41535432u) { std::cerr << "bad magic\n"; return 2; }
    image.resize((image.size() + 3) & ~std::size_t(3), 0);
    for (std::size_t i = 0; i < image.size(); i += 4) {
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        axi_write(std::uint32_t(i), word);
    }
    for (std::size_t i = 0; i < image.size(); i += 4) {      // every word read back, through the fabric
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        if (axi_read(std::uint32_t(i)) != word) { std::cerr << "image read-back mismatch at " << i << "\n"; return 2; }
        if (checker.memory.word(profile.mem_base + std::uint32_t(i)) != word) {
            std::cerr << "the checker's copy of the image differs at " << i << "\n";
            return 2;
        }
    }
    const std::uint32_t npu_config = axi_read(0x3F058);
    const std::uint32_t soc_config = axi_read(0x3F05C);     // {WAIT, SHELL_PAGE, HARTS}
    wait_cycles = (soc_config >> 16) & 0xFFu;
    axi_write(0x3F030, tohost);
    axi_write(0x3F000, 1);

    const bool board = !plusarg("board").empty(), kernel = !plusarg("kernel").empty();
    const char* trace_args[2] = {"trace", "trace1"};
    for (int h = 0; h < 2; ++h)
        if (!plusarg(trace_args[h]).empty() && !(trace[h] = std::fopen(plusarg(trace_args[h]).c_str(), "w"))) {
            std::cerr << "cannot write " << plusarg(trace_args[h]) << "\n";
            return 2;
        }
    std::string console, result = "TIMEOUT";
    std::uint32_t seen = 0;
    while (failure.empty()) {
        for (int i = 0; i < 2000 && failure.empty(); ++i) cycle();
        const std::uint32_t status = axi_read(0x3F004);
        const std::uint32_t count = axi_read(0x3F008);
        if (count - seen > 4096) { result = "CONSOLE_OVERFLOW"; break; }
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
    if (!failure.empty()) result = failure;
    const std::uint64_t cycles = read64(0x3F038);
    const std::uint32_t value = axi_read(0x3F034);
    if (board) {
        const std::uint64_t window_cycles = read64(0x3F020), window_retired = read64(0x3F028);
        std::printf("BOARD %s cycles=%llu retired=%llu window_cycles=%llu window_retired=%llu tohost=%x "
                    "tohost_cycles=%llu tohost_retired=%llu console_bytes=%zu npu_config=%u\n", result.c_str(),
                    (unsigned long long)read64(0x3F010), (unsigned long long)read64(0x3F018),
                    (unsigned long long)window_cycles, (unsigned long long)window_retired, value,
                    (unsigned long long)cycles, (unsigned long long)read64(0x3F050), console.size(), npu_config);
    }
    const std::uint64_t retired1 = read64(0x3F060);
    axi_write(0x3F000, 0);
    if (!plusarg("signature").empty()) {
        std::ofstream sig(plusarg("signature"));
        const std::uint32_t begin = std::uint32_t(std::stoul(plusarg("sig_begin"), nullptr, 16));
        const std::uint32_t end = std::uint32_t(std::stoul(plusarg("sig_end"), nullptr, 16));
        char line[16];
        for (std::uint32_t at = begin; at < end; at += 4) {
            std::snprintf(line, sizeof line, "%08x\n", axi_read(at - profile.mem_base));
            sig << line;
        }
    }
    if (!plusarg("console").empty()) std::ofstream(plusarg("console")) << console;
    if (!failure.empty() && result == "PASS") result = failure;     // (a failure in the readback after the stop)
    if (!board) std::printf("SOC %s cycles=%llu tohost=%x npu_jobs=%llu npu_unfinished=%llu cpu_checks=%llu "
                            "snoops=%llu loads=%llu sc=%llu sc_failed=%llu amos=%llu retired1=%llu resets=%llu "
                            "resets_owed=%llu resets_amo=%llu resets_refill=%llu resets_resv=%llu releases_early=%llu "
                            "npu_waits=%llu npu_waits_irefill=%llu npu_waits_daccess=%llu npu_waits_amo=%llu "
                            "npu_waits_blk=%llu npu_waits_other=%llu hart_waits_npu=%llu dma_reads=%llu dma_writes=%llu "
                            "npu_config=%u soc_config=%u\n", result.c_str(), (unsigned long long)cycles, value,
                            (unsigned long long)npu_jobs, (unsigned long long)npu_unfinished,
                            (unsigned long long)cpu_checks, (unsigned long long)snoops,
                            (unsigned long long)checker.checked, (unsigned long long)sc_count,
                            (unsigned long long)sc_failed, (unsigned long long)amo_count,
                            (unsigned long long)retired1, (unsigned long long)resets, (unsigned long long)resets_owed,
                            (unsigned long long)resets_amo, (unsigned long long)resets_refill,
                            (unsigned long long)resets_resv, (unsigned long long)releases_early,
                            (unsigned long long)npu_waits, (unsigned long long)npu_waits_irefill,
                            (unsigned long long)npu_waits_dwrite, (unsigned long long)npu_waits_amo,
                            (unsigned long long)npu_waits_blk, (unsigned long long)npu_waits_other,
                            (unsigned long long)hart_waits_npu, (unsigned long long)dma_reads,
                            (unsigned long long)dma_writes, npu_config, soc_config);
    for (std::FILE* f : trace) if (f) std::fclose(f);
    delete dut;
    return result == "PASS" ? 0 : 1;
}
