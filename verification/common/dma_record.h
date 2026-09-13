#ifndef ASTER_DMA_RECORD_H
#define ASTER_DMA_RECORD_H
#include <array>
#include <cstdint>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

inline void dma_require(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
static const std::array<std::string,14> dma_cpu_events = {"cycles","retired","memory","i_access","i_miss","d_access","d_miss",
    "backing","atomic","sc_success","sc_failure","intervention","invalidation","writeback"};
static const std::array<std::string,14> dma_device_events = {"busy","wait","reads","writes","bytes","backing_reads","backing_writes",
    "forwards","dirty_words","invalidations","success","aborts","errors","rejected"};
static const std::array<std::string,3> dma_alignments = {"aligned","same_offset","different_offset"};
inline uint64_t dma_number(const std::string& value) {
    dma_require(!value.empty() && value.size() <= 22, "invalid numeric length");
    bool hex = value.rfind("0x",0) == 0;
    dma_require(hex ? value.size() > 2 : value.size() == 1 || value[0] != '0', "noncanonical unsigned number");
    for (unsigned i = hex ? 2 : 0; i < value.size(); ++i)
        dma_require((value[i] >= '0' && value[i] <= '9') || (hex && value[i] >= 'a' && value[i] <= 'f'), "invalid numeric character");
    std::size_t end; auto result = std::stoull(value,&end,hex ? 16 : 10);
    dma_require(end == value.size(), "numeric trailing characters"); return result;
}
inline unsigned dma_allocation(unsigned size) { dma_require(size <= 8192, "oversized DMA study copy"); return (size+194)&~63u; }
inline uint8_t dma_source_byte(unsigned i, uint32_t seed) {
    return uint8_t(seed >> (8*(i%4))) ^ uint8_t(i*73) ^ uint8_t(i/8);
}
inline uint8_t dma_initial_destination(unsigned i, uint32_t seed) { return uint8_t(0xa5 ^ i ^ (seed >> 16)); }
inline std::vector<uint8_t> dma_reference(unsigned size, unsigned alignment, uint32_t seed) {
    dma_require(alignment < 3, "invalid DMA alignment");
    unsigned src = 64+(alignment != 0), dst = 64+(alignment == 1 ? 1 : alignment == 2 ? 2 : 0);
    std::vector<uint8_t> result(dma_allocation(size));
    for (unsigned i = 0; i < result.size(); ++i)
        result[i] = i >= dst && i < dst+size ? dma_source_byte(src+i-dst,seed) : dma_initial_destination(i,seed);
    return result;
}
inline std::string dma_hex(const std::vector<uint8_t>& bytes) {
    const char* digits = "0123456789abcdef"; std::string result; result.reserve(bytes.size()*2);
    for (auto byte : bytes) { result += digits[byte >> 4]; result += digits[byte & 15]; }
    return result;
}
struct DmaRecord { std::map<std::string,uint64_t> n; unsigned method, alignment; std::string output; };
inline DmaRecord validate_dma_record(std::string line) {
    dma_require(line.size() <= 65536 && !line.empty() && line.back() == '\n' &&
        line.find('\n') == line.size()-1 && line.find('\r') == std::string::npos && line.rfind("ASTERBENCH,",0) == 0,
        "incomplete/oversized/noncanonical v5 line");
    line.pop_back(); line.erase(0,11); dma_require(!line.empty() && line.back() != ',', "empty field");
    std::map<std::string,std::string> fields;
    std::istringstream input(line); std::string field;
    while (std::getline(input,field,',')) {
        auto equals = field.find('=');
        dma_require(equals != std::string::npos && equals && equals+1 < field.size() && field.find('=',equals+1) == std::string::npos,
                    "malformed key/value field");
        dma_require(fields.emplace(field.substr(0,equals),field.substr(equals+1)).second, "duplicate field");
    }
    std::set<std::string> numbers = {"version","size","jobs","job","pass","base_seed","seed","harts","workers","source_offset",
        "destination_offset","buffer_bytes","source_addr","destination_addr","errors","source_errors","destination_errors",
        "driver_result","clock_hz","l1","sync_memory","line_words","line_count","memory_wait","cpu_abi","dma_abi",
        "dma_counter_abi","raw_dma_status","raw_dma_bytes_done","raw_dma_job_cycles"};
    std::set<std::string> wide = {"raw_dma_job_cycles"};
    for (unsigned h = 0; h < 2; ++h) for (const auto& event : dma_cpu_events) wide.insert("h"+std::to_string(h)+"_"+event);
    for (const auto& event : dma_device_events) wide.insert("dma_"+event);
    numbers.insert(wide.begin(),wide.end());
    const std::set<std::string> strings = {"name","window","policy","status","method","order","alignment","output"};
    dma_require(fields.size() == numbers.size()+strings.size(), "missing/unknown fields");
    for (const auto& key : strings) dma_require(fields.count(key), "missing text field");
    dma_require(fields.at("name") == "dma_memcpy" && fields.at("window") == "setup_copy_complete" &&
        fields.at("policy") == "prepared_reinitialize" && fields.at("status") == "PASS", "wrong DMA window/name/status");
    DmaRecord r{{},fields.at("method") == "dma" ? 1u : 0u,3,fields.at("output")};
    dma_require(fields.at("method") == "cpu" || fields.at("method") == "dma", "unknown method");
    for (unsigned i = 0; i < 3; ++i) if (fields.at("alignment") == dma_alignments[i]) r.alignment = i;
    dma_require(r.alignment < 3, "unknown alignment");
    for (const auto& key : numbers) {
        dma_require(fields.count(key), "missing numeric field"); r.n[key] = dma_number(fields.at(key));
        dma_require(wide.count(key) || r.n[key] <= UINT32_MAX, "32-bit metadata overflow");
    }
    const auto& n = r.n;
    dma_require(n.at("version") == 5 && n.at("jobs") >= 1 && n.at("jobs") <= 8 && n.at("job") >= 1 && n.at("job") <= n.at("jobs") &&
        n.at("pass") >= 1 && n.at("pass") <= 2, "invalid version/job/pass");
    dma_require(r.method == (((n.at("job")-1)&1) ^ (n.at("pass")-1)) &&
        fields.at("order") == (n.at("job")&1 ? "cpu_dma" : "dma_cpu"), "wrong paired method order");
    dma_require(n.at("seed") == uint32_t(n.at("base_seed") ^ uint32_t(n.at("job")*0x9e3779b9u)), "wrong job seed");
    dma_require(n.at("buffer_bytes") == dma_allocation(n.at("size")) && n.at("source_offset") == 64+(r.alignment != 0) &&
        n.at("destination_offset") == 64+(r.alignment == 1 ? 1 : r.alignment == 2 ? 2 : 0), "bad copy/guard configuration");
    dma_require(n.at("harts") >= 1 && n.at("harts") <= 2 && n.at("workers") == 1 && n.at("l1") <= 1 && n.at("sync_memory") <= 1 &&
        n.at("memory_wait") >= n.at("sync_memory") && n.at("memory_wait") <= 1024, "invalid worker/cache/timing configuration");
    for (const auto& key : {"line_words","line_count"})
        dma_require(n.at(key) >= (n.at("l1") ? 2u : 1u) && n.at(key) <= 1024 && !(n.at(key)&(n.at(key)-1)), "invalid cache geometry");
    auto source_base = n.at("source_addr")-n.at("source_offset");
    auto destination_base = n.at("destination_addr")-n.at("destination_offset");
    for (auto base : {source_base,destination_base})
        dma_require(base >= 0x10000000 && base <= 0x10008000-n.at("buffer_bytes") && base%64 == 0, "guard allocation outside shared RAM");
    dma_require(std::min(source_base,destination_base)+n.at("buffer_bytes") <= std::max(source_base,destination_base), "overlapping allocations");
    dma_require(n.at("cpu_abi") == 4 && n.at("dma_abi") == 1 && n.at("dma_counter_abi") == 5 && n.at("clock_hz") == 31250000,
        "bad hardware clock/ABI");
    dma_require(!n.at("errors") && !n.at("source_errors") && !n.at("destination_errors") && !n.at("driver_result"), "firmware/driver errors");
    dma_require(r.output == dma_hex(dma_reference(n.at("size"),r.alignment,n.at("seed"))), "full independent byte/guard oracle mismatch");
    dma_require(n.at("h0_cycles") > 0 && n.at("h0_cycles") == n.at("h1_cycles") && n.at("h0_retired") && n.at("h0_memory"), "no actual paired window/work");
    const auto cycles = n.at("h0_cycles");
    for (unsigned h = 0; h < 2; ++h) {
        const std::string prefix = "h"+std::to_string(h)+"_";
        for (const auto& event : dma_cpu_events) dma_require(n.at(prefix+event) <= cycles, "CPU event exceeds per-edge bound");
        dma_require(n.at(prefix+"i_miss") <= n.at(prefix+"i_access") && n.at(prefix+"d_miss") <= n.at(prefix+"d_access"), "misses exceed accesses");
        for (const auto& event : {"atomic","sc_success","sc_failure","intervention","invalidation"})
            dma_require(n.at(prefix+event) == 0, "copy emitted peer/atomic events");
        if (!n.at("l1")) for (const auto& event : {"i_access","i_miss","d_access","d_miss","writeback"})
            dma_require(n.at(prefix+event) == 0, "cache-off CPU cache events");
    }
    for (unsigned i = 1; i < 14; ++i) dma_require(n.at("h1_"+dma_cpu_events[i]) == 0, "held-reset secondary performed work");
    if (!r.method) {
        for (const auto& event : dma_device_events) dma_require(n.at("dma_"+event) == 0, "CPU baseline has DMA activity");
        dma_require(n.at("raw_dma_status") == 0, "CPU method lacks idle ACK");
    } else {
        dma_require(n.at("dma_bytes") == n.at("size") && n.at("dma_success") == 1 && !n.at("dma_aborts") &&
            !n.at("dma_errors") && !n.at("dma_rejected") && n.at("raw_dma_status") == 2 && n.at("raw_dma_bytes_done") == n.at("size") &&
            n.at("raw_dma_job_cycles") == n.at("dma_busy"), "bad actual DMA completion/accounting");
        unsigned count = 0, left = n.at("size"), src = n.at("source_offset"), dst = n.at("destination_offset");
        while (left) { unsigned chunk = (src%4 == 0 && dst%4 == 0 && left >= 4) ? 4 : 1; left -= chunk; src += chunk; dst += chunk; ++count; }
        dma_require(n.at("dma_reads") == count && n.at("dma_writes") == count &&
            (n.at("dma_busy") > 0) == (n.at("size") > 0) && n.at("dma_wait") <= n.at("dma_busy") &&
            n.at("dma_busy") <= cycles && count*2 <= n.at("dma_busy"), "wrong transaction/busy totals");
        dma_require(n.at("dma_dirty_words")%n.at("line_words") == 0 &&
            n.at("dma_dirty_words")/n.at("line_words") <= n.at("dma_invalidations") && n.at("dma_invalidations") <= count*2,
            "invalid/incomplete device line maintenance");
        dma_require(n.at("dma_forwards") <= count && n.at("dma_backing_reads") == count-n.at("dma_forwards") &&
            n.at("dma_backing_writes") == count+n.at("dma_dirty_words"), "payload/maintenance attribution mismatch");
        if (!n.at("l1")) dma_require(!n.at("dma_forwards") && !n.at("dma_dirty_words") && !n.at("dma_invalidations"), "cache-off device cache events");
        for (const auto& event : dma_device_events) {
            const auto count_event = n.at("dma_"+event);
            const unsigned limit = event == "bytes" ? 4 : event == "invalidations" ? 2 : 1;
            // Division form avoids overflow for adversarial 64-bit records.
            dma_require(count_event/limit <= cycles && (count_event/limit < cycles || count_event%limit == 0), "DMA event exceeds per-edge bound");
        }
    }
    auto remaining = cycles;
    for (const auto& key : {"h0_backing","h1_backing","dma_backing_reads","dma_backing_writes"}) {
        dma_require(n.at(key) <= remaining, "multiple accepted backing events per cycle"); remaining -= n.at(key);
    }
    return r;
}
#endif
