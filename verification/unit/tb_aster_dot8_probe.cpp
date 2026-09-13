#include "Vaster_dot8_probe.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef ASTER_DOT8_ENABLE
#define ASTER_DOT8_ENABLE 1
#endif
#ifndef ASTER_ICACHE_ENABLE
#define ASTER_ICACHE_ENABLE 0
#endif

// Independent ISA interpreter + delayed lower memory/command backend. This
// proves the production hart's arithmetic, writeback and PCPI arbitration,
// not SoC permissions/coherence: the backend below is explicitly a mock.
static void require(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static std::int64_t signed32(std::uint32_t x) { return x < 0x80000000u ? x : std::int64_t(x)-0x100000000ll; }
static std::uint32_t dot(std::uint32_t a, std::uint32_t b) {
    std::int64_t sum=0;
    for (unsigned lane=0; lane<4; ++lane) {
        int x=(a>>(8*lane))&255, y=(b>>(8*lane))&255;
        if (x>=128) x-=256;
        if (y>=128) y-=256;
        sum += x*y;
    }
    require(sum>=-65024 && sum<=65536, "reference sum range");
    return std::uint32_t(sum);
}
static std::uint32_t muldiv(unsigned kind, std::uint32_t a, std::uint32_t b) {
    const auto x=signed32(a), y=signed32(b);
    switch (kind) {
        case 0: return std::uint32_t(std::uint64_t(a)*b);
        case 1: return std::uint32_t(std::uint64_t(x*y)>>32);
        case 2: return std::uint32_t(std::uint64_t(x*std::int64_t(b))>>32);
        case 3: return std::uint32_t((std::uint64_t(a)*b)>>32);
        case 4: return b ? std::uint32_t(x/y) : 0xffffffffu;
        case 5: return b ? a/b : 0xffffffffu;
        case 6: return b ? std::uint32_t(x%y) : a;
        case 7: return b ? a%b : a;
    }
    throw std::runtime_error("reference M encoding");
}
static std::uint32_t r(unsigned opcode, unsigned rd, unsigned a, unsigned b, unsigned fn=0) {
    return opcode | (rd<<7) | (a<<15) | (b<<20) | fn;
}
static void li(std::vector<std::uint32_t>& code, unsigned rd, std::uint32_t value) {
    code.push_back(((value+0x800u)&0xfffff000u)|(rd<<7)|0x37);
    code.push_back(((value&4095)<<20)|(rd<<15)|(rd<<7)|0x13);
}
static std::uint32_t sw(unsigned rs, unsigned offset) {
    return ((offset>>5)<<25)|(rs<<20)|(30<<15)|(2<<12)|((offset&31)<<7)|0x23;
}
struct Case {
    unsigned rd=3, a=1, b=2, pause=0;
    std::uint32_t function=0, opcode=0x0b;
    bool valid=true;
};
static std::mt19937 rng(0xa57e8);
static std::vector<std::uint32_t> program(const Case& c) {
    std::vector<std::uint32_t> code;
    const std::array<std::uint32_t,12> corners={0,1,0xffffffffu,0x80808080,0x7f7f7f7f,0x80000000,
        0x7fffffff,0xff000001,0x0100ff80,0x00000080,0xaaaaaaaa,0x55555555};
    for (unsigned reg=1; reg<32; ++reg) li(code, reg, (rng()&1) ? rng() : corners[rng()%corners.size()]);
    code.push_back(r(c.opcode,c.rd,c.a,c.b,c.function));
    code.push_back(r(0x0b,c.rd,c.rd,c.b));
    code.push_back(r(0x0b,c.a,c.a,c.a));
    for (unsigned kind=0; kind<8; ++kind) {
        code.push_back(r(0x33,5+kind,c.a,c.b,0x02000000|(kind<<12)));
        code.push_back(r(0x0b,13,5+kind,c.a));
    }
    li(code,20,0x10000800); li(code,21,37);
    code.push_back(r(0x2f,22,20,0,0x10002000)); // LR.W
    code.push_back(r(0x0b,23,22,21));
    code.push_back(r(0x2f,24,20,21,0x18002000)); // SC.W must retain reservation through dot8
    code.push_back(r(0x2f,25,20,21,0x00002000)); // AMOADD.W
    code.push_back(r(0x0b,26,25,23));
    code.push_back(0x0ff0000f); // fence
    li(code,30,0x10000000);
    for (unsigned reg=0; reg<32; ++reg) code.push_back(sw(reg,reg*4));
    code.push_back(0x00100073); // EBREAK: no retirement
    return code;
}
using Ram=std::array<std::uint32_t,1024>;
struct Retired { unsigned pc, rd; std::uint32_t insn, value; };
struct Oracle { Ram ram; std::vector<Retired> retired; unsigned halt=0, dots=0, commands=0, stores=0; };
static Oracle interpret(const std::vector<std::uint32_t>& code, const Ram& initial) {
    Oracle result; result.ram=initial;
    std::array<std::uint32_t,32> regs{};
    bool reserved=false;
    for (unsigned pc=0; pc<code.size()*4; pc+=4) {
        const auto insn=code[pc/4];
        unsigned rd=(insn>>7)&31, a=(insn>>15)&31, b=(insn>>20)&31;
        std::uint32_t value=0;
        bool halt=false;
        switch (insn&127) {
            case 0x37: value=insn&0xfffff000; break;
            case 0x13: value=regs[a]+std::uint32_t((int(insn>>20)^2048)-2048); break;
            case 0x0b:
                if (ASTER_DOT8_ENABLE && (insn&0xfe00707f)==0x0b) { value=dot(regs[a],regs[b]); ++result.dots; }
                else halt=true;
                break;
            case 0x33: value=muldiv((insn>>12)&7,regs[a],regs[b]); break;
            case 0x2f: {
                require(regs[a]==0x10000800, "reference atomic address");
                auto& word=result.ram[512];
                switch (insn>>27) {
                    case 2: value=word; reserved=true; break;
                    case 3: value=reserved?0:1; if (reserved) word=regs[b]; reserved=false; break;
                    case 0: value=word; word+=regs[b]; reserved=false; break;
                    default: throw std::runtime_error("reference atomic operation");
                }
                ++result.commands; break;
            }
            case 0x23: {
                const auto addr=regs[a]+((insn>>25)<<5)+((insn>>7)&31);
                require(addr>=0x10000000 && addr<0x10001000 && !(addr&3), "reference native store");
                result.ram[(addr-0x10000000)/4]=regs[b]; ++result.stores; rd=0; break;
            }
            case 0x0f: rd=0; break;
            default: halt=true; break;
        }
        if (halt) { result.halt=pc; return result; }
        if (rd) regs[rd]=value;
        result.retired.push_back({pc,rd,insn,rd?value:0});
    }
    throw std::runtime_error("reference failed to halt");
}
struct Totals { std::uint64_t runs=0,dots=0,commands=0,retired=0,resets=0,overlap=0,cycles=0;
    unsigned min_retire_latency=1000000,max_retire_latency=0; } total;
static void reset(Vaster_dot8_probe& d) {
    d.resetn=0; d.lower_ready=0; d.lower_rdata=0; d.lower_fault=0; d.dot8_admit=0; d.eval();
    require(!d.lower_valid && !d.pcpi_wait && !d.pcpi_ready && !d.pcpi_wr &&
        !d.dot8_events && !d.dot8_busy && !d.instr_retired && !d.fault_valid, "reset output gating");
    for (unsigned n=0;n<8;++n) { d.clk=0; d.eval(); d.clk=1; d.eval(); }
    require(!d.atomic_busy && !d.dot8_busy && !d.pcpi_rd, "reset retained arithmetic state");
    d.resetn=1;
}
// Reset stages: paused admission, captured products, held result before core
// acceptance, and acknowledged result before architectural retirement.
static void run(Vaster_dot8_probe& d, const Case& c, int latency, unsigned reset_stage=0) {
    reset(d);
    const auto code=program(c);
    Ram ram; for (auto& word:ram) word=rng();
    const auto expected=interpret(code,ram);
    const bool should_halt=!c.valid || !ASTER_DOT8_ENABLE;
    require(expected.halt==(should_halt?62u*4:unsigned(code.size()-1)*4), "test encoding fixture");
    bool pending=false,reserved=false;
    unsigned left=0,addr=0,data=0,mask=0,op=0,atomic=0,instr=0;
    unsigned retired=0,accepts=0,completes=0,dot_retired=0,commands=0,stores=0,ready=0;
    unsigned trap_cycles=0,paused=0,accepted_at=0;
    bool withdrawing=false,got_ack=false;
    for (unsigned cycle=0;cycle<200000;++cycle) {
        d.clk=0; d.lower_ready=0; d.lower_fault=0;
        d.dot8_admit=paused>=c.pause && !withdrawing; d.eval();
        if (!d.dot8_busy) withdrawing=false;
        if ((d.dot8_events&2) && !d.dot8_busy && !d.dot8_admit) ++paused;
        if (reset_stage && ((reset_stage==1 && paused>=65) ||
            (reset_stage==2 && (d.dot8_events&4)) ||
            (reset_stage==3 && d.pcpi_ready && d.dot8_busy) ||
            (reset_stage==4 && got_ack && !d.instr_retired))) {
            require(!dot_retired, "reset missed first dot retirement");
            reset(d); ++total.resets;
            // Keep reset asserted: no instruction or late memory/compute event.
            d.resetn=0;
            for (unsigned n=0;n<30;++n) { d.clk=0;d.eval();d.clk=1;d.eval();
                require(!d.dot8_events && !d.pcpi_ready && !d.lower_valid, "late reset event"); }
            return;
        }
        if (d.lower_valid) {
            if (d.dot8_busy && d.lower_instr) ++total.overlap;
            if (!pending) {
                pending=true; addr=d.lower_addr; data=d.lower_wdata; mask=d.lower_wstrb;
                atomic=d.lower_atomic; instr=d.lower_instr; op=d.lower_op;
                left=latency<0 ? rng()%81 : unsigned(latency);
            } else require(addr==d.lower_addr && data==d.lower_wdata && mask==d.lower_wstrb &&
                atomic==d.lower_atomic && instr==d.lower_instr && (!atomic || op==d.lower_op),
                "held lower request changed");
            if (left) --left;
            else {
                d.lower_ready=1; d.lower_rdata=0;
                if (atomic) {
                    require(addr==0x10000800 && mask==15 && !instr && !d.dot8_busy,
                        "dot8 emitted/overlapped atomic side effect");
                    auto& word=ram[512];
                    if (op==2) { d.lower_rdata=word; reserved=true; }
                    else if (op==3) { d.lower_rdata=reserved?0:1; if (reserved) word=data; reserved=false; }
                    else if (op==0) { d.lower_rdata=word; word+=data; reserved=false; }
                    else throw std::runtime_error("unexpected atomic command");
                    ++commands;
                } else if (mask) {
                    require(addr>=0x10000000 && addr<0x10001000 && !(addr&3) && mask==15 && !instr,
                        "unexpected native write / fetch side effect");
                    ram[(addr-0x10000000)/4]=data; ++stores;
                } else if (addr<0x10000) {
                    d.lower_rdata=addr/4<code.size()?code[addr/4]:0x00100073;
                } else throw std::runtime_error("unexpected native data read");
                pending=false;
            }
        } else require(!pending, "held lower request withdrawn");
        d.eval();
        if (d.dot8_events&1) { ++accepts; accepted_at=cycle; withdrawing=true; }
        if (d.dot8_events&4) ++completes;
        if (d.dot8_events&8) ++dot_retired;
        if (d.pcpi_ready && d.pcpi_valid && (d.pcpi_insn&0xfe00707f)==0x0b) { ++ready; got_ack=true; }
        require(!d.fault_valid, "legal mixed workload raised atomic fault");
        d.clk=1; d.eval(); ++total.cycles;
        if (d.instr_retired) {
            require(retired<expected.retired.size(), "extra / trapping retirement");
            const auto& e=expected.retired[retired++];
            require(d.retired_pc==e.pc && d.retired_insn==e.insn, "retirement identity/order/exactly-once");
            require(d.retired_rd==e.rd && (!e.rd || d.retired_value==e.value), "RVFI actual register writeback mismatch");
            if ((e.insn&0xfe00707f)==0x0b) {
                total.min_retire_latency=std::min(total.min_retire_latency,cycle-accepted_at);
                total.max_retire_latency=std::max(total.max_retire_latency,cycle-accepted_at);
            }
        }
        if (d.trap) {
            ++trap_cycles;
            if (trap_cycles>3) require(d.retired_pc==expected.halt &&
                d.retired_insn==code[expected.halt/4], "trap identity");
            if (trap_cycles==40) break;
        }
    }
    require(!reset_stage, "reset stage was not reached");
    require(trap_cycles==40 && retired==expected.retired.size(), "timeout/missing retirement");
    require(ram==expected.ram, "complete RAM differs from independent interpreter");
    require(commands==expected.commands && stores==expected.stores, "side effect count");
    require(accepts==expected.dots && completes==accepts && dot_retired==accepts && ready==accepts,
        "dot8 acceptance/completion/handshake/retirement count");
    if (should_halt) require(!d.dot8_busy && !d.atomic_busy, "unsupported instruction retained ownership");
    ++total.runs; total.dots+=accepts; total.commands+=commands; total.retired+=retired;
}
int main(int argc,char** argv) {
    std::string context;
    try {
        Verilated::commandArgs(argc,argv); Vaster_dot8_probe d;
        const std::array<int,5> delays={0,1,19,65,-1};
        for (unsigned rd=0;rd<32;++rd) for (unsigned a=0;a<32;++a) for (unsigned b=0;b<32;++b) {
            Case c; c.rd=rd;c.a=a;c.b=b;c.pause=(rd+a+b)%19;
            context="registers "+std::to_string(rd)+"/"+std::to_string(a)+"/"+std::to_string(b);
            run(d,c,delays[(rd+a+b)%delays.size()]);
        }
        for (unsigned f7=0;f7<128;++f7) for (unsigned f3=0;f3<8;++f3) {
            if (!f7 && !f3) continue;
            Case c;c.function=(f7<<25)|(f3<<12);c.valid=false;
            context="illegal function "+std::to_string(c.function);run(d,c,-1);
        }
        for (unsigned opcode:{0x2bu,0x5bu,0x7bu}) {
            Case c;c.opcode=opcode;c.valid=false;context="other custom opcode";run(d,c,65);
        }
        if (ASTER_DOT8_ENABLE) {
            for (unsigned stage=1;stage<=4;++stage) for (unsigned repeat=0;repeat<8;++repeat) {
                Case c;c.pause=stage==1?1025:0;context="reset stage "+std::to_string(stage);
                run(d,c,-1,stage);run(d,Case{},-1);
            }
            Case c;c.pause=1025;context="long admission wait";run(d,c,65);
            require(total.overlap>0, "no cached/native prefetch overlap exercised");
        }
        std::cout<<"PASS: dot8 real hart enabled="<<ASTER_DOT8_ENABLE<<" icache="<<ASTER_ICACHE_ENABLE
            <<" seed=0xa57e8 programs="<<total.runs<<" dot8="<<total.dots<<" atomic="<<total.commands
            <<" retired="<<total.retired<<" resets="<<total.resets<<" prefetch_overlap="<<total.overlap
            <<" cycles="<<total.cycles<<" accept_to_retire_edges="
            <<(total.dots?total.min_retire_latency:0)<<".."<<total.max_retire_latency
            <<"; complete RAM and RVFI writeback, aliases/x0, M/A, traps, admission/reset\n";
        return 0;
    } catch (const std::exception& e) { std::cerr<<"FAIL: "<<context<<": "<<e.what()<<'\n';return 1; }
}
