#include "../common/linux_bus.h"
#include "Vaster_pynq_linux___024root.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#ifndef ASTER_HART_COUNT
#define ASTER_HART_COUNT 2
#define ASTER_L1 1
#define ASTER_DOT8 1
#endif
static std::uint32_t merge(std::uint32_t old,std::uint32_t data,unsigned mask) {
    for (unsigned b=0;b<4;++b) if (mask&(1u<<b)) old=(old&~(255u<<(8*b)))|(data&(255u<<(8*b)));
    return old;
}
int main(int argc,char** argv) {
    try {
        Verilated::commandArgs(argc,argv);std::cout.setf(std::ios::unitbuf);
        require(argc==2,"usage: linux_dot8 dot8_runtime.hex");
        std::ifstream file(argv[1]);require(bool(file),"cannot read actual firmware");
        std::vector<std::uint32_t> image;
        for (std::string word;file>>word;) {
            require(word.size()==8 && word.find_first_not_of("0123456789abcdefABCDEF")==std::string::npos,"invalid firmware hex word");
            image.push_back(std::stoul(word,nullptr,16));
        }
        require(image.size()==16384,"full 64 KiB firmware required");
        Bus b;std::array<std::uint32_t,16384> ram{};
        std::array<std::uint64_t,50> counts{},frozen{};
        unsigned stores=0,dma_bytes=0,starts=0,freezes=0;bool counting=false;
        b.before_edge=[&]() {
            const auto* r=b.dut.rootp;
            if (!b.dut.aresetn || r->aster_pynq_linux__DOT__coherent_stopped) {counts={};counting=false;}
            else if (r->aster_pynq_linux__DOT__coherent_perf_start) {
                require(!counting,"overlapping functional windows");counts={};counting=true;++starts;
            } else if (r->aster_pynq_linux__DOT__coherent_perf_freeze) {
                require(counting,"freeze without window");frozen=counts;counting=false;++freezes;
            } else if (r->aster_pynq_linux__DOT__coherent_perf_resume) counting=true;
            else if (counting) {
                for (unsigned h=0;h<2;++h) {
                    for (unsigned n=0;n<14;++n) counts[14*h+n]+=(r->aster_pynq_linux__DOT__coherent_perf_events[h]>>n)&1;
                    for (unsigned n=0;n<4;++n) counts[42+4*h+n]+=(r->aster_pynq_linux__DOT__dot8_events[h]>>n)&1;
                }
                for (unsigned n=0;n<14;++n) counts[28+n]+=r->aster_pynq_linux__DOT__dma_events[n];
            }
            auto store=[&](std::uint32_t addr,std::uint32_t data,unsigned mask) {
                require(addr>=0x10000000 && addr<0x10010000 && !(addr&3) && mask,"accepted store escaped RAM");
                auto& word=ram[(addr-0x10000000)/4];word=merge(word,data,mask);++stores;
            };
            if (r->aster_pynq_linux__DOT__coherent_store_commit)
                store(r->aster_pynq_linux__DOT__coherent_store_addr,r->aster_pynq_linux__DOT__coherent_store_data,
                    r->aster_pynq_linux__DOT__coherent_store_mask);
            if (r->aster_pynq_linux__DOT__dma_store_commit) {
                require(!r->aster_pynq_linux__DOT__coherent_store_commit && r->aster_pynq_linux__DOT__dma_backing_valid &&
                    r->aster_pynq_linux__DOT__dma_backing_ready && r->aster_pynq_linux__DOT__dma_backing_device,"device store not accepted");
                store(r->aster_pynq_linux__DOT__dma_backing_addr,r->aster_pynq_linux__DOT__dma_backing_data,r->aster_pynq_linux__DOT__dma_backing_mask);
                for (unsigned n=0;n<4;++n) dma_bytes+=(r->aster_pynq_linux__DOT__dma_backing_mask>>n)&1;
            }
            if (r->aster_pynq_linux__DOT__dma_request_pending && r->aster_pynq_linux__DOT__dma_request_ready && !r->aster_pynq_linux__DOT__dma_request_mask) {
                auto addr=r->aster_pynq_linux__DOT__dma_request_addr;
                require(addr>=0x10000000 && addr<0x10008000 && !(addr&3) &&
                    r->aster_pynq_linux__DOT__dma_request_rdata==ram[(addr-0x10000000)/4],"AXI-shell DMA read stale/nonshared");
            }
        };
        b.reset();
        require(b.read(0x18)==0x41535452 && b.read(0x1c)==(ASTER_DOT8?0x80001u:0x70001u) &&
            b.read(0x20)==31250000 && b.read(0x24)==ASTER_HART_COUNT &&
            b.read(0x44)==(5u|(ASTER_L1<<1)|(ASTER_DOT8<<3)),"Linux explicit identity/feature/clock");
        auto map=[&](bool live,bool writes) {
            for (unsigned addr=0x110;addr<0x180;++addr) {
                bool allowed=ASTER_DOT8 && !(addr&3) && (addr<0x160 || addr==0x160 || addr==0x164 || addr==0x168);
                std::uint32_t value=0;
                if (allowed) {
                    if (addr==0x110)value=1;else if(addr==0x114)value=6;
                    else if(addr==0x160)value=0x0b;else if(addr==0x164)value=0xfe00707f;else if(addr==0x168)value=4;
                    else if(addr>=0x120 && addr<0x160 && live) {
                        auto counter=frozen[42+(addr-0x120)/8];value=std::uint32_t(counter>>((addr&4)?32:0));
                    }
                }
                require(b.read(addr,allowed?0:2)==value,"dot8 host register value/order/denial");
                if(writes) for(unsigned strobe=0;strobe<16;++strobe) b.write(addr,0xffffffff,strobe%3,strobe,2);
                require(b.read(addr,allowed?0:2)==value,"denied write mutated dot8 state");
            }
        };
        auto stopped=[&]() {
            b.write(0,0);
            for(unsigned n=0;n<200000 && !(b.read(0x40)&1);++n) {}
            require(b.read(0x40)==1 && b.read(0x28)==0 && b.read(0)==0 && b.read(4)==0 &&
                b.read(0x84)==0 && b.read(0x98)==0,"safe Linux STOPPED/dma/fifo");
            map(false,false);
            for(unsigned n=0;n<ram.size();++n) require(b.read(0x20000+4*n)==ram[n],"all 64 KiB stopped RAM differs from accepted stores");
        };
        auto load=[&](const std::vector<std::uint32_t>& words) {
            require(b.read(0x40)==1 && !b.read(0),"ROM programming before STOPPED");
            for(unsigned n=0;n<words.size();++n) b.write(0x10000+4*n,words[n],n%3);
        };
        map(false,true);stopped();load(image);
        if(ASTER_DOT8) for(unsigned boot=0;boot<2;++boot) {
            b.write(0,1);b.write(0x10000,0xffffffff,2,15,2);
            require(b.read(0x20000,2)==0,"running RAM snapshot allowed");
            std::string uart;const std::string expected="DOT8 RUNTIME PASS\n";
            for(unsigned n=0;n<10000000 && uart!=expected;++n) {
                b.idle(512);auto value=b.read(8);
                if(value&0x80000000u) {uart+=char(value);require(uart.size()<=expected.size() && expected.compare(0,uart.size(),uart)==0,"unexpected actual serial");}
                require(!(b.read(4)&0x1a),"actual core trap/serial error");
                if(n && n%100000==0) std::cout<<"OBS: Linux dot8 boot="<<boot+1<<" polls="<<n<<'\n';
            }
            require(uart==expected && starts==boot+1 && freezes==boot+1 && !counting,"real runtime/serial/window incomplete");
            b.idle(2048);require(!b.read(0x0c) && b.read(0x10)==expected.size() && b.read(0x14)==expected.size(),"serial counts/trailing output");
            map(true,true);
            for(unsigned n=0;n<14;++n) {
                auto lo=b.read(0xa0+8*n),hi=b.read(0xa4+8*n);
                require((std::uint64_t(hi)<<32|lo)==frozen[28+n],"host DMA counter mismatch");
            }
            for(unsigned n=0;n<50;++n) {
                auto value=std::uint64_t(ram[0x8000/4+32+2*n])|(std::uint64_t(ram[0x8000/4+33+2*n])<<32);
                require(value==frozen[n],"actual C RAM counter snapshot versus independent AXI-shell events");
            }
            unsigned groups=0;for(auto n:{0u,1u,2u,3u,4u,5u,7u,8u,15u,16u,31u,32u,63u,64u,127u,128u,255u,256u})
                groups+=16*(n/4)*(n<=64?24:1);
            for(unsigned h=0;h<2;++h) {
                auto dots=h>=ASTER_HART_COUNT?0u:groups+17+(h?360:64)+(ASTER_HART_COUNT==1?360:0);
                require(frozen[42+4*h]==dots && frozen[43+4*h]==2*dots && frozen[44+4*h]==dots && frozen[45+4*h]==dots,"exact functional dot8 totals");
                if(h<ASTER_HART_COUNT)require(ram[(h?0xc000:0x8000)/4+16]==736,"directed cases missing");
            }
            require(frozen[32]==2048 && frozen[38]==4 && !frozen[39] && !frozen[40] && !frozen[41] &&
                dma_bytes==2048*(boot+1),"DMA functional activity mismatch");
            stopped();
            std::cout<<"PASS: dot8 AXI/serial runtime boot="<<boot+1<<" harts="<<ASTER_HART_COUNT<<" cache="<<ASTER_L1
                <<" counters=50 retained_RAM=65536; real C, ROM-write/running-RAM denial, all read-only strobes/offsets, stable replies\n";
        }
        // Explicitly unsupported instruction in either configuration cannot
        // claim/retire or touch memory. Disabled dot8 also rejects its legal form.
        const auto stores_before=stores;
        load({ASTER_DOT8?0x0000100bu:0x0000000bu});b.write(0,1);
        bool trapped=false;for(unsigned n=0;n<10000 && !trapped;++n)trapped=b.read(4)&2;
        require(trapped && stores==stores_before && b.read(0x50)==0 && b.read(0x5c)==0,"unsupported custom trapping/side effect");stopped();
        // LR.W to the native dot8 data bank must not read/modify it; executable
        // MMIO remains denied independently of the enabled data-register map.
        load({0x200030b7,0x20008093,0x1000a12f});b.write(0,1);
        trapped=false;for(unsigned n=0;n<10000 && !trapped;++n)trapped=b.read(4)&2;
        require(trapped && stores==stores_before && b.read(0x50)==0x15 && b.read(0x54)==0x20003200 &&
            b.read(0x58)==0x1000a12f && b.read(0x5c)==8,"native dot8 MMIO LR permission");stopped();
        load({0x200030b7,0x20008093,0x00008067});b.write(0,1);
        trapped=false;for(unsigned n=0;n<10000 && !trapped;++n)trapped=b.read(4)&2;
        require(trapped && stores==stores_before && b.read(0x84)==0,"native dot8 MMIO instruction permission");stopped();
        std::cout<<"PASS: dot8 Linux bridge enabled="<<ASTER_DOT8<<" harts="<<ASTER_HART_COUNT<<" cache="<<ASTER_L1
            <<" all AXI byte offsets/strobes, exact live banks, custom/atomic/fetch denial, final STOPPED\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
