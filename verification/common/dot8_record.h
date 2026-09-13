#pragma once
#include <array>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

static void dot8_require(bool ok,const char* why) {if(!ok)throw std::runtime_error(why);}
static const std::array<std::string,14> dot8_cpu_events={"cycles","retired","memory","i_access","i_miss","d_access","d_miss",
    "backing","atomic","sc_success","sc_failure","intervention","invalidation","writeback"};
static const std::array<std::string,14> dot8_dma_events={"busy","wait","reads","writes","bytes","backing_reads","backing_writes",
    "forwards","dirty_words","invalidations","success","aborts","errors","rejected"};
static const std::array<std::string,4> dot8_compute_events={"accept","wait","complete","retired"};
static const std::array<std::string,3> dot8_names={"dot","fir","gemm"};
static std::uint64_t dot8_number(const std::string& text) {
    dot8_require(!text.empty() && text.size()<=22,"invalid unsigned number length");
    unsigned base=text.rfind("0x",0)==0?16:10,first=base==16?2:0;
    dot8_require(text.size()>first && (base==16 || text.size()==1 || text[0]!='0'),"noncanonical number");
    dot8_require(text.find_first_not_of(base==16?"0123456789abcdef":"0123456789",first)==std::string::npos,"invalid unsigned digits");
    return std::stoull(text.substr(first),nullptr,base);
}
static std::uint8_t dot8_input(unsigned i,std::uint32_t seed,unsigned bank) {
    return (seed>>((i%4)*8))^(i*73u)^(i/8)^(bank*0x5bu);
}
static std::uint32_t dot8_guard(unsigned i,std::uint32_t seed) {return 0x6d5a0000u^(i*0x01010101u)^seed;}
static unsigned dot8_outputs(unsigned kind) {return kind==0?1:kind==1?8:15;}
static unsigned dot8_a_used(unsigned kind,unsigned k) {return kind==2?3*k:kind==1?k+7:k;}
static unsigned dot8_b_used(unsigned kind,unsigned k) {return kind==2?5*k:k;}
static unsigned dot8_allocation(unsigned used) {return (used+194)&~63u;}
static std::array<std::uint32_t,48> dot8_reference(unsigned kind,unsigned k,unsigned alignment,std::uint32_t seed) {
    std::array<std::uint32_t,48> result{};
    for(unsigned i=0;i<48;++i)result[i]=dot8_guard(i,seed);
    for(unsigned out=0;out<dot8_outputs(kind);++out) {
        std::int64_t sum=0;
        for(unsigned n=0;n<k;++n) {
            unsigned ai=kind==2?(out/5)*k+n:kind==1?out+n:n,bi=kind==2?n*5+out%5:n;
            int a=dot8_input(64+alignment+ai,seed,0),b=dot8_input(64+2*alignment+bi,seed,1);
            if(a>=128)a-=256;
            if(b>=128)b-=256;
            sum+=std::int64_t(a)*b;
        }
        result[16+out]=std::uint32_t(std::uint64_t(sum));
    }
    return result;
}
struct Dot8Record {
    std::map<std::string,std::uint64_t> numbers;
    std::map<std::string,std::string> strings;
    std::vector<std::uint32_t> words;
    std::array<std::uint32_t,48> output;
};
static Dot8Record dot8_record(const std::map<std::string,std::uint64_t>& opts,unsigned record,const std::array<std::uint64_t,50>& counts) {
    Dot8Record r;auto& n=r.numbers;auto& s=r.strings;
    unsigned job=record/2+1,pass=record%2+1,method=((job-1)&1)^(pass-1),kind=opts.at("kind"),k=opts.at("k"),al=opts.at("alignment");
    std::uint32_t seed=std::uint32_t(opts.at("seed"))^(job*0x9e3779b9u);
    unsigned au=dot8_a_used(kind,k),bu=dot8_b_used(kind,k),ab=dot8_allocation(au),bb=dot8_allocation(bu),out=dot8_outputs(kind);
    n={{"version",6},{"k",k},{"rows",kind==2?3u:kind==1?8u:1u},{"cols",kind==2?5u:1u},{"outputs",out},
       {"jobs",opts.at("jobs")},{"job",job},{"pass",pass},{"base_seed",opts.at("seed")},{"seed",seed},
       {"harts",opts.at("harts")},{"workers",1},{"a_used",au},{"b_used",bu},{"a_offset",64+al},{"b_offset",64+2*al},
       {"a_bytes",ab},{"b_bytes",bb},{"y_bytes",192},{"y_offset",64},{"a_addr",0x10001040+al},{"b_addr",0x10003040+2*al},
       {"y_addr",0x10006040},{"errors",0},{"a_errors",0},{"b_errors",0},{"y_errors",0},{"dot8_abi",1},{"dot8_counter_abi",6},
       {"cpu_abi",4},{"dma_abi",1},{"dma_counter_abi",5},{"groups",(k/4)*out},{"clock_hz",31250000},
       {"l1",opts.at("l1")},{"sync_memory",opts.at("sync-memory")},{"memory_wait",opts.at("memory-wait")},
       {"line_words",opts.at("line-words")},{"line_count",opts.at("line-count")}};
    s={{"name",dot8_names[kind]},{"window","dispatch_load_pack_compute_store"},{"policy","prepared_reinitialize"},
       {"status","PASS"},{"method",method?"custom":"scalar"},{"order",job&1?"scalar_custom":"custom_scalar"},
       {"alignment",al?"unaligned":"aligned"}};
    for(unsigned h=0;h<2;++h)for(unsigned i=0;i<14;++i)n["h"+std::to_string(h)+"_"+dot8_cpu_events[i]]=counts[14*h+i];
    for(unsigned i=0;i<14;++i)n["dma_"+dot8_dma_events[i]]=counts[28+i];
    for(unsigned h=0;h<2;++h)for(unsigned i=0;i<4;++i)n["h"+std::to_string(h)+"_dot8_"+dot8_compute_events[i]]=counts[42+4*h+i];
    r.output=dot8_reference(kind,k,al,seed);
    const char* digits="0123456789abcdef";
    for(auto word:r.output)for(unsigned b=0;b<4;++b) {auto byte=std::uint8_t(word>>(8*b));s["output"]+=digits[byte>>4];s["output"]+=digits[byte&15];}
    r.words={job,method,pass,seed,kind,k,64+al,64+2*al,0x10001040+al,0x10003040+2*al,0x10006040,ab,bb,192,out,
        std::uint32_t(opts.at("harts")),std::uint32_t(4+opts.at("l1")+2*opts.at("sync-memory")),31250000,
        std::uint32_t(opts.at("line-words")),std::uint32_t(opts.at("line-count")),std::uint32_t(opts.at("memory-wait")),0,0,0,0,1,6,4,1,5,(k/4)*out,0};
    for(auto value:counts) {r.words.push_back(value);r.words.push_back(value>>32);}
    dot8_require(r.words.size()==132,"internal expected record layout");return r;
}
static void dot8_validate_line(const std::string& line,const Dot8Record& expected) {
    dot8_require(line.size()<=16384 && line.rfind("ASTERBENCH,",0)==0 && line.back()=='\n' &&
        line.find('\n')==line.size()-1 && line.find('\r')==std::string::npos,"invalid actual UART v6 framing");
    std::map<std::string,std::string> fields;
    for(size_t pos=11;pos<line.size()-1;) {
        auto end=line.find(',',pos);if(end==std::string::npos)end=line.size()-1;
        const auto item=line.substr(pos,end-pos);
        const auto eq=item.find('=');
        dot8_require(eq!=std::string::npos && eq && eq+1<item.size() && item.find('=',eq+1)==std::string::npos,"invalid actual key/value");
        dot8_require(fields.emplace(item.substr(0,eq),item.substr(eq+1)).second,"duplicate actual field");pos=end+1;
    }
    dot8_require(fields.size()==expected.numbers.size()+expected.strings.size(),"actual field set mismatch");
    for(const auto& [key,value]:expected.numbers)dot8_require(fields.count(key) && dot8_number(fields.at(key))==value,"actual metadata/counter differs from independent observation");
    for(const auto& [key,value]:expected.strings)dot8_require(fields.count(key) && fields.at(key)==value,"actual method/output/guard record differs from oracle");
}
