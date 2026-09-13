#include "Vaster_dot8_perf.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>

#ifndef ASTER_HARTS
#define ASTER_HARTS 2
#endif
static void require(bool ok,const char* why) { if (!ok) throw std::runtime_error(why); }
int main(int argc,char** argv) {
    try {
        Verilated::commandArgs(argc,argv); Vaster_dot8_perf d; std::mt19937 rng(0xa57e8);
        std::array<std::uint64_t,8> expected{}; bool running=false;
        auto step=[&]() {
            d.clk=0;d.eval();
            if (!d.resetn || d.start) { expected.fill(0);running=d.resetn && d.start; }
            else if (d.freeze) running=false;
            else if (d.resume_counting) running=true;
            else if (running) for (unsigned i=0;i<4*ASTER_HARTS;++i) expected[i]+=(d.events>>i)&1;
            d.clk=1;d.eval();
            require(d.running==running,"window running state/priority");
            for (unsigned i=0;i<8;++i) require(d.counters[i]==expected[i],"event/command edge/count/carry");
        };
        auto read=[&](unsigned addr,std::uint32_t expected_value) {
            d.addr=addr;d.eval();require(d.rdata==expected_value,"counter address/read/metadata");
        };
        d.resetn=d.start=d.freeze=d.resume_counting=0;d.events=255;step();d.resetn=1;
        for (unsigned state=0;state<2;++state) for (unsigned command=0;command<8;++command)
        for (unsigned event=0;event<256;++event) {
            d.start=state;d.freeze=!state;d.resume_counting=0;step();
            d.start=command&1;d.freeze=(command>>1)&1;d.resume_counting=(command>>2)&1;
            d.events=event;step();
        }
        d.start=1;d.freeze=d.resume_counting=0;step();d.start=0;
        for (unsigned i=0;i<4*ASTER_HARTS;++i) {
            // Observation-state deposit only, no test mode in synthesizable RTL.
            expected[i]=(i&1)?~std::uint64_t(0)-2:0xfffffffd;
            d.counters[i]=expected[i];
        }
        d.events=255;for (unsigned n=0;n<8;++n) step();
        for (unsigned n=0;n<20000;++n) {
            d.events=rng()&255;d.start=rng()%191==0;d.freeze=rng()%23==0;
            d.resume_counting=rng()%19==0;d.resetn=rng()%251!=0;step();
            for (unsigned i=0;i<8;++i) {read(0x200+8*i,expected[i]);read(0x204+8*i,expected[i]>>32);}
        }
        for (unsigned addr=0;addr<4096;++addr) {
            if (addr>=0x200 && addr<0x240 && !(addr&3)) continue;
            if (addr>=0x280 && addr<=0x2a0 && !(addr&3)) continue;
            read(addr,0);
        }
        read(0x280,running);read(0x284,6);read(0x288,1);read(0x28c,0x0b);read(0x290,0xfe00707f);
        read(0x294,4);read(0x298,ASTER_HARTS);read(0x29c,1);read(0x2a0,31250000);
        std::cout<<"PASS: dot8 ABI 6 harts="<<ASTER_HARTS<<" eight counters, 4096 command/event cases,"
            " 20000 seeded steps, common-window priority/exclusion, full 32/64-bit wrap, absent-hart zeros, all offsets\n";
        return 0;
    } catch (const std::exception& e) {std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}
