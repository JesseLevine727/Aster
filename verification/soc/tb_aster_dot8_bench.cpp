#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include "../common/dot8_record.h"
#include <fcntl.h>
#include <unistd.h>
#include <iostream>
#include <random>
#include <set>

static void write_exclusive(const std::string& path,const std::vector<std::uint8_t>& data) {
    int fd=open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0644);
    dot8_require(fd>=0,"cannot create exclusive RAM evidence; existing artifacts are retained");
    for(size_t offset=0;offset<data.size();) {
        auto n=write(fd,data.data()+offset,data.size()-offset);
        if(n<=0){close(fd);throw std::runtime_error("RAM evidence write failed");}offset+=n;
    }
    dot8_require(close(fd)==0,"RAM evidence close failed");
}
int main(int argc,char** argv) {
    try {
        Verilated::commandArgs(argc,argv);std::cout.setf(std::ios::unitbuf);
        const std::set<std::string> keys={"scalar-start","scalar-end","custom-start","custom-end","kind","k","alignment",
            "harts","jobs","seed","l1","sync-memory","memory-wait","line-words","line-count","boots","uart-seed"};
        std::map<std::string,std::uint64_t> opts;std::string prefix;
        for(int n=1;n<argc;++n) {
            std::string key=argv[n];if(key.rfind("+",0)==0)continue;
            dot8_require(key.rfind("--",0)==0 && n+1<argc,"invalid simulator option");key.erase(0,2);
            std::string value=argv[++n];
            if(key=="ram-prefix"){dot8_require(prefix.empty() && !value.empty(),"duplicate/empty RAM prefix");prefix=value;}
            else {dot8_require(keys.count(key) && !opts.count(key),"unknown/duplicate simulator option");opts[key]=dot8_number(value);}
        }
        dot8_require(opts.size()==keys.size() && !prefix.empty(),"missing simulator options");
        dot8_require(opts.at("kind")<3 && opts.at("k")<=(opts.at("kind")==0?4096u:64u) && opts.at("alignment")<2 &&
            opts.at("jobs")>=1 && opts.at("jobs")<=8 && opts.at("boots")>=1 && opts.at("boots")<=16 &&
            opts.at("seed")<=0xffffffff && opts.at("uart-seed")<=0xffffffff,"invalid study configuration");
        for(const auto& name:{"scalar","custom"})dot8_require(opts.at(std::string(name)+"-start")<opts.at(std::string(name)+"-end") &&
            opts.at(std::string(name)+"-end")<=65536,"actual kernel range outside ROM");
        unsigned kind=opts.at("kind"),k=opts.at("k"),alignment=opts.at("alignment");
        unsigned ab=dot8_allocation(dot8_a_used(kind,k)),bb=dot8_allocation(dot8_b_used(kind,k));
        Vaster_coherent_soc d;d.resetn=d.host_run=d.boot_we=d.boot_addr=d.boot_wdata=d.boot_wstrb=d.host_ram_addr=0;d.uart_tx_ready=1;
        for(unsigned n=0;n<8;++n){d.clk=0;d.eval();d.clk=1;d.eval();}
        d.resetn=1;d.clk=0;d.eval();d.clk=1;d.eval();dot8_require(d.stopped,"initial POR not STOPPED");
        std::array<std::uint32_t,16384> ram;ram.fill(0xa5a5a5a5);
        auto index=[&](std::uint32_t addr){dot8_require(addr>=0x10000000 && addr<0x10010000,"RAM address range");return (addr-0x10000000)/4;};
        auto byte=[&](std::uint32_t addr){return std::uint8_t(ram[index(addr)]>>(8*(addr%4)));};
        std::mt19937 rng(opts.at("uart-seed"));unsigned total_records=0;
        for(unsigned boot=1;boot<=opts.at("boots");++boot) {
            std::cout<<"ASTERBOOT "<<boot<<'\n';
            std::array<std::uint64_t,50> counts{};
            std::array<std::uint64_t,2> kernel{},first{},last{},lifetime{};
            std::array<std::set<std::uint32_t>,2> pcs;
            std::set<std::uint32_t> custom_pcs;
            std::array<std::uint32_t,48> output{};
            unsigned records=0,starts=0,freezes=0,seen=0,stores=0,entries=0;
            bool running=false;std::string line;
            auto buffers=[&](bool prepared) {
                std::uint32_t seed=std::uint32_t(opts.at("seed"))^(((starts-1)/2+1)*0x9e3779b9u);
                for(unsigned n=0;n<ab;++n)dot8_require(byte(0x10001000+n)==dot8_input(n,seed,0),"full A/guard preparation or preservation");
                for(unsigned n=0;n<bb;++n)dot8_require(byte(0x10003000+n)==dot8_input(n,seed,1),"full B/guard preparation or preservation");
                for(unsigned n=0;n<48;++n)dot8_require(ram[index(0x10006000)+n]==(prepared?dot8_guard(n,seed):output[n]),"full Y/guard oracle");
            };
            auto edge=[&](bool capture) {
                d.clk=0;d.uart_tx_ready=opts.at("uart-seed")==0 || rng()%4!=0;d.eval();
                dot8_require(!d.hart_trap && !d.fault_valid && !(d.hart_run&2),"actual firmware trap or secondary released");
                dot8_require(!d.perf_resume && d.stop_commit!=2,"unexpected resume/selective lifecycle");
                dot8_require(!d.dma_busy && !d.dma_request_pending && !d.dma_store_commit && !d.dma_status,"hidden DMA in compute-only study");
                for(unsigned n=0;n<14;++n)dot8_require(!d.dma_events[n],"nonzero DMA event in compute study");
                bool active=running && !d.perf_start && !d.perf_freeze && !d.stopped;
                unsigned method=starts?(((starts-1)/2)&1)^((starts-1)%2):0;
                if(d.stopped){running=false;counts={};}
                else if(d.perf_start) {
                    dot8_require(!running && starts==freezes && records==freezes && !d.dot8_busy,"overlapping/unsettled windows");
                    running=true;++starts;counts={};kernel={};first={};last={};pcs={};custom_pcs.clear();seen=0;
                    auto seed=std::uint32_t(opts.at("seed"))^(((starts-1)/2+1)*0x9e3779b9u);
                    output=dot8_reference(kind,k,alignment,seed);buffers(true);
                } else if(d.perf_freeze) {
                    dot8_require(running && !d.dot8_busy && seen==(1u<<dot8_outputs(kind))-1,"freeze before all compute/output stores");
                    dot8_require(kernel[method]>0 && kernel[1-method]==0,"wrong/missing actual scalar/custom kernel");
                    const unsigned expected=method?(k/4)*dot8_outputs(kind):0;
                    for(unsigned n=0;n<8;++n)dot8_require(counts[42+n]==(n<4?expected*(n==1?2:1):0),"custom events differ from complete mathematical groups");
                    buffers(false);running=false;++freezes;
                } else if(active) {
                    for(unsigned h=0;h<2;++h) {
                        for(unsigned n=0;n<14;++n)counts[14*h+n]+=(d.perf_events[h]>>n)&1;
                        for(unsigned n=0;n<4;++n)counts[42+4*h+n]+=(d.dot8_events[h]>>n)&1;
                        dot8_require(bool((d.perf_events[h]>>1)&1)==bool((d.retired>>h)&1),"CPU retirement event mux");
                        bool custom=((d.retired>>h)&1) && (d.retired_insn[h]&0xfe00707f)==0x0b;
                        dot8_require(bool(d.dot8_events[h]&8)==custom,"custom retirement event mux");
                        if(custom) {
                            dot8_require(h==0 && method==1 && d.retired_pc[h]>=opts.at("custom-start") && d.retired_pc[h]<opts.at("custom-end"),"custom retired outside audited kernel");
                            custom_pcs.insert(d.retired_pc[h]);
                        }
                    }
                    if(d.retired&1)for(unsigned m=0;m<2;++m) {
                        std::string name=m?"custom":"scalar";
                        if(d.retired_pc[0]>=opts.at(name+"-start") && d.retired_pc[0]<opts.at(name+"-end")) {
                            if(!kernel[m]++)first[m]=counts[0];last[m]=counts[0];pcs[m].insert(d.retired_pc[0]);
                        }
                    }
                }
                if(d.store_commit) {
                    auto addr=d.store_addr;
                    if(active) {
                        dot8_require(!(addr>=0x10001000 && addr<0x10001000+ab) && !(addr>=0x10003000 && addr<0x10003000+bb),"kernel wrote input/guard");
                        if(addr>=0x10006000 && addr<0x100060c0) {
                            dot8_require(addr>=0x10006040 && addr<0x10006040+4*dot8_outputs(kind) && d.store_mask==15,"kernel wrote output guard/partial word");
                            unsigned out=(addr-0x10006040)/4;
                            dot8_require(!(seen&(1u<<out)) && d.store_data==output[16+out],"output store repeated or incorrect signed arithmetic");seen|=1u<<out;
                        }
                    }
                    auto& word=ram[index(addr)];
                    for(unsigned n=0;n<4;++n)if(d.store_mask&(1u<<n))word=(word&~(255u<<(8*n)))|(d.store_data&(255u<<(8*n)));
                    ++stores;
                }
                for(unsigned h=0;h<2;++h)if(d.retired&(1u<<h))++lifetime[h];
                if((d.retired&1) && d.retired_pc[0]==0)++entries;
                if(capture && d.uart_tx_valid && d.uart_tx_ready) {
                    dot8_require(records<2*opts.at("jobs"),"trailing actual UART");line+=char(d.uart_tx_data);dot8_require(line.size()<=16384,"oversized UART record");
                    if(line.back()=='\n') {
                        dot8_require(!running && starts==freezes && freezes==records+1,"UART outside frozen window");
                        auto expected=dot8_record(opts,records,counts);dot8_validate_line(line,expected);buffers(false);
                        for(unsigned n=0;n<132;++n)dot8_require(ram[0x8000/4+132*records+n]==expected.words[n],"RAM record versus actual events/metadata");
                        std::cout<<line<<"DOT8_OBS {\"boot\":"<<boot<<",\"job\":"<<records/2+1<<",\"pass\":"<<records%2+1
                            <<",\"method\":"<<method<<",\"counts\":[";
                        for(unsigned n=0;n<50;++n)std::cout<<(n?",":"")<<counts[n];
                        std::cout<<"],\"output_stores\":"<<dot8_outputs(kind);
                        for(const auto& entry:std::map<std::string,std::array<std::uint64_t,2>>{{"kernel_retired",kernel},{"kernel_first",first},{"kernel_last",last}})
                            std::cout<<",\""<<entry.first<<"\":["<<entry.second[0]<<','<<entry.second[1]<<']';
                        std::cout<<",\"kernel_pcs\":[";
                        for(unsigned m=0;m<2;++m){std::cout<<(m?",[":"[");bool comma=false;for(auto pc:pcs[m]){std::cout<<(comma?",":"")<<pc;comma=true;}std::cout<<']';}
                        std::cout<<"],\"custom_pcs\":[";bool comma=false;for(auto pc:custom_pcs){std::cout<<(comma?",":"")<<pc;comma=true;}
                        std::cout<<"]}\n";++records;++total_records;line.clear();
                    }
                }
                d.clk=1;d.eval();
                dot8_require(d.dot8_counting==running && d.dma_counting==running,"common counter window state");
                for(unsigned n=0;n<8;++n)dot8_require(d.dot8_counters[n]==counts[42+n],"actual counter update versus event edge");
            };
            d.host_run=1;unsigned quiet=0;
            for(std::uint64_t cycle=0;cycle<1000000000ull && quiet<512;++cycle){edge(true);if(records==2*opts.at("jobs"))++quiet;}
            dot8_require(quiet==512 && line.empty() && !running && entries==1 && !lifetime[1],"incomplete actual benchmark boot");
            d.host_run=0;for(unsigned n=0;n<2000000 && !d.stopped;++n)edge(false);
            dot8_require(d.stopped && !d.hart_run && !d.dot8_busy && !d.dma_busy && !d.fabric_busy && !d.flush_active && !d.reservations,"unsafe STOPPED");
            std::vector<std::uint8_t> saved;saved.reserve(65536);
            for(unsigned n=0;n<ram.size();++n) {
                d.host_ram_addr=4*n;edge(false);edge(false);dot8_require(d.host_ram_rdata==ram[n] && !d.backing_valid,"whole RAM store oracle differs after stop");
                for(unsigned byte=0;byte<4;++byte)saved.push_back(d.host_ram_rdata>>(8*byte));
            }
            write_exclusive(prefix+".boot"+std::to_string(boot)+".ram",saved);
            std::cout<<"ASTERSTOP {\"boot\":"<<boot<<",\"records\":"<<records<<",\"ram_bytes\":65536,\"cpu_stores\":"<<stores
                <<",\"dma_stores\":0,\"lifetime_retired\":["<<lifetime[0]<<','<<lifetime[1]<<"]}\n";
        }
        std::cout<<"PASS: dot8 benchmark boots="<<opts.at("boots")<<" records="<<total_records
            <<"; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows\n";return 0;
    } catch(const std::exception& e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
