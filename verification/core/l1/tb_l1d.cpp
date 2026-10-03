// Random unit test of the Aster core's L1 data cache (rtl/aster_core/aster_l1d.sv;
// milestone 18.6), against a flat memory: a random core side (loads, stores of
// every width, lr/sc, the AMOs, I/O and error addresses, requests withdrawn
// before acceptance) and a memory side with random back-pressure and latencies
// (one or two cycles, plus up to two, plus 16-63 one access in 16 with seeds
// 2 and 3 mod 4). Every answer's data is checked against the reference (stores
// excepted), d_rsp_error in the cycle after each acceptance, at most two
// requests held, and every memory-side access other than a refill read
// against the expected one, exactly once and in order; every request is
// accepted within 2,000 cycles of being presented and answered within 2,000 of
// acceptance (and the run answers at least one per 50 cycles on average).
//     l1d_unit <seed> [cycles]
#include "Vl1d_unit.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <random>
#include <string>

static uint32_t initial(uint32_t w) { return w * 2654435761u ^ 0x5a5a1234u; }
struct Mem {
    std::map<uint32_t, uint32_t> m;
    bool res = false; uint32_t resw = 0;
    uint32_t rd(uint32_t a) { a &= ~3u; auto it = m.find(a); return it == m.end() ? initial(a) : it->second; }
    void wr(uint32_t a, uint32_t d, uint32_t be) {
        uint32_t v = rd(a), mask = 0;
        for (int i = 0; i < 4; ++i) if (be >> i & 1) mask |= 0xffu << (8 * i);
        m[a & ~3u] = (v & ~mask) | (d & mask);
    }
    // perform op; returns response data
    uint32_t perform(uint32_t op, uint32_t a, uint32_t d, uint32_t be) {
        uint32_t old = rd(a);
        switch (op) {
        case 0: return old;
        case 1: wr(a, d, be); return 0;
        case 2: res = true; resw = a & ~3u; return old;
        case 3: { bool ok = res && resw == (a & ~3u); res = false; if (ok) wr(a, d, 0xf); return ok ? 0 : 1; }
        default: {
            uint32_t n = 0; int32_t so = int32_t(old), sd = int32_t(d);
            switch (op) {
            case 4: n = d; break; case 5: n = old + d; break; case 6: n = old ^ d; break;
            case 7: n = old & d; break; case 8: n = old | d; break;
            case 9: n = so < sd ? old : d; break; case 10: n = so > sd ? old : d; break;
            case 11: n = old < d ? old : d; break; default: n = old > d ? old : d; break;
            }
            wr(a, n, 0xf); return old;
        }
        }
    }
};
static bool cacheable(uint32_t a, uint32_t bytes) { return a - 0x80000000u < bytes; }
static bool io(uint32_t a) { return (a & ~0xFFFFu) == 0x20000000u || (a & ~3u) == 0x30000000u; }

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    uint32_t seed = argc > 1 ? std::stoul(argv[1]) : 1;
    uint64_t ncycles = argc > 2 ? std::stoull(argv[2]) : 200000;
    std::mt19937 rng(seed);
    Vl1d_unit d;
    const uint32_t bytes = 0x10000;
    d.cacheable_bytes = bytes;
    d.clk = 0; d.rst_n = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.rst_n = 1; d.eval();
    Mem mem, ref;
    struct Exp { uint32_t op, addr, data; bool err; bool check; uint64_t at; };
    std::deque<Exp> expect;            // core-side responses expected, in order
    struct MRsp { uint64_t at; uint32_t data; };
    std::deque<MRsp> mq;               // memory-side responses in flight
    struct Acc { uint32_t op, addr, data, be; };
    std::deque<Acc> mexp;              // expected non-refill memory-side accesses
    int max_inflight = 2 + rng() % 3;
    // core driver state
    bool pv = false; uint32_t pop = 0, paddr = 0, pdata = 0, pbe = 0;
    bool acc_last = false, err_last = false;
    uint64_t answered = 0, hits_like = 0;
    int stallmode = seed % 4;   // 0 none, 1 short, 2 long, 3 mixed
    auto gen = [&]() {
        uint32_t r = rng() % 100;
        uint32_t a;
        if (r < 80) a = 0x80000000u + (rng() % 3) * 4096 + (rng() % 6) * 16 + (rng() % 4) * 4;
        else if (r < 92) a = 0x20000000u + (rng() % 4) * 4;
        else if (r < 95) a = 0x30000000u;
        else a = 0x40000000u + (rng() % 4) * 4;
        uint32_t o = rng() % 100;
        uint32_t op = o < 45 ? 0 : o < 80 ? 1 : o < 85 ? 2 : o < 90 ? 3 : 4 + rng() % 9;
        if (!cacheable(a, bytes) && op > 1 && (a & ~3u) == 0x30000000u) op = rng() % 2;
        uint32_t be = 0xf;
        if (op <= 1) {
            uint32_t s = rng() % 3;
            if (s == 0) { uint32_t b = rng() % 4; be = 1u << b; a += b; }
            else if (s == 1) { uint32_t h = rng() % 2; be = 3u << (2 * h); a += 2 * h; }
        }
        pop = op; paddr = a; pdata = rng(); pbe = be; pv = true;
    };
    uint64_t waiting_since = 0;
    for (uint64_t cyc = 0; cyc < ncycles; ++cyc) {
        // inputs
        size_t outstanding = expect.size();
        if (!pv && outstanding < 2 && rng() % 100 < 70) gen();
        else if (pv && rng() % 100 < 3) pv = false;      // a flush withdraws it
        d.d_req_valid = pv; d.d_req_op = pop; d.d_req_addr = paddr; d.d_req_wdata = pdata; d.d_req_be = pbe;
        bool mready = int(mq.size()) < max_inflight;
        if (stallmode && rng() % 4 == 0) mready = false;
        d.m_req_ready = mready;
        bool mrsp = !mq.empty() && mq.front().at <= cyc;
        d.m_rsp_valid = mrsp; d.m_rsp_rdata = mrsp ? mq.front().data : rng(); d.m_rsp_error = 0;
        d.eval();
        // checks this cycle
        if (acc_last) {
            if (bool(d.d_rsp_error) != err_last) { printf("FAIL seed %u cyc %llu: d_rsp_error %d expected %d\n", seed, (unsigned long long)cyc, d.d_rsp_error, err_last); return 1; }
        }
        if (d.d_rsp_valid) {
            if (expect.empty()) { printf("FAIL seed %u cyc %llu: unexpected response\n", seed, (unsigned long long)cyc); return 1; }
            Exp e = expect.front(); expect.pop_front(); ++answered;
            if (e.check && d.d_rsp_rdata != e.data) {
                printf("FAIL seed %u cyc %llu: op %u addr %08x rdata %08x expected %08x\n", seed, (unsigned long long)cyc, e.op, e.addr, d.d_rsp_rdata, e.data);
                return 1;
            }
        }
        bool accept = d.d_req_valid && d.d_req_ready;
        bool macc = d.m_req_valid && d.m_req_ready;
        if (accept) {
            if (expect.size() >= 2) { printf("FAIL: third in flight\n"); return 1; }
            bool err = !cacheable(paddr, bytes) && !io(paddr);
            Exp e{pop, paddr, 0, err, false, cyc};
            if (!err) {
                e.data = ref.perform(pop, paddr, pdata, pbe);
                e.check = pop != 1;
                if (!cacheable(paddr, bytes) || pop != 0) mexp.push_back({pop, paddr, pdata, pbe});
            }
            expect.push_back(e);
            acc_last = true; err_last = err; pv = false;
        } else acc_last = false;
        if (!pv) waiting_since = cyc;            // a presented request is accepted within 2,000 cycles
        else if (cyc - waiting_since > 2000) {
            printf("FAIL seed %u cyc %llu: a request not accepted for 2000 cycles\n", seed, (unsigned long long)cyc);
            return 1;
        }
        if (macc) {
            uint32_t op = d.m_req_op, a = d.m_req_addr;
            bool refill = op == 0 && cacheable(a, bytes);
            if (!refill) {
                if (mexp.empty()) { printf("FAIL seed %u: unexpected mem access op %u %08x\n", seed, op, a); return 1; }
                Acc x = mexp.front(); mexp.pop_front();
                if (x.op != op || x.addr != a || x.be != d.m_req_be || (op != 0 && op != 2 && x.data != d.m_req_wdata)) {
                    printf("FAIL seed %u cyc %llu: mem access op %u %08x be %x, expected op %u %08x be %x\n", seed, (unsigned long long)cyc, op, a, d.m_req_be, x.op, x.addr, x.be); return 1;
                }
            } else if ((a & 3) || d.m_req_be != 0xf) { printf("FAIL: odd refill\n"); return 1; }
            uint32_t data = mem.perform(op, a, d.m_req_wdata, d.m_req_be);
            uint64_t lat = 1 + rng() % 2;
            if (stallmode >= 1) lat += rng() % 3;
            if (stallmode >= 2 && rng() % 16 == 0) lat += 16 + rng() % 48;
            uint64_t at = cyc + lat;
            if (!mq.empty() && mq.back().at >= at) at = mq.back().at + 1;
            mq.push_back({at, data});
        }
        if (mrsp) mq.pop_front();
        // Progress: the oldest request is answered within 2,000 cycles (a refill
        // under the longest stalls takes a few hundred).
        if (!expect.empty() && cyc - expect.front().at > 2000) {
            printf("FAIL seed %u cyc %llu: no answer for 2000 cycles\n", seed, (unsigned long long)cyc);
            return 1;
        }
        d.clk = 1; d.eval();
        d.clk = 0; d.eval();
    }
    if (answered < ncycles / 50) {
        printf("FAIL seed %u: only %llu answers in %llu cycles\n", seed, (unsigned long long)answered,
               (unsigned long long)ncycles);
        return 1;
    }
    printf("PASS seed %u answered %llu\n", seed, (unsigned long long)answered);
    return 0;
}
