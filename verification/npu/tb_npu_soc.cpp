// Phase 19.4: the Phase 19 SoC's simulation (sim_npu_soc.sv around
// rtl/soc/aster_npu_soc.sv), driven through its AXI4-Lite port as the ARM side
// drives it on the board: the program loaded into main memory while the core
// is held in reset, the tohost address set, the core started, the run followed
// until tohost is stored. Every cycle, independently of the RTL's own checks:
// - a copy of main memory and the register page follows every write;
// - each NPU job (its start, with the descriptor the NPU latched, to its end)
//   is checked against npu_model.h's reference on the copy as it was at the
//   start: every C element, for a job that completes (NPU_MISMATCH); the end's
//   error code, for a job not aborted (NPU_CODE; the memory side answers no
//   errors, so the descriptor's); each of its writes only to C, each C byte
//   once, and none in a job that must end with an error (STRAY_WRITE,
//   DOUBLE_WRITE, ERROR_JOB_WRITE); each end after a start, each start after
//   an end (JOB_ORDER);
// - each NPU write the memory side accepts is presented to the data cache as a
//   snoop of its line in the next cycle, and no other snoop is (SNOOP);
// - each load the data cache answers from main memory (as Phase 18's unit
//   test tb_l1d.cpp, cpu.md §9): the word memory held at its lookup — after
//   every write before that cycle, whose snoop came by then — or a value
//   written to it later, before its answer (STALE_LOAD). Defensively, not the
//   lookup's value if a store, sc or AMO of the core's to the word, ahead of
//   the load, had not reached memory by then — which the cache's in-order
//   head, answering a posted store once memory takes it, never allows;
// - a CPU result: when the program stores 1 to the page's check word
//   (0x2000_3F3C) after the descriptor of a GEMM it computed (0x2000_3F00:
//   A, B and C bases, A, B and C strides, M, N, K), its C as the reference
//   computes it from the copy (CPU_MISMATCH).
// Prints one line, and the console to +console=<file>:
//   SOC <PASS|status> cycles=<n> tohost=<hex> npu_jobs=<n> npu_unfinished=<n> cpu_checks=<n> snoops=<n>
//       loads=<n>
// (cycles: from the core's release to its tohost store, as the CPU shell
// counts, 0 if it never stored; npu_jobs: the jobs completed and checked;
// npu_unfinished: those ended by an error or an abort; loads: the main-memory
// loads checked).
//
// With +board, the status line is instead 18.7's board simulation's
// (verification/fpga/tb_aster_core_pynq.cpp), so scripts/aster_board.py runs
// its programs on this SoC as on 18.7's design and compares each with the CPU
// shell's run, cycle for cycle (+kernel: a CPU kernel's end, its record line
// after its window, as there):
//   BOARD <PASS|status> cycles=<n> retired=<n> window_cycles=<n> window_retired=<n>
//         tohost=<hex> tohost_cycles=<n> tohost_retired=<n> console_bytes=<n>
//
// +trace=<file> writes the core's RVFI trace in the CPU shell's format
// (tb_core_ports.cpp), so a run's trace can be compared with the shell's.
// +no_snoop_check (for planted-bug runs only) leaves out the snoop check, to
// show what the load check catches on its own.
//
//     npu_soc_sim +bin=<file> [+tohost=<hex>] [+console=<file>] [+max_cycles=<n>] [+board [+kernel]]
#include "Vsim_npu_soc.h"
#include "verilated.h"

#include "npu_model.h"

#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

Vsim_npu_soc* dut = nullptr;
std::uint64_t ticks = 0;
const npu::Profile profile = npu::v2_profile();
npu::Memory main_copy(profile.mem_base, profile.mem_bytes);
std::vector<std::uint8_t> page_copy(16 * 1024, 0);
std::string failure;
std::uint64_t npu_jobs = 0, npu_unfinished = 0, cpu_checks = 0, snoops = 0, loads_checked = 0;
std::uint32_t job_error = 0;               // the live job's expected error code
bool snoop_check = true;
std::FILE* trace = nullptr;
// The CSR of each rvfi_csr_wvalid/wdata entry (as tb_core_ports.cpp's kCsrAddress).
constexpr std::uint32_t kCsrAddress[15] = {0x300, 0x310, 0x304, 0x344, 0x305, 0x340, 0x341, 0x342,
                                           0x343, 0x320, 0xB00, 0xB80, 0xB02, 0xB82, 0x301};

// The data cache's requests, in order, for the load check.
struct DReq {
    std::uint32_t word;                    // byte address of the word
    bool main_load;                        // a load from main memory: checked
    bool looked = false;
    std::vector<std::uint32_t> allowed;    // the values it may return
};
std::deque<DReq> dreqs;
std::size_t dlooked = 0;                   // dreqs[0 .. dlooked) have been looked up
std::map<std::uint32_t, int> own_pending;  // per word: the core's writes accepted by the cache, not yet by memory
constexpr std::uint32_t OP_LOAD = 0, OP_LR = 2;

// The NPU job in flight.
bool job_live = false;
npu::Job job;
npu::Memory job_before(profile.mem_base, profile.mem_bytes);
std::vector<std::uint8_t> job_written;
bool snoop_due = false;
std::uint32_t snoop_due_line = 0;

std::string plusarg(const char* name) {
    const char* value = Verilated::commandArgsPlusMatch(name);
    const std::string text = value ? value : "";
    const std::string prefix = std::string("+") + name + "=";
    return text.rfind(prefix, 0) == 0 ? text.substr(prefix.size()) : (text.empty() ? "" : "1");   // a flag: "1"
}

void fail(const std::string& status, const std::string& detail) {
    if (failure.empty()) {
        failure = status;
        std::cerr << status << ": " << detail << " (cycle " << ticks << ")\n";
    }
}

std::uint32_t page_word(std::uint32_t index) {
    std::uint32_t word = 0;
    for (int lane = 0; lane < 4; ++lane) word |= std::uint32_t(page_copy[4 * index + lane]) << (8 * lane);
    return word;
}

// A CPU result: the GEMM whose descriptor the program put on the page.
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

bool in_main(std::uint32_t addr) { return addr - profile.mem_base < profile.mem_bytes; }
std::uint32_t main_word(std::uint32_t addr) {
    std::uint32_t word = 0;
    for (int b = 0; b < 4; ++b) word |= std::uint32_t(main_copy.at(addr + b)) << (8 * b);
    return word;
}

// One clock cycle, with the checks of the signals before its edge.
void cycle() {
    dut->aclk = 0;
    dut->eval();
    const bool running = dut->aresetn && dut->chk_run;
    // Snoops: the one due from the last cycle's NPU write, and no other.
    if (!snoop_check) {}
    else if (running && dut->chk_snoop_valid != snoop_due)
        fail("SNOOP", snoop_due ? "an NPU write not snooped the next cycle" : "a snoop with no NPU write");
    else if (running && snoop_due && std::uint32_t(dut->chk_snoop_line) != snoop_due_line)
        fail("SNOOP", "a snoop of the wrong line");
    if (snoop_due) ++snoops;
    snoop_due = running && dut->chk_n_accept && dut->chk_n_we;
    snoop_due_line = std::uint32_t(dut->chk_n_addr) >> 2;
    if (trace && running && dut->rvfi_valid) {
        std::fprintf(trace, "%llu %08x %08x %u %u %08x %08x %x %x %08x %08x", (unsigned long long)dut->rvfi_order,
                     dut->rvfi_pc_rdata, dut->rvfi_insn, unsigned(dut->rvfi_trap), unsigned(dut->rvfi_rd_addr),
                     dut->rvfi_rd_wdata, dut->rvfi_mem_addr, unsigned(dut->rvfi_mem_rmask),
                     unsigned(dut->rvfi_mem_wmask), dut->rvfi_mem_rdata, dut->rvfi_mem_wdata);
        for (int i = 0; i < 15; ++i)
            if (dut->rvfi_csr_wvalid >> i & 1u) std::fprintf(trace, " c%03x=%08x", kCsrAddress[i], dut->rvfi_csr_wdata[i]);
        std::fputs(dut->rvfi_intr ? " intr\n" : "\n", trace);
    }
    // The data cache's answers, lookups and requests (the load check).
    if (running && dut->chk_c_rsp_valid) {
        if (dreqs.empty() || dlooked == 0) fail("STALE_LOAD", "a data-cache answer with no request looked up");
        else {
            const DReq r = dreqs.front();
            dreqs.pop_front();
            --dlooked;
            if (r.main_load) {
                ++loads_checked;
                bool ok = false;
                for (const std::uint32_t v : r.allowed) ok = ok || v == std::uint32_t(dut->chk_c_rsp_rdata);
                if (!ok) fail("STALE_LOAD", "a load of " + std::to_string(r.word) + " older than its lookup");
            }
        }
    }
    if (running && dut->chk_lookup) {
        if (dlooked >= dreqs.size()) fail("STALE_LOAD", "a lookup with no request");
        else {
            DReq& r = dreqs[dlooked++];
            r.looked = true;
            if (r.main_load && own_pending[r.word] == 0) r.allowed.push_back(main_word(r.word));
        }
    }
    if (running && dut->chk_c_accept) {
        const std::uint32_t op = dut->chk_c_op, addr = dut->chk_c_addr;
        dreqs.push_back(DReq{addr & ~3u, op == OP_LOAD && in_main(addr)});
        if (op != OP_LOAD && op != OP_LR && in_main(addr)) ++own_pending[addr & ~3u];
    }
    if (running && dut->chk_d_main_accept && dut->chk_m_d_op != OP_LOAD && dut->chk_m_d_op != OP_LR)
        --own_pending[std::uint32_t(dut->chk_m_d_addr) & ~3u];
    // The NPU's start: its descriptor and the memory as it is.
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
    // The NPU's writes: only C's bytes, each once.
    if (running && dut->chk_n_accept && dut->chk_n_we) {
        const std::uint32_t addr = std::uint32_t(dut->chk_n_addr) << 2;
        // A write is one C word: its byte enables select that word in its unit.
        if (std::uint32_t(dut->chk_n_be) != (addr & 4u ? 0xF0u : 0x0Fu))
            fail("STRAY_WRITE", "an NPU write's byte enables not its word's");
        for (int lane = 0; lane < 4; ++lane) {
            if (!job_live || !npu::in_c(job, addr + lane)) { fail("STRAY_WRITE", "an NPU write outside its C"); break; }
            if (job_error) { fail("ERROR_JOB_WRITE", "an NPU write in a job that must end with an error"); break; }
            std::uint8_t& mark = job_written[addr + lane - profile.mem_base];
            if (mark) { fail("DOUBLE_WRITE", "a C byte written twice"); break; }
            mark = 1;
        }
    }
    // Port B's and the page's writes, at this edge.
    if (dut->chk_b_we) {                       // port B's write, as a 64-bit unit
        const std::uint32_t unit = profile.mem_base + 8 * std::uint32_t(dut->chk_b_unit);
        for (int lane = 0; lane < 8; ++lane)
            if ((dut->chk_b_be >> lane) & 1u)
                main_copy.at(unit + lane) = std::uint8_t(std::uint64_t(dut->chk_b_wdata) >> (8 * lane));
        for (std::uint32_t half = 0; half < 2; ++half) {
            if (!((dut->chk_b_be >> (4 * half)) & 0xFu)) continue;
            const std::uint32_t word = unit + 4 * half;
            for (DReq& r : dreqs)              // a value a looked-up load may now return
                if (r.looked && r.main_load && r.word == word) r.allowed.push_back(main_word(word));
        }
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
    // The NPU's end: a completed job's C as the reference computes it.
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

    dut = new Vsim_npu_soc;
    dut->aresetn = 0;
    for (int i = 0; i < 8; ++i) cycle();
    dut->aresetn = 1;
    cycle();
    if (axi_read(0x3F040) != 0x4153544Eu) { std::cerr << "bad magic\n"; return 2; }
    image.resize((image.size() + 3) & ~std::size_t(3), 0);
    for (std::size_t i = 0; i < image.size(); i += 4) {
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        axi_write(std::uint32_t(i), word);
    }
    for (std::size_t i = 0; i < image.size(); i += 4) {      // every word read back, odd and even
        std::uint32_t word = 0;
        for (int b = 0; b < 4; ++b) word |= std::uint32_t(std::uint8_t(image[i + b])) << (8 * b);
        if (axi_read(std::uint32_t(i)) != word) { std::cerr << "image read-back mismatch at " << i << "\n"; return 2; }
    }
    const std::uint32_t npu_config = axi_read(0x3F058);    // {DIM, PORT_BYTES, A_STRIPS} (19.5)
    axi_write(0x3F030, tohost);
    axi_write(0x3F000, 1);

    const bool board = !plusarg("board").empty(), kernel = !plusarg("kernel").empty();
    snoop_check = plusarg("no_snoop_check").empty();
    if (!plusarg("trace").empty() && !(trace = std::fopen(plusarg("trace").c_str(), "w"))) {
        std::cerr << "cannot write " << plusarg("trace") << "\n";
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
        if (kernel && (status & 4u)) {   // the window closed: the kernel's record line ends the run
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
    const std::uint64_t cycles = read64(0x3F038);     // the cycle tohost was stored
    const std::uint32_t value = axi_read(0x3F034);
    if (board) {
        const std::uint64_t window_cycles = read64(0x3F020), window_retired = read64(0x3F028);
        std::printf("BOARD %s cycles=%llu retired=%llu window_cycles=%llu window_retired=%llu tohost=%x "
                    "tohost_cycles=%llu tohost_retired=%llu console_bytes=%zu npu_config=%u\n", result.c_str(),
                    (unsigned long long)read64(0x3F010), (unsigned long long)read64(0x3F018),
                    (unsigned long long)window_cycles, (unsigned long long)window_retired, value,
                    (unsigned long long)cycles, (unsigned long long)read64(0x3F050), console.size(), npu_config);
    }
    axi_write(0x3F000, 0);
    if (!plusarg("console").empty()) std::ofstream(plusarg("console")) << console;
    if (!board) std::printf("SOC %s cycles=%llu tohost=%x npu_jobs=%llu npu_unfinished=%llu cpu_checks=%llu "
                            "snoops=%llu loads=%llu npu_config=%u\n", result.c_str(), (unsigned long long)cycles, value,
                            (unsigned long long)npu_jobs, (unsigned long long)npu_unfinished,
                            (unsigned long long)cpu_checks, (unsigned long long)snoops,
                            (unsigned long long)loads_checked, npu_config);
    if (trace) std::fclose(trace);
    delete dut;
    return result == "PASS" ? 0 : 1;
}
