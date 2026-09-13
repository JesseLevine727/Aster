// Independent AXI/real-serial check of paired CPU/DMA windows. UART delivery
// can lag several completed windows: observations are queued at FREEZE, never
// compared with whichever live counter bank happens to exist at newline time.
#include "../common/linux_bus.h"
#include "../common/dma_record.h"
#include "Vaster_pynq_linux___024root.h"
#include "verilated.h"
#include <fstream>
#include <iostream>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_L1 1
#endif
struct Window {
    std::array<std::array<uint64_t,14>,2> cpu{};
    std::array<uint64_t,14> dma{};
    uint64_t kernel = 0, first = 0, last = 0, job_cycles = 0;
    unsigned cpu_bytes = 0, dma_bytes = 0, status = 0, bytes_done = 0;
};
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc,argv); std::cout.setf(std::ios::unitbuf);
        require(argc == 12,"usage: linux_dma_bench firmware size alignment jobs boots seed kernel_start kernel_end results source destination");
        const unsigned size = dma_number(argv[2]), alignment = dma_number(argv[3]), jobs = dma_number(argv[4]), boots = dma_number(argv[5]);
        const uint32_t seed = dma_number(argv[6]), kernel_start = dma_number(argv[7]), kernel_end = dma_number(argv[8]);
        const uint32_t results_addr = dma_number(argv[9]), source = dma_number(argv[10]), destination = dma_number(argv[11]);
        const unsigned allocation = dma_allocation(size), src_offset = 64+(alignment != 0), dst_offset = 64+(alignment == 1 ? 1 : alignment == 2 ? 2 : 0);
        require(alignment < 3 && jobs >= 1 && jobs <= 8 && boots >= 1 && boots <= 16 && kernel_start < kernel_end && kernel_end <= 65536,
                "invalid Linux DMA benchmark options");
        require(source == 0x10001000 && destination == 0x10005080 && results_addr >= 0x10008000 && results_addr+jobs*2*432 <= 0x1000b000 && !(results_addr&3),
                "wrong fixed DMA buffer/results layout");
        std::ifstream input(argv[1]); require(bool(input),"missing actual firmware");
        std::vector<uint32_t> image;
        for (std::string word; input >> word;) { require(word.size() == 8,"invalid ROM word"); image.push_back(dma_number("0x"+word)); }
        require(image.size() == 16384,"incomplete boot ROM");
        Bus b; std::array<uint32_t,16384> memory{};
        auto index = [](uint32_t addr) { require(addr >= 0x10000000 && addr < 0x10010000,"RAM observation outside memory"); return (addr-0x10000000)/4; };
        auto byte = [&](uint32_t addr) { return uint8_t(memory[index(addr)] >> (8*(addr%4))); };
        Window current; std::vector<Window> windows;
        unsigned starts = 0; bool running = false;
        uint64_t lifetime[2]{}; unsigned total_cpu_stores = 0, total_dma_stores = 0;
        b.before_edge = [&]() {
            const auto* r = b.dut.rootp;
            require(!(r->aster_pynq_linux__DOT__hart_trap & r->aster_pynq_linux__DOT__hart_run) &&
                !r->aster_pynq_linux__DOT__atomic_fault_valid && !(r->aster_pynq_linux__DOT__hart_run & 2), "DMA bench fault / secondary released");
            bool active = running && !r->aster_pynq_linux__DOT__coherent_perf_start && !r->aster_pynq_linux__DOT__coherent_perf_freeze &&
                !r->aster_pynq_linux__DOT__coherent_stopped;
            require(!r->aster_pynq_linux__DOT__coherent_perf_resume,"unexpected resumed window");
            if (r->aster_pynq_linux__DOT__coherent_stopped) running = false;
            else if (r->aster_pynq_linux__DOT__coherent_perf_start) {
                require(!running && starts == windows.size() && starts < jobs*2 && !r->aster_pynq_linux__DOT__dma_busy,"overlapping/incomplete paired windows");
                current = {}; running = true; ++starts;
                uint32_t job_seed = seed ^ uint32_t(((starts-1)/2+1)*0x9e3779b9u);
                for (unsigned i = 0; i < allocation; ++i) require(byte(source+i) == dma_source_byte(i,job_seed) &&
                    byte(destination+i) == dma_initial_destination(i,job_seed), "CPU and DMA did not receive identical prepared buffers");
            } else if (active) {
                for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i)
                    current.cpu[h][i] += (r->aster_pynq_linux__DOT__coherent_perf_events[h] >> i)&1;
                for (unsigned i = 0; i < 14; ++i) current.dma[i] += r->aster_pynq_linux__DOT__dma_events[i];
                const auto pc = r->aster_pynq_linux__DOT__hart_pc[0];
                if ((r->aster_pynq_linux__DOT__hart_retired & 1) && pc >= kernel_start && pc < kernel_end) {
                    if (!current.kernel++) current.first = current.cpu[0][0]; current.last = current.cpu[0][0];
                }
            }
            unsigned method = starts ? (((starts-1)/2)&1) ^ ((starts-1)%2) : 0;
            auto store = [&](uint32_t addr, uint32_t data, unsigned mask, bool device) {
                auto& value = memory[index(addr)];
                for (unsigned lane = 0; lane < 4; ++lane) if (mask & (1u << lane)) {
                    if (active) {
                        auto target = addr+lane;
                        require(target < source || target >= source+allocation,"copy modified source buffer");
                        if (target >= destination && target < destination+allocation) {
                            auto& copied = device ? current.dma_bytes : current.cpu_bytes;
                            require(method == unsigned(device) && copied < size && target == destination+dst_offset+copied &&
                                uint8_t(data >> (lane*8)) == byte(source+src_offset+copied), "wrong CPU/DMA payload ownership/byte/guard"); ++copied;
                        } else require(!device,"DMA wrote outside payload allocation");
                    } else require(!device,"unmeasured DMA payload");
                    value = (value & ~(255u << (8*lane))) | (data & (255u << (8*lane)));
                }
            };
            require(!(r->aster_pynq_linux__DOT__coherent_store_commit && r->aster_pynq_linux__DOT__dma_store_commit),"CPU/DMA stores interleaved");
            if (r->aster_pynq_linux__DOT__coherent_store_commit) {
                store(r->aster_pynq_linux__DOT__coherent_store_addr,r->aster_pynq_linux__DOT__coherent_store_data,r->aster_pynq_linux__DOT__coherent_store_mask,false);
                ++total_cpu_stores;
            }
            if (r->aster_pynq_linux__DOT__dma_store_commit) {
                require(r->aster_pynq_linux__DOT__dma_backing_valid && r->aster_pynq_linux__DOT__dma_backing_ready &&
                    r->aster_pynq_linux__DOT__dma_backing_device,"nonphysical DMA commit");
                store(r->aster_pynq_linux__DOT__dma_backing_addr,r->aster_pynq_linux__DOT__dma_backing_data,r->aster_pynq_linux__DOT__dma_backing_mask,true);
                ++total_dma_stores;
            }
            bool read = r->aster_pynq_linux__DOT__dma_request_pending && r->aster_pynq_linux__DOT__dma_request_ready && !r->aster_pynq_linux__DOT__dma_request_mask;
            if (read) require(active && r->aster_pynq_linux__DOT__dma_request_rdata == memory[index(r->aster_pynq_linux__DOT__dma_request_addr)],"stale/unmeasured DMA source read");
            if (r->aster_pynq_linux__DOT__coherent_perf_freeze) {
                require(running && !r->aster_pynq_linux__DOT__dma_busy && !r->aster_pynq_linux__DOT__dma_request_pending,"freeze before copy completion");
                running = false;
                require(current.cpu_bytes == (method ? 0u : size) && current.dma_bytes == (method ? size : 0u) &&
                    (method ? current.kernel == 0 : current.kernel > 0),"copy implementation did not execute");
                uint32_t job_seed = seed ^ uint32_t(((starts-1)/2+1)*0x9e3779b9u); auto output = dma_reference(size,alignment,job_seed);
                for (unsigned i = 0; i < allocation; ++i) require(byte(source+i) == dma_source_byte(i,job_seed) && byte(destination+i) == output[i],
                    "frozen full source/destination/guard oracle mismatch");
                current.status = r->aster_pynq_linux__DOT__dma_status; current.bytes_done = r->aster_pynq_linux__DOT__dma_bytes_done;
                current.job_cycles = r->aster_pynq_linux__DOT__dma_job_cycles; windows.push_back(current);
            }
            for (unsigned h = 0; h < 2; ++h) if (r->aster_pynq_linux__DOT__hart_retired & (1u << h)) ++lifetime[h];
        };
        b.reset();
        require(b.read(0x18) == 0x41535452 && b.read(0x1c) == 0x70001 && b.read(0x20) == 31250000 &&
            b.read(0x24) == ASTER_HART_COUNT && b.read(0x44) == (5u | (ASTER_L1 << 1)) && b.read(0x80) == 1 && b.read(0x9c) == 5,
            "wrong physical-shell identity");
        for (unsigned i = 0; i < image.size(); ++i) b.write(0x10000+i*4,image[i],i%3);
        for (unsigned boot = 1; boot <= boots; ++boot) {
            current = {}; windows.clear(); starts = 0; lifetime[0] = lifetime[1] = 0;
            require(b.read(0x40) == 1 && b.read(0x84) == 0,"warm boot before full STOPPED");
            b.write(0,1,boot%3); b.write(0x10000,0xffffffff,1,15,2); b.read(0x20000,2);
            b.idle(300000); // Real serial may already be queued; credits bound it.
            std::string line; unsigned records = 0, raw_bytes = 0;
            for (unsigned polls = 0; polls < 100000000 && records < jobs*2; ++polls) {
                auto count = b.read(0x0c); require(count <= 128,"invalid receive FIFO occupancy");
                for (unsigned i = 0; i < count; ++i) {
                    const auto data = b.read(8); require((data & 0xffffff00) == 0x80000000,"serial count/pop mismatch");
                    line += char(data); ++raw_bytes; require(line.size() <= 65536,"oversized serial record");
                    if (line.back() != '\n') continue;
                    require(records < windows.size() && records < jobs*2,"serial lacks a completed observed window");
                    auto r = validate_dma_record(line); const auto& n = r.n; const auto& observed = windows[records];
                    require(n.at("job") == records/2+1 && n.at("pass") == records%2+1 && n.at("size") == size && n.at("jobs") == jobs &&
                        n.at("base_seed") == seed && r.alignment == alignment && n.at("harts") == ASTER_HART_COUNT && n.at("l1") == ASTER_L1 &&
                        n.at("sync_memory") == 1 && n.at("memory_wait") == 1 && n.at("line_words") == 4 && n.at("line_count") == 16 &&
                        n.at("source_addr") == source+src_offset && n.at("destination_addr") == destination+dst_offset,"serial/configuration mismatch");
                    for (unsigned h = 0; h < 2; ++h) for (unsigned e = 0; e < 14; ++e)
                        require(n.at("h"+std::to_string(h)+"_"+dma_cpu_events[e]) == observed.cpu[h][e],"serial CPU counters differ from queued actual window");
                    for (unsigned e = 0; e < 14; ++e) require(n.at("dma_"+dma_device_events[e]) == observed.dma[e],"serial DMA counters differ from queued actual window");
                    require(n.at("raw_dma_status") == observed.status && n.at("raw_dma_bytes_done") == observed.bytes_done &&
                        n.at("raw_dma_job_cycles") == observed.job_cycles,"raw engine diagnostics differ from freeze-time observation");
                    const std::array<uint32_t,24> metadata = {uint32_t(n.at("job")),r.method,uint32_t(n.at("pass")),uint32_t(n.at("seed")),size,
                        src_offset,dst_offset,source+src_offset,destination+dst_offset,allocation,0,observed.status,observed.bytes_done,
                        uint32_t(observed.job_cycles),uint32_t(observed.job_cycles >> 32),ASTER_HART_COUNT,6+ASTER_L1,31250000,4,16,1,0,0,0};
                    for (unsigned w = 0; w < 108; ++w) {
                        uint32_t expected;
                        if (w < 24) expected = metadata[w];
                        else { unsigned bank = (w-24)/28, field = (w-24)%28;
                            uint64_t value = bank < 2 ? observed.cpu[bank][field/2] : observed.dma[field/2]; expected = value >> (32*(field%2)); }
                        require(memory[index(results_addr)+records*108+w] == expected,"RAM-published record differs from actual frozen observation");
                    }
                    std::cout << line; line.clear(); ++records;
                }
                require(!(b.read(4)&0x1a),"serial/core failure");
            }
            require(records == jobs*2 && windows.size() == records && !running && line.empty(),"incomplete paired serial capture");
            b.idle(2048);
            require(b.read(0x0c) == 0 && b.read(0x10) == raw_bytes && b.read(0x14) == raw_bytes && b.read(4) == 1 && b.read(0x28) == 1 &&
                lifetime[0] > 100 && lifetime[1] == 0,"trailing serial / missing primary / unexpected secondary");
            const auto& last = windows.back();
            require(b.read(0x84) == last.status && b.read(0x88) == last.bytes_done && b.read(0x8c) == 0 && b.read(0x98) == 0,"terminal host DMA diagnostic mismatch");
            for (unsigned i = 0; i < 14; ++i) {
                auto lo = b.read(0xa0+i*8), hi = b.read(0xa4+i*8);
                require((uint64_t(hi) << 32 | lo) == last.dma[i],"host DMA bank differs from final serial/RAM/observed method");
            }
            b.write(0,0); bool stopped = false;
            for (unsigned n = 0; n < 100000; ++n) if (b.read(0x40) == 1) { stopped = true; break; }
            require(stopped && b.read(0) == 0 && b.read(4) == 0 && b.read(0x28) == 0 && b.read(0x84) == 0,"unsafe final STOPPED");
            for (unsigned i = 0; i < memory.size(); ++i) require(b.read(0x20000+i*4) == memory[i],"AXI stopped RAM differs from complete architectural CPU/DMA store oracle");
            std::cout << "PASS: DMA AXI/serial benchmark boot=" << boot << " size=" << size << " alignment=" << alignment
                      << " cache=" << ASTER_L1 << " records=" << records << " serial_bytes=" << raw_bytes << " retained_RAM=65536\n";
        }
        std::cout << "PASS: DMA Linux paired windows, queued freeze-time observations, actual kernel/payload ownership, full output/guards/RAM; CPU stores="
                  << total_cpu_stores << " DMA stores=" << total_dma_stores << '\n';
        return 0;
    } catch (const std::exception& error) { std::cerr << "FAIL: " << error.what() << '\n'; return 1; }
}
