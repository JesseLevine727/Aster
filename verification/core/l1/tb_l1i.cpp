// Random unit test of the Aster core's L1 instruction cache
// (rtl/aster_core/aster_l1i.sv; milestone 18.6): random fetches (some outside
// the cacheable memory, some replaced before acceptance, as a redirect does),
// a memory whose words are rewritten at random, each rewrite followed within
// eight cycles by an invalidation (fence.i), and a memory side with random
// back-pressure and latencies (as tb_l1d.cpp). A fetch accepted after an
// invalidation must return the word as of that invalidation or later; every
// answer comes in order, with an error exactly outside the cacheable memory;
// every fetch is accepted within 2,000 cycles of being presented and answered
// within 2,000 of acceptance (and the run answers at least one per 50 cycles
// on average).
//     l1i_unit <seed> [cycles]
#include "Vaster_l1i.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <random>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    uint32_t seed = argc > 1 ? std::stoul(argv[1]) : 1;
    uint64_t ncycles = argc > 2 ? std::stoull(argv[2]) : 200000;
    std::mt19937 rng(seed);
    Vaster_l1i d;
    const uint32_t bytes = 0x10000;
    d.cacheable_bytes = bytes; d.invalidate = 0;
    d.clk = 0; d.rst_n = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.rst_n = 1; d.eval();
    // memory: per word history of (cycle performed, value)
    std::map<uint32_t, std::vector<std::pair<uint64_t, uint32_t>>> hist;
    auto value_now = [&](uint32_t a) -> uint32_t { auto it = hist.find(a); return it == hist.end() ? a * 2654435761u : it->second.back().second; };
    auto allowed = [&](uint32_t a, uint64_t from, uint32_t v) {
        auto it = hist.find(a);
        if (it == hist.end()) return v == a * 2654435761u;
        auto& h = it->second;
        // the value held at time 'from' (latest entry with t < from), and every later one
        uint32_t at_from = a * 2654435761u; size_t i = 0;
        for (; i < h.size() && h[i].first < from; ++i) at_from = h[i].second;
        if (v == at_from) return true;
        for (; i < h.size(); ++i) if (h[i].second == v) return true;
        return false;
    };
    struct Exp { uint32_t addr; bool err; uint64_t from; bool check; uint64_t at; };
    std::deque<Exp> expect;
    struct MRsp { uint64_t at; uint32_t data; };
    std::deque<MRsp> mq;
    int max_inflight = 2 + rng() % 3;
    int stallmode = seed % 4;
    bool pv = false; uint32_t paddr = 0;
    uint64_t last_pulse_edge = 0;  // fetches accepted at an edge >= this must see memory as of the pulse
    uint64_t pulse_cycle = 0;
    bool pulse_pending = false; uint64_t pulse_at = 0;
    uint64_t answered = 0, checked = 0;
    uint64_t waiting_since = 0;
    for (uint64_t cyc = 0; cyc < ncycles; ++cyc) {
        // a "store" performs, then a fence.i pulse some cycles later
        if (!pulse_pending && rng() % 200 == 0) {
            uint32_t a = 0x80000000u + (rng() % 2) * 4096 + (rng() % 4) * 16 + (rng() % 4) * 4;
            hist[a].push_back({cyc, rng()});
            pulse_pending = true; pulse_at = cyc + 1 + rng() % 8;
        }
        bool pulse = pulse_pending && cyc >= pulse_at;
        if (pulse) pulse_pending = false;
        d.invalidate = pulse;
        if (!pv && rng() % 100 < 80) {
            uint32_t r = rng() % 100;
            paddr = r < 95 ? 0x80000000u + (rng() % 2) * 4096 + (rng() % 4) * 16 + (rng() % 4) * 4 : 0x20000000u + (rng() % 4) * 4;
            pv = true;
        } else if (pv && rng() % 100 < 5) {   // a redirect replaces it
            paddr = 0x80000000u + (rng() % 2) * 4096 + (rng() % 4) * 16 + (rng() % 4) * 4;
        }
        d.i_req_valid = pv; d.i_req_addr = paddr >> 2;
        bool mready = int(mq.size()) < max_inflight;
        if (stallmode && rng() % 4 == 0) mready = false;
        d.m_req_ready = mready;
        bool mrsp = !mq.empty() && mq.front().at <= cyc;
        d.m_rsp_valid = mrsp; d.m_rsp_data = mrsp ? mq.front().data : rng(); d.m_rsp_error = 0;
        d.eval();
        if (d.i_rsp_valid) {
            if (expect.empty()) { printf("FAIL seed %u: unexpected response\n", seed); return 1; }
            Exp e = expect.front(); expect.pop_front(); ++answered;
            if (bool(d.i_rsp_error) != e.err) { printf("FAIL seed %u cyc %llu: error %d expected %d\n", seed, (unsigned long long)cyc, d.i_rsp_error, e.err); return 1; }
            if (!e.err && e.check) {
                ++checked;
                if (!allowed(e.addr, e.from, d.i_rsp_data)) {
                    printf("FAIL seed %u cyc %llu: fetch %08x data %08x stale (from %llu)\n", seed, (unsigned long long)cyc, e.addr, d.i_rsp_data, (unsigned long long)e.from);
                    return 1;
                }
            }
        }
        if (pulse) pulse_cycle = cyc;   // the pulse clears the lines at the edge ending this cycle
        bool accept = d.i_req_valid && d.i_req_ready;
        if (accept) {
            bool err = paddr - 0x80000000u >= bytes;
            // accepted at the edge ending cycle 'cyc': if a pulse came in an earlier cycle (or this one), memory as of that pulse
            expect.push_back({paddr, err, pulse_cycle, true, cyc});
            pv = false;
        }
        if (!pv) waiting_since = cyc;            // a presented request is accepted within 2,000 cycles
        else if (cyc - waiting_since > 2000) {
            printf("FAIL seed %u cyc %llu: a request not accepted for 2000 cycles\n", seed, (unsigned long long)cyc);
            return 1;
        }
        if (d.m_req_valid && d.m_req_ready) {
            uint32_t a = uint32_t(d.m_req_addr) << 2;
            uint64_t lat = 1 + rng() % 2;
            if (stallmode >= 1) lat += rng() % 3;
            if (stallmode >= 2 && rng() % 16 == 0) lat += 16 + rng() % 48;
            uint64_t at = cyc + lat;
            if (!mq.empty() && mq.back().at >= at) at = mq.back().at + 1;
            mq.push_back({at, value_now(a)});
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
    printf("PASS seed %u answered %llu checked %llu\n", seed, (unsigned long long)answered, (unsigned long long)checked);
    return 0;
}
