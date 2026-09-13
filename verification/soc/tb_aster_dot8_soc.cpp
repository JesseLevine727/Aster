#include "Vaster_coherent_soc.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_L1 1
#define ASTER_MEMORY_WAIT 0
#endif
static void require(bool ok,const char* why) { if (!ok) throw std::runtime_error(why); }
static std::uint32_t merge(std::uint32_t old,std::uint32_t data,unsigned mask) {
    for (unsigned b=0;b<4;++b) if (mask&(1u<<b)) old=(old&~(255u<<(8*b)))|(data&(255u<<(8*b)));
    return old;
}
static std::uint8_t pattern(unsigned i,std::uint32_t seed,unsigned bank) {
    switch (seed&7) {
        case 0:return 0;case 1:return 128;case 2:return bank?127:128;case 3:return (i&1)?127:128;
        default:return (seed>>((i%4)*8))^(i*73u)^(i/8)^(bank*0x5bu);
    }
}
static std::uint32_t guard(unsigned i,std::uint32_t seed) { return 0x6d5a0000u^(i*0x01010101u)^seed; }
static const std::array<unsigned,18> sizes={0,1,2,3,4,5,7,8,15,16,31,32,63,64,127,128,255,256};
static unsigned outputs(unsigned kind) {return kind==0?1:kind==1?8:15;}
class Bench {
public:
    Vaster_coherent_soc d;
    std::array<std::uint32_t,16384> ram{};
    std::array<std::uint64_t,50> counts{};
    std::array<std::uint64_t,2> dot_total{};
    std::array<std::uint64_t,2> accepted_total{},completed_total{};
    std::array<unsigned,2> phase{},finished{},methods{},seen{},phase_dots{},scalar_job{},publication{};
    std::array<std::array<std::uint32_t,12>,2> meta{};
    std::array<std::array<std::uint32_t,3>,2> bases{};
    std::array<std::uint32_t,3> descriptor{};
    std::vector<std::uint8_t> source;
    std::mt19937 rng{0xa57e8};
    unsigned copied=0,destination=0,starts=0,stops=0,cpu_stores=0,dma_stores=0,overlap=0,selective=0;
    unsigned previous_accept=0;
    bool counting=false,received=false,checking=true;
    std::string uart;
    static unsigned index(std::uint32_t addr) {
        require(addr>=0x10000000 && addr<0x10010000,"store/read outside RAM");return (addr-0x10000000)/4;
    }
    std::uint8_t byte(std::uint32_t addr) const {return ram[index(addr)]>>(8*(addr%4));}
    unsigned base(unsigned h) const {return (h?0xc000:0x8000)/4;}
    Bench() {
        ram.fill(0xa5a5a5a5);d.resetn=d.host_run=d.boot_we=d.boot_addr=d.boot_wdata=d.boot_wstrb=d.host_ram_addr=0;
        d.uart_tx_ready=1;
        for (unsigned i=0;i<8;++i) {d.clk=0;d.eval();d.clk=1;d.eval();}
        d.resetn=1;step();require(d.stopped,"initial STOPPED");snapshot();
    }
    std::uint32_t expected_output(unsigned h,unsigned out) const {
        const auto& m=meta[h];std::int64_t sum=0;
        for (unsigned k=0;k<m[3];++k) {
            unsigned ai=m[2]==2?(out/5)*m[3]+k:m[2]==1?out+k:k;
            unsigned bi=m[2]==2?k*5+out%5:k;
            int a=byte(m[7]+64+m[4]+ai),b=byte(m[8]+64+m[5]+bi);
            if (a>127) a-=256;if (b>127) b-=256;sum+=std::int64_t(a)*b;
        }
        return std::uint32_t(std::uint64_t(sum));
    }
    void buffers(unsigned h,bool prepared) {
        const auto& m=meta[h];
        for (unsigned i=0;i<512;++i) require(byte(m[7]+i)==pattern(i,m[6],0) &&
            byte(m[8]+i)==pattern(i,m[6],1),"complete input/guard bytes mutated or not prepared");
        for (unsigned i=0;i<47;++i) {
            auto expected=!prepared && i>=16 && i<16+outputs(m[2]) ? expected_output(h,i-16):guard(i,m[6]);
            require(ram[index(m[9]+4*i)]==expected,"complete output/guard word oracle");
        }
    }
    void end_phase(unsigned h) {
        if (phase[h]!=1 && phase[h]!=2) return;
        const auto& m=meta[h];
        require(seen[h]==(1u<<outputs(m[2]))-1,"missing/duplicate output write");
        require(phase_dots[h]==(phase[h]==2?(m[3]/4)*outputs(m[2]):0),"custom retirement count differs from mathematical groups");
        buffers(h,false);++methods[h];
        if (phase[h]==1) scalar_job[h]=m[1];
        else if (m[1]<=736) {
            require(scalar_job[h]==m[1] && m[1]==finished[h]+1,"scalar/custom pairing order");
            ++finished[h];
            if (finished[h]%128==0) std::cout<<"OBS: hart="<<h<<" directed_pairs="<<finished[h]<<'\n';
        } else ++publication[h];
    }
    void begin_phase(unsigned h,unsigned next) {
        end_phase(h);phase[h]=next;seen[h]=phase_dots[h]=0;
        if (next!=1 && next!=2) return;
        for (unsigned i=0;i<12;++i) meta[h][i]=ram[base(h)+i];
        const auto& m=meta[h];
        require(m[10]==512 && m[11]==47 && m[2]<=2 && m[4]<4 && m[5]<4,"runtime metadata bounds");
        if (m[1]<=736) {
            require(m[1]==finished[h]+1,"missing/reordered directed shape");
            unsigned j=m[1]-1,kind=0;
            while (j>=16*(kind==0?18u:14u)) {j-=16*(kind==0?18u:14u);++kind;}
            unsigned n=kind==0?18:14;
            require(m[2]==kind && m[3]==sizes[j%n] && m[4]==(j/n)/4 && m[5]==(j/n)%4 &&
                m[6]==(0xa57e8000u^(h<<24)^(m[1]*0x9e3779b9u)),"shape/alignment/seed not the fixed functional plan");
            if (m[1]==1 && next==1) bases[h]={m[7],m[8],m[9]};
            require(m[7]==bases[h][0] && m[8]==bases[h][1] && m[9]==bases[h][2],"directed allocation changed");
        } else {
            require(next==2 && finished[h]==736 && m[1]==737+publication[h] && publication[h]<3 &&
                h==(ASTER_HART_COUNT==2?1u:0u) && m[2]==2 && m[3]==32 && m[4]==1 && m[5]==2 &&
                m[6]==0x81d7e005u+(m[1]-736)*8,"DMA publication plan mismatch");
            require(m[7]==bases[0][0] && m[8]==bases[0][1] && m[9]==bases[0][2],"peer did not consume primary buffers");
        }
        require(m[7]>=0x10000000 && m[7]+512<=0x10008000 && m[8]>=0x10000000 && m[8]+512<=0x10008000 &&
            m[9]>=0x10000000 && m[9]+188<=0x10008000 && !(m[7]%64) && !(m[8]%64) && !(m[9]%4),"buffer allocation range");
        buffers(h,true);
    }
    void low() {
        d.clk=0;d.uart_tx_ready=rng()%4!=0;d.eval();
        if (d.hart_trap || d.fault_valid) {
            std::cerr<<"trap pcs="<<std::hex<<d.retired_pc[0]<<','<<d.retired_pc[1]<<std::dec
                <<" jobs="<<ram[base(0)+1]<<','<<ram[base(1)+1]<<'\n';
            throw std::runtime_error("actual dot8 C runtime trapped/faulted");
        }
    }
    void rise() {
        require(!(d.store_commit && d.dma_store_commit),"CPU and DMA commit collided");
        if (d.store_commit) {
            unsigned owner=d.store_owner,addr=d.store_addr;
            if (checking && (phase[owner]==1 || phase[owner]==2)) {
                const auto& m=meta[owner];
                require(!(addr>=m[7] && addr<m[7]+512) && !(addr>=m[8] && addr<m[8]+512),"kernel modified input");
                if (addr>=m[9] && addr<m[9]+188) {
                    require(addr>=m[9]+64 && addr<m[9]+64+4*outputs(m[2]) && d.store_mask==15,"kernel guard/partial store");
                    unsigned out=(addr-m[9]-64)/4;
                    require(!(seen[owner]&(1u<<out)) && d.store_data==expected_output(owner,out),"actual output store arithmetic/duplicate");
                    seen[owner]|=1u<<out;
                }
            }
            if (checking && addr==0x10000000+4*base(owner)) {
                require(d.store_mask==15,"partial progress word");begin_phase(owner,d.store_data);
            }
            auto n=index(addr);ram[n]=merge(ram[n],d.store_data,d.store_mask);++cpu_stores;
        }
        if (d.backing_valid && d.backing_ready && !d.backing_device && !d.backing_owner && (d.backing_addr>>12)==0x30000) {
            unsigned off=d.backing_addr&4095;
            if (off<=8 && !(off%4) && d.backing_mask) descriptor[off/4]=merge(descriptor[off/4],d.backing_data,d.backing_mask);
            if (off==12 && (d.backing_mask&1) && (d.backing_data&255)==1 && !d.dma_busy) {
                source.clear();copied=0;destination=descriptor[1];++starts;
                require(descriptor[2]==512,"functional DMA descriptor size");
                for (unsigned i=0;i<descriptor[2];++i) source.push_back(byte(descriptor[0]+i));
            }
        }
        if (d.dma_store_commit) {
            require(d.backing_valid && d.backing_ready && d.backing_device && d.backing_mask,"DMA commit not a payload store");
            for (unsigned b=0;b<4;++b) if (d.backing_mask&(1u<<b)) {
                require(copied<source.size() && d.backing_addr+b==destination+copied &&
                    std::uint8_t(d.backing_data>>(8*b))==source[copied],"DMA prefix/address/value oracle");++copied;
            }
            auto n=index(d.backing_addr);ram[n]=merge(ram[n],d.backing_data,d.backing_mask);++dma_stores;
        }
        if (d.dma_request_pending && d.dma_request_ready && !d.dma_request_mask)
            require(d.dma_request_rdata==ram[index(d.dma_request_addr)],"DMA saw stale dirty input");
        unsigned accept=0;
        for (unsigned h=0;h<2;++h) {
            bool retired=(d.retired>>h)&1;
            bool custom=retired && (d.retired_insn[h]&0xfe00707f)==0x0b;
            require(bool(d.dot8_events[h]&8)==custom,"retired custom event did not match actual RVFI");
            require(bool(d.dot8_events[h]&4)==bool(previous_accept&(1u<<h)),"sum completion was not one edge after actual admission");
            if (d.dot8_events[h]&1) { require(!(d.dot8_busy&(1u<<h)),"recaptured a busy instruction");accept|=1u<<h;if(d.dma_busy)++overlap;++accepted_total[h]; }
            if (d.dot8_events[h]&4) ++completed_total[h];
            if (custom) {
                ++dot_total[h];
                if (checking) {require(phase[h]==2 || phase[h]==4,"custom instruction outside declared custom/helper phase");if(phase[h]==2)++phase_dots[h];}
            }
            if (h>=ASTER_HART_COUNT) require(!d.dot8_events[h],"absent hart event");
        }
        previous_accept=accept;
        if (d.stopped || d.perf_start) {counts.fill(0);counting=!d.stopped && d.perf_start;}
        else if (d.perf_freeze) counting=false;
        else if (d.perf_resume) counting=true;
        else if (counting) {
            for (unsigned h=0;h<2;++h) {
                for (unsigned n=0;n<14;++n) counts[h*14+n]+=(d.perf_events[h]>>n)&1;
                for (unsigned n=0;n<4;++n) counts[42+h*4+n]+=(d.dot8_events[h]>>n)&1;
            }
            for (unsigned n=0;n<14;++n) counts[28+n]+=d.dma_events[n];
        }
        if (d.stop_commit) {
            require(!d.dot8_busy && (!d.fabric_busy || (d.stop_commit==2 && d.dma_busy && !d.host_run &&
                !d.backing_valid && !d.atomic_active && !d.dma_request_pending)),"warm reset raced admitted compute/memory");
            if(d.stop_commit==2) {require(d.hart_run==3,"selective reset lost the primary");++selective;}
        }
        if (d.uart_tx_valid && d.uart_tx_ready) {
            uart+=char(d.uart_tx_data);
            if (d.uart_tx_data=='\n') {require(uart=="DOT8 RUNTIME PASS\n","unexpected actual UART");received=true;}
        }
        d.clk=1;d.eval();
        require(d.dot8_counting==counting && d.dma_counting==counting,"common bank window state");
        for (unsigned n=0;n<8;++n) require(d.dot8_counters[n]==counts[42+n],"integrated dot8 counter mismatch");
        for (unsigned n=0;n<14;++n) require(d.dma_counters[n]==counts[28+n],"integrated DMA counter mismatch");
        require((d.dot8_busy&accept)==accept,"admitted dot8 failed to become busy");
    }
    void step() {low();rise();}
    void snapshot() {
        require(d.stopped && !d.hart_run && !d.fabric_busy && !d.dot8_busy && !d.dma_busy && !d.reservations,"STOPPED without complete drain");
        d.boot_we=0;
        for (unsigned i=0;i<ram.size();++i) {
            d.host_ram_addr=4*i;step();step();
            require(!d.backing_valid && d.host_ram_rdata==ram[i],"complete 64 KiB stopped RAM differs from architectural oracle");
        }
    }
    void full() {
        require(d.stopped,"start without stopped");
        checking=true;phase={};finished={};methods={};seen={};phase_dots={};scalar_job={};publication={};bases={};
        dot_total={};previous_accept=0;starts=overlap=0;received=false;uart.clear();descriptor={};
        d.host_run=1;d.boot_we=1;d.boot_addr=0;d.boot_wdata=0xffffffff;d.boot_wstrb=15;
        std::uint64_t cycles=0;
        for (;cycles<2000000000ull && !received;++cycles) step();
        require(received && !counting,"actual functional firmware timed out / did not freeze");
        const auto frozen=counts;
        for (unsigned h=0;h<ASTER_HART_COUNT;++h) {
            require(finished[h]==736 && methods[h]==1472+publication[h],"functional matrix incomplete");
            require(ram[base(h)+16]==736 && ram[base(h)+19]==16 && ram[base(h)+18]==dot_total[h],"firmware job/LRSC/custom totals");
            require(frozen[42+4*h]==dot_total[h] && frozen[43+4*h]==2*dot_total[h] &&
                frozen[44+4*h]==dot_total[h] && frozen[45+4*h]==dot_total[h],"whole-program custom event/retirement totals");
        }
        require(publication[ASTER_HART_COUNT==2?1:0]==3 && starts==4 && overlap>0,"missing DMA publication / active compute overlap");
        require(frozen[32]==2048 && frozen[38]==4 && !frozen[39] && !frozen[40] && !frozen[41],"DMA payload/success/error counts");
        require(ram[base(0)+21]==ASTER_HART_COUNT && ram[base(0)+22]==0x80001,"runtime image identity");
        for (unsigned n=0;n<50;++n) {
            auto value=std::uint64_t(ram[base(0)+32+2*n])|(std::uint64_t(ram[base(0)+33+2*n])<<32);
            require(value==frozen[n],"firmware common-window snapshot differs from independently counted events");
        }
        d.host_run=0;
        for (unsigned cycle=0;cycle<2000000 && !d.stopped;++cycle) step();
        require(d.stopped,"warm global stop deadlocked");++stops;snapshot();
        std::cout<<"PASS: dot8 C runtime harts="<<ASTER_HART_COUNT<<" cache="<<ASTER_L1<<" wait="<<ASTER_MEMORY_WAIT
            <<" cycles="<<cycles<<" pairs="<<finished[0]+finished[1]<<" output_methods="<<methods[0]+methods[1]
            <<" dots="<<dot_total[0]<<','<<dot_total[1]<<" DMA-overlap="<<overlap
            <<"; all 16 byte alignments, tails, dot/FIR/GEMM, actual output stores, full guards/RAM, LRSC, dirty DMA publication, ABI 6\n";
    }
    void load(const std::string& path) {
        require(d.stopped && !d.host_run,"ROM reload requires safe STOPPED");
        std::ifstream file(path);require(bool(file),"cannot read actual stop fixture");
        std::vector<std::uint32_t> words;
        for(std::string word;file>>word;) {
            require(word.size()==8 && word.find_first_not_of("0123456789abcdefABCDEF")==std::string::npos,"invalid stop fixture ROM");
            words.push_back(std::stoul(word,nullptr,16));
        }
        require(words.size()==16384,"stop fixture must fill ROM");
        for(unsigned i=0;i<words.size();++i) {
            d.boot_we=1;d.boot_addr=4*i;d.boot_wdata=words[i];d.boot_wstrb=15;step();
        }
        d.boot_we=0;
    }
    void adversarial(unsigned hart,unsigned point,bool escalation) {
        require(d.stopped,"adversarial restart before STOPPED");
        checking=false;received=false;uart.clear();descriptor={};source.clear();previous_accept=0;
        const auto accepted_before=accepted_total,completed_before=completed_total;
        const auto selective_before=selective;
        d.host_run=1;bool reached=false;unsigned flush_edges=0;
        for(unsigned cycle=0;cycle<10000000;++cycle) {
            low();if(d.flush_active)++flush_edges;
            unsigned events=d.dot8_events[hart];bool busy=(d.dot8_busy>>hart)&1;
            bool trigger=escalation ? d.stop_busy && d.hart_run==3 && d.dma_busy &&
                (point==0?!d.flush_active:point==1?d.flush_active:point==2?flush_edges>=8:d.stop_commit==2) :
                point==0?(events&1):point==1?(events&4):point==2?(busy && !(events&2)):
                point==3?(events&8):(selective-selective_before>=8 && (events&1));
            if(trigger) {d.host_run=0;d.eval();reached=true;}
            rise();if(reached)break;
        }
        require(reached,"adversarial compute/selective stop edge not reached");
        bool primary_reset=false;
        if(escalation)d.host_run=1; // transient STOP must remain latched
        for(unsigned cycle=0;cycle<2000000 && !d.stopped;++cycle) {
            low();
            if(d.stop_commit&1) {primary_reset=true;d.host_run=0;d.eval();}
            rise();
        }
        require(primary_reset && d.stopped,"custom-aware STOP/escalation deadlocked or RUN canceled it");
        ++stops;snapshot();
        for(unsigned h=0;h<2;++h) require(accepted_total[h]-accepted_before[h]==completed_total[h]-completed_before[h],
            "admitted compute discarded or duplicated by warm STOP");
        std::cout<<"PASS: dot8 warm stop hart="<<hart<<" point="<<point<<" escalation="<<escalation
            <<" selective="<<selective-selective_before<<"; all admitted sums completed, no reset race, full retained RAM\n";
    }
};
int main(int argc,char** argv) {
    try {
        Verilated::commandArgs(argc,argv);std::cout.setf(std::ios::unitbuf);
        std::string stop_rom;
        bool stops_only=false;
        for(int n=1;n<argc;++n) {
            std::string arg=argv[n];
            if(arg.rfind("--stop-rom=",0)==0)stop_rom=arg.substr(11);
            if(arg=="--stops-only")stops_only=true;
        }
        require(!stop_rom.empty(),"missing real compute stop fixture ROM");Bench b;
        if(!stops_only) {
        b.full();b.full();
        }
        b.load(stop_rom);
        for(unsigned h=0;h<ASTER_HART_COUNT;++h)for(unsigned point=0;point<4;++point)b.adversarial(h,point,false);
        if(ASTER_HART_COUNT==2) {
            b.adversarial(0,4,false);
            for(unsigned point=0;point<4;++point)b.adversarial(0,point,true);
        }
        std::cout<<"PASS: dot8 runtime closeout warm_boots="<<(stops_only?0:2)<<" stopped="<<b.stops
            <<" CPU-stores="<<b.cpu_stores<<" DMA-stores="<<b.dma_stores<<"; no reset after initial POR\n";
        return 0;
    } catch (const std::exception& e) {std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
