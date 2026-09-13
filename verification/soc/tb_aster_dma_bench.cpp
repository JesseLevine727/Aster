#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include "../common/dma_record.h"
#include <fcntl.h>
#include <unistd.h>
#include <iostream>
#include <random>

static void write_exclusive(const std::string& path, const std::vector<uint8_t>& bytes) {
    int fd = open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
    dma_require(fd >= 0,"cannot create new RAM evidence (existing files are preserved)");
    unsigned offset = 0;
    while (offset < bytes.size()) {
        auto n = write(fd,bytes.data()+offset,bytes.size()-offset);
        if (n <= 0) { close(fd); throw std::runtime_error("RAM evidence write failed"); }
        offset += n;
    }
    dma_require(close(fd) == 0,"RAM evidence close failed");
}
int main(int argc, char** argv) {
    try {
        Verilated::commandArgs(argc,argv); std::cout.setf(std::ios::unitbuf);
        const std::set<std::string> keys = {"kernel-start","kernel-end","results-addr","source-base","destination-base",
            "size","alignment","harts","jobs","seed","l1","sync-memory","memory-wait","line-words","line-count","boots","uart-seed"};
        std::map<std::string,uint64_t> opts; std::string ram_prefix;
        for (int i = 1; i < argc; ++i) {
            std::string key = argv[i]; if (key.rfind("+",0) == 0) continue;
            dma_require(key.rfind("--",0) == 0 && i+1 < argc,"invalid simulator option"); key.erase(0,2);
            std::string value = argv[++i];
            if (key == "ram-prefix") { dma_require(ram_prefix.empty(),"duplicate RAM path"); ram_prefix = value; }
            else { dma_require(keys.count(key) && !opts.count(key),"unknown/duplicate simulator option"); opts[key] = dma_number(value); }
        }
        dma_require(opts.size() == keys.size() && !ram_prefix.empty(),"missing simulator options");
        dma_require(opts.at("kernel-start") < opts.at("kernel-end") && opts.at("kernel-end") <= 65536,"CPU kernel outside ROM");
        dma_require(opts.at("jobs") >= 1 && opts.at("jobs") <= 8 && opts.at("boots") >= 1 && opts.at("boots") <= 16 && opts.at("alignment") < 3,
                    "invalid run length/alignment");
        const unsigned size = opts.at("size"), allocation = dma_allocation(size), alignment = opts.at("alignment");
        const uint32_t source_base = opts.at("source-base"), destination_base = opts.at("destination-base");
        const unsigned src_offset = 64+(alignment != 0), dst_offset = 64+(alignment == 1 ? 1 : alignment == 2 ? 2 : 0);
        for (auto address : {source_base,destination_base})
            dma_require(address%64 == 0 && address >= 0x10000000 && uint64_t(address)+allocation <= 0x10008000,"buffer outside shared RAM");
        dma_require(uint64_t(std::min(source_base,destination_base))+allocation <= std::max(source_base,destination_base),"overlapping buffers");
        dma_require(opts.at("results-addr")%4 == 0 && opts.at("results-addr") >= 0x10008000 &&
            opts.at("results-addr")+opts.at("jobs")*2*108*4 <= 0x1000b000,"results exceed protected runtime RAM");
        Vaster_coherent_soc d;
        d.resetn = d.host_run = d.boot_we = d.boot_addr = d.boot_wdata = d.boot_wstrb = d.host_ram_addr = 0;
        d.uart_tx_ready = 1;
        for (unsigned n = 0; n < 8; ++n) { d.clk = 0; d.eval(); d.clk = 1; d.eval(); }
        d.resetn = 1; d.clk = 0; d.eval(); d.clk = 1; d.eval(); dma_require(d.stopped,"POR not stopped");
        std::array<uint32_t,16384> memory; memory.fill(0xa5a5a5a5);
        auto index = [&](uint32_t address) { dma_require(address >= 0x10000000 && address < 0x10010000,"RAM address out of range"); return (address-0x10000000)/4; };
        auto byte = [&](uint32_t address) { return uint8_t(memory[index(address)] >> (8*(address%4))); };
        auto store = [&](uint32_t address, uint32_t data, unsigned mask) {
            auto& value = memory[index(address)];
            for (unsigned b = 0; b < 4; ++b) if (mask & (1u << b)) value = (value & ~(255u << (8*b))) | (data & (255u << (8*b)));
        };
        std::mt19937 rng(opts.at("uart-seed"));
        unsigned records_total = 0;
        for (unsigned boot = 1; boot <= opts.at("boots"); ++boot) {
            std::cout << "ASTERBOOT " << boot << '\n';
            unsigned records = 0, starts = 0, freezes = 0, primary_entries = 0, cpu_stores = 0, dma_stores = 0;
            unsigned cpu_payload_bytes = 0, dma_payload_bytes = 0, dma_reads = 0, dma_writes = 0;
            uint64_t kernel_retired = 0, kernel_first = 0, kernel_last = 0;
            std::array<uint64_t,2> lifetime{};
            std::array<std::array<uint64_t,14>,2> counters{};
            std::array<uint64_t,14> device{};
            bool running = false; std::string line;
            auto edge = [&](bool check_uart) {
                d.clk = 0; d.uart_tx_ready = opts.at("uart-seed") == 0 || rng()%4 != 0; d.eval();
                dma_require(!d.hart_trap && !d.fault_valid && !(d.hart_run & 2),"benchmark fault or released secondary");
                dma_require(!d.perf_resume && d.stop_commit != 2,"benchmark introduced resume/selective reset");
                bool active = running && !d.perf_start && !d.perf_freeze && !d.stopped;
                if (d.stopped) { running = false; counters = {}; device = {}; }
                else if (d.perf_start) {
                    dma_require(!running && starts == freezes && records == freezes && !d.dma_busy,"overlapping/unsettled method windows");
                    running = true; ++starts; counters = {}; device = {};
                    cpu_payload_bytes = dma_payload_bytes = dma_reads = dma_writes = 0;
                    kernel_retired = kernel_first = kernel_last = 0;
                    uint32_t seed = uint32_t(opts.at("seed")) ^ uint32_t(((starts-1)/2+1)*0x9e3779b9u);
                    for (unsigned i = 0; i < allocation; ++i)
                        dma_require(byte(source_base+i) == dma_source_byte(i,seed) &&
                            byte(destination_base+i) == dma_initial_destination(i,seed),"methods did not receive identical full-buffer preparation");
                } else if (d.perf_freeze) {
                    dma_require(running && !d.dma_busy && !d.dma_request_pending,"freeze before method/DMA completion");
                    running = false; ++freezes;
                } else if (active) {
                    for (unsigned h = 0; h < 2; ++h) {
                        for (unsigned i = 0; i < 14; ++i) counters[h][i] += (d.perf_events[h] >> i)&1;
                        dma_require(((d.perf_events[h] >> 1)&1) == ((d.retired >> h)&1),"retirement event misrouted");
                    }
                    for (unsigned i = 0; i < 14; ++i) device[i] += d.dma_events[i];
                    if ((d.retired & 1) && d.retired_pc[0] >= opts.at("kernel-start") && d.retired_pc[0] < opts.at("kernel-end")) {
                        if (!kernel_retired++) kernel_first = counters[0][0]; kernel_last = counters[0][0];
                    }
                }
                const unsigned method = starts ? (((starts-1)/2)&1) ^ ((starts-1)%2) : 0;
                auto payload_store = [&](uint32_t address, uint32_t data, unsigned mask, bool is_dma) {
                    if (!active) return;
                    for (unsigned b = 0; b < 4; ++b) if (mask & (1u << b)) {
                        auto target = address+b;
                        dma_require(target < source_base || target >= source_base+allocation,"method corrupted source allocation");
                        if (target < destination_base || target >= destination_base+allocation) {
                            dma_require(!is_dma,"DMA wrote outside destination allocation"); continue;
                        }
                        auto& copied = is_dma ? dma_payload_bytes : cpu_payload_bytes;
                        dma_require(method == unsigned(is_dma) && copied < size && target == destination_base+dst_offset+copied,
                            "CPU/DMA path mixed, skipped/repeated bytes or corrupted guard");
                        dma_require(uint8_t(data >> (8*b)) == byte(source_base+src_offset+copied),"payload not latest source byte"); ++copied;
                    }
                };
                dma_require(!(d.store_commit && d.dma_store_commit),"CPU/DMA store interleaving");
                if (d.store_commit) { payload_store(d.store_addr,d.store_data,d.store_mask,false); store(d.store_addr,d.store_data,d.store_mask); ++cpu_stores; }
                if (d.dma_store_commit) {
                    dma_require(active && d.backing_valid && d.backing_ready && d.backing_device && d.backing_mask,"unmeasured/nonphysical DMA payload write");
                    payload_store(d.backing_addr,d.backing_data,d.backing_mask,true); store(d.backing_addr,d.backing_data,d.backing_mask); ++dma_stores;
                }
                bool read = d.dma_request_pending && d.dma_request_ready && !d.dma_request_mask;
                bool write = d.dma_request_pending && d.dma_request_ready && d.dma_request_mask;
                dma_require(d.dma_events[0] == d.dma_busy && d.dma_events[1] == (d.dma_request_pending && !d.dma_request_ready) &&
                    d.dma_events[2] == read && d.dma_events[3] == write,"device transaction/activity event mux mismatch");
                unsigned bytes = 0; if (d.dma_store_commit) for (unsigned b = 0; b < 4; ++b) bytes += (d.backing_mask >> b)&1;
                dma_require(d.dma_events[4] == bytes && d.dma_events[5] == (d.backing_valid && d.backing_ready && d.backing_device && !d.backing_mask) &&
                    d.dma_events[6] == (d.backing_valid && d.backing_ready && d.backing_device && d.backing_mask),"device payload/maintenance event mismatch");
                if (read) { dma_require(active && d.dma_request_rdata == memory[index(d.dma_request_addr)],"stale/unmeasured DMA source read"); ++dma_reads; }
                if (write) { dma_require(active,"unmeasured DMA front write"); ++dma_writes; }
                for (unsigned h = 0; h < 2; ++h) if (d.retired & (1u << h)) ++lifetime[h];
                if ((d.retired & 1) && d.retired_pc[0] == 0) ++primary_entries;
                if (check_uart && d.uart_tx_valid && d.uart_tx_ready) {
                    dma_require(records < 2*opts.at("jobs"),"trailing UART");
                    line += char(d.uart_tx_data); dma_require(line.size() <= 65536,"oversized record");
                    if (line.back() == '\n') {
                        auto r = validate_dma_record(line); const auto& n = r.n;
                        dma_require(!running && starts == freezes && freezes == records+1 && n.at("job") == records/2+1 && n.at("pass") == records%2+1,
                                    "record outside frozen method/order");
                        for (const auto& mapping : std::map<std::string,std::string>{{"size","size"},{"jobs","jobs"},{"harts","harts"},
                            {"base_seed","seed"},{"l1","l1"},{"sync_memory","sync-memory"},{"memory_wait","memory-wait"},
                            {"line_words","line-words"},{"line_count","line-count"}})
                            dma_require(n.at(mapping.first) == opts.at(mapping.second),"firmware/model configuration mismatch");
                        dma_require(r.alignment == alignment && r.method == method && n.at("source_addr") == source_base+src_offset &&
                            n.at("destination_addr") == destination_base+dst_offset,"wrong ELF buffer/method identity");
                        for (unsigned h = 0; h < 2; ++h) for (unsigned i = 0; i < 14; ++i)
                            dma_require(n.at("h"+std::to_string(h)+"_"+dma_cpu_events[i]) == counters[h][i],"CPU register counter differs from observed window");
                        for (unsigned i = 0; i < 14; ++i) dma_require(n.at("dma_"+dma_device_events[i]) == device[i],"DMA register counter differs from observed window");
                        dma_require(cpu_payload_bytes == (method ? 0u : size) && dma_payload_bytes == (method ? size : 0u) &&
                            (method ? kernel_retired == 0 : kernel_retired > 0),"copy implementation/actual kernel retirement mismatch");
                        dma_require(n.at("raw_dma_status") == d.dma_status && n.at("raw_dma_bytes_done") == d.dma_bytes_done &&
                            n.at("raw_dma_job_cycles") == d.dma_job_cycles,"firmware raw engine diagnostics are not actual registers");
                        auto expected = dma_reference(size,alignment,n.at("seed"));
                        for (unsigned i = 0; i < allocation; ++i) dma_require(byte(destination_base+i) == expected[i] &&
                            byte(source_base+i) == dma_source_byte(i,n.at("seed")),"complete logical buffers/guards differ from oracle");
                        const std::array<uint32_t,24> metadata = {uint32_t(n.at("job")),method,uint32_t(n.at("pass")),uint32_t(n.at("seed")),size,
                            src_offset,dst_offset,source_base+src_offset,destination_base+dst_offset,allocation,0,uint32_t(n.at("raw_dma_status")),
                            uint32_t(n.at("raw_dma_bytes_done")),uint32_t(n.at("raw_dma_job_cycles")),uint32_t(n.at("raw_dma_job_cycles") >> 32),
                            uint32_t(opts.at("harts")),uint32_t(4+opts.at("l1")+2*opts.at("sync-memory")),31250000,uint32_t(opts.at("line-words")),
                            uint32_t(opts.at("line-count")),uint32_t(opts.at("memory-wait")),0,0,0};
                        unsigned result_index = index(opts.at("results-addr"))+records*108;
                        for (unsigned i = 0; i < 108; ++i) {
                            uint32_t want;
                            if (i < 24) want = metadata[i];
                            else { unsigned bank = (i-24)/28, field = (i-24)%28;
                                uint64_t value = bank < 2 ? counters[bank][field/2] : device[field/2];
                                want = value >> (32*(field%2)); }
                            dma_require(memory[result_index+i] == want,"RAM-published method metadata/counters differ from actual observation");
                        }
                        std::cout << line << "DMA_OBS {\"boot\":" << boot << ",\"job\":" << records/2+1 << ",\"pass\":" << records%2+1
                                  << ",\"method\":" << method << ",\"cpu_counts\":[";
                        for (unsigned h = 0; h < 2; ++h) { std::cout << (h ? ",[" : "[");
                            for (unsigned i = 0; i < 14; ++i) std::cout << (i ? "," : "") << counters[h][i]; std::cout << ']'; }
                        std::cout << "],\"dma_counts\":[";
                        for (unsigned i = 0; i < 14; ++i) std::cout << (i ? "," : "") << device[i];
                        std::cout << "],\"cpu_payload_bytes\":" << cpu_payload_bytes << ",\"dma_payload_bytes\":" << dma_payload_bytes
                                  << ",\"dma_front_reads\":" << dma_reads << ",\"dma_front_writes\":" << dma_writes
                                  << ",\"kernel_retired\":" << kernel_retired << ",\"kernel_first\":" << kernel_first << ",\"kernel_last\":" << kernel_last << "}\n";
                        ++records; ++records_total; line.clear();
                    }
                }
                d.clk = 1; d.eval();
                dma_require(d.dma_counting == running,"DMA common-window running state mismatch");
                for (unsigned i = 0; i < 14; ++i) dma_require(d.dma_counters[i] == device[i],"DMA counter bank differs on actual increment edge");
            };
            d.host_run = 1; unsigned quiet = 0;
            for (uint64_t cycle = 0; cycle < 1000000000ull && quiet < 512; ++cycle) {
                edge(true); if (records == 2*opts.at("jobs")) ++quiet;
            }
            dma_require(quiet == 512 && line.empty() && !running && !d.dma_busy && primary_entries == 1 && !lifetime[1],"incomplete run / unexpected lifecycle");
            d.host_run = 0;
            for (unsigned n = 0; !d.stopped && n < 2000000; ++n) edge(false);
            dma_require(d.stopped && !d.hart_run && !d.fabric_busy && !d.flush_active && !d.reservations && !d.dma_busy,"unsafe/incomplete global stop");
            std::vector<uint8_t> ram; ram.reserve(65536);
            for (unsigned i = 0; i < memory.size(); ++i) {
                d.host_ram_addr = 4*i; edge(false); edge(false);
                dma_require(d.host_ram_rdata == memory[i] && !d.backing_valid,"stopped RAM lost/invented architectural CPU/DMA bytes");
                for (unsigned b = 0; b < 4; ++b) ram.push_back(uint8_t(d.host_ram_rdata >> (8*b)));
            }
            write_exclusive(ram_prefix+".boot"+std::to_string(boot)+".ram",ram);
            std::cout << "ASTERSTOP {\"boot\":" << boot << ",\"records\":" << records << ",\"ram_bytes\":65536,\"cpu_stores\":" << cpu_stores
                      << ",\"dma_stores\":" << dma_stores << ",\"lifetime_retired\":[" << lifetime[0] << ',' << lifetime[1] << "]}\n";
        }
        std::cout << "PASS: DMA benchmark boots=" << opts.at("boots") << " records=" << records_total
                  << "; exact CPU/DMA payload ownership, full output/guards/RAM, actual kernel and 42-counter windows\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << "FAIL: " << e.what() << '\n'; return 1; }
}
