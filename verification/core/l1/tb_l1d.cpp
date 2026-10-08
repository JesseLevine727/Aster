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
// acceptance (and the run answers at least one per 50 cycles on average), and
// the memory side never has more than two requests in flight. Another master
// writes a remote region at random (one write in about 30 cycles), its line
// snooped in that cycle or — with seeds 4-7 mod 8 — late, within the
// contract (cpu.md §9, 18.6): queued until a random 0-15 cycles pass or the
// cache presents a memory-side request, whose acceptance then waits for the
// queue to empty. The core side loads and stores there too; a load must return
// the newest value whose snoop came (or own store was accepted) in or before
// the cycle of its lookup, or a newer one, and no older than the core's own
// last store to the word before the load.
//
// Built with L1D_SNOOPS=3 (the Phase 20 fabric's data cache, soc.md §4.6;
// milestone 20.1), there are three other masters, each snooped on its own
// port (one write a cycle each, never two to one 8-byte unit in a cycle, as
// the fabric's bank rule), so up to three snoops come in one cycle. A run of
// a million cycles or more must see snoops on two or more ports in one cycle,
// and a snoop on each port of a line the cache is refilling.
//     l1d_unit <seed> [cycles]
#include "Vl1d_unit.h"
#include "verilated.h"
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <map>
#include <random>
#include <string>
#include <vector>

#ifndef L1D_SNOOPS
#define L1D_SNOOPS 1
#endif

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
#ifdef L1D_UNCACHED
// The cache off (DCACHE = 0, 20.4): a load of main memory is an access of its own, as a store is; no refills.
static constexpr bool kUncached = true;
#else
static constexpr bool kUncached = false;
#endif
static bool io(uint32_t a) { return (a & ~0xFFFFu) == 0x20000000u || (a & ~3u) == 0x30000000u; }

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    uint32_t seed = argc > 1 ? std::stoul(argv[1]) : 1;
    uint64_t ncycles = argc > 2 ? std::stoull(argv[2]) : 200000;
    std::mt19937 rng(seed);
    Vl1d_unit d;
#ifdef L1D_SPAN17
    // main memory's tag span (TAG_SPAN 17): 96 KiB cacheable; the core's pages set each kept tag bit
    // (12-16) in some page, and the remote region's pages are 3, 7, 11 and 19 (snooped lines that differ
    // from held ones in bit 14, 15 or 16 alone, as pages 1 and 2 do in bits 13 and 12)
    const uint32_t bytes = 0x18000;
    static const uint32_t pages[] = {0, 1, 2, 4, 8, 16};
    static const uint32_t remote_pages[] = {0, 0x4000, 0x8000, 0x10000};
#else
    const uint32_t bytes = 0x10000;
    static const uint32_t pages[] = {0, 1, 2};
    static const uint32_t remote_pages[] = {0};
#endif
    const uint32_t npages = sizeof pages / sizeof pages[0], nremote = sizeof remote_pages / sizeof remote_pages[0];
    d.cacheable_bytes = bytes;
    d.clk = 0; d.rst_n = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.eval(); d.clk = 1; d.eval(); d.clk = 0; d.rst_n = 1; d.eval();
    Mem mem, ref;
    struct Exp {
        uint32_t op, addr, data; bool err; bool check; uint64_t at;
        bool remote = false; uint64_t lookup = 0; uint32_t own = 0;   // own: the core's stores to the word before it
    };
    // The remote region (page 3: its lines share indices with the core's
    // pages): per word, the values memory held, in order — another master's
    // writes and the core's own stores as memory performed them: (cycle,
    // value, the own store's number or 0).
    struct Held { uint64_t cycle; uint32_t value, own; uint64_t seen; };   // seen: when the cache must see it
    std::map<uint32_t, std::vector<Held>> hist;
    std::map<uint32_t, uint32_t> own_issued, own_done;
    auto remote_region = [&](uint32_t a) {
        for (uint32_t r = 0; r < nremote; ++r) if ((a & ~0xFFFu) == 0x80003000u + remote_pages[r]) return true;
        return false;
    };
    // A load looked up at `lookup`, after the core's `own`-th store to the word:
    // the value memory held at its lookup or later, and not before that store.
    auto allowed = [&](uint32_t a, uint64_t lookup, uint32_t own, uint32_t v) {
        const auto& h = hist[a];
        long start = -1;                          // -1: the initial value is still allowed
        for (size_t i = 0; i < h.size(); ++i) {
            if (h[i].seen <= lookup) start = std::max(start, long(i));
            if (own && h[i].own == own) start = std::max(start, long(i));
        }
        if (start < 0 && v == initial(a)) return true;
        for (size_t i = size_t(std::max(start, 0L)); i < h.size(); ++i) if (h[i].value == v) return true;
        return false;
    };
    size_t looked = 0;                            // answered-or-not requests already looked up
    struct Owed { uint32_t word; size_t index; long wait; };
    std::deque<Owed> snoops[L1D_SNOOPS];          // snoops owed to the cache on each port, in order
    uint64_t together = 0, refill_snooped[L1D_SNOOPS] = {};
    std::map<uint32_t, uint64_t> refilling;       // lines with a refill read accepted lately: when
    const bool lazy = (seed / 4) % 2 == 1;
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
        if (r < 70) a = 0x80000000u + pages[rng() % npages] * 4096 + (rng() % 6) * 16 + (rng() % 4) * 4;
        else if (r < 80) {                  // a word of the remote region: a load or a store
            pop = rng() % 5 < 2 ? 1 : 0; paddr = 0x80003000u + (nremote > 1 ? remote_pages[rng() % nremote] : 0) + (rng() % 6) * 16 + (rng() % 4) * 4; pdata = rng(); pbe = 0xf;
            if (pop == 1 && rng() % 2) { uint32_t b = rng() % 4; pbe = 1u << b; paddr += b; }
            pv = true;
            return;
        }
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
        // Other masters' writes this cycle (one a master, never two to one
        // 8-byte unit): performed now, each snoop owed on its master's port.
        std::vector<uint32_t> units;
        for (int port = 0; port < L1D_SNOOPS; ++port) {
            if (rng() % (L1D_SNOOPS == 1 ? 30 : 20) != 0) continue;
            const uint32_t a = 0x80003000u + (nremote > 1 ? remote_pages[rng() % nremote] : 0) + (rng() % 6) * 16 + (rng() % 4) * 4, v = rng();
            if (std::find(units.begin(), units.end(), a >> 3) != units.end()) continue;
            units.push_back(a >> 3);
            mem.wr(a, v, 0xf);
            hist[a].push_back({cyc, v, 0, ~uint64_t(0)});
            snoops[port].push_back({a, hist[a].size() - 1, lazy ? long(rng() % 16) : 0});
        }
        // A snoop now on each port: its oldest owed, when its wait is over or
        // the cache presents a memory-side request (its m_req_valid comes
        // from registers).
        uint32_t snoop_valid = 0, presented = 0;
        uint32_t lines[L1D_SNOOPS] = {};
        bool owed_any = false;
        for (int port = 0; port < L1D_SNOOPS; ++port) {
            auto& q = snoops[port];
            if (!q.empty() && (q.front().wait <= 0 || d.m_req_valid)) {
                snoop_valid |= 1u << port;
                lines[port] = q.front().word >> 4;
                hist[q.front().word][q.front().index].seen = cyc;
                for (auto [line, when] : refilling)
                    if (line == lines[port] && cyc - when < 8) ++refill_snooped[port];
                q.pop_front();
                ++presented;
            }
            for (auto& owed : q) --owed.wait;
            owed_any = owed_any || !q.empty();
        }
        if (presented >= 2) ++together;
        d.snoop_valid = snoop_valid;
#if L1D_SNOOPS == 1
        d.snoop_line = lines[0];
#else
        for (int w = 0; w < int((28 * L1D_SNOOPS + 31) / 32); ++w) d.snoop_line[w] = 0;
        for (int port = 0; port < L1D_SNOOPS; ++port)
            for (int b = 0; b < 28; ++b)
                if (lines[port] >> b & 1u) d.snoop_line[(28 * port + b) / 32] |= 1u << ((28 * port + b) % 32);
#endif
        d.m_req_ready = mready && !owed_any;      // no acceptance before the owed snoops
        d.eval();
        // checks this cycle
        if (acc_last) {
            if (bool(d.d_rsp_error) != err_last) { printf("FAIL seed %u cyc %llu: d_rsp_error %d expected %d\n", seed, (unsigned long long)cyc, d.d_rsp_error, err_last); return 1; }
        }
        if (d.d_rsp_valid) {
            if (expect.empty()) { printf("FAIL seed %u cyc %llu: unexpected response\n", seed, (unsigned long long)cyc); return 1; }
            Exp e = expect.front(); expect.pop_front(); ++answered;
            --looked;
            if (e.remote && e.op == 0 && !allowed(e.addr & ~3u, e.lookup, e.own, d.d_rsp_rdata)) {
                printf("FAIL seed %u cyc %llu: remote load %08x rdata %08x older than its lookup (cycle %llu)\n", seed,
                       (unsigned long long)cyc, e.addr, d.d_rsp_rdata, (unsigned long long)e.lookup);
                return 1;
            }
            if (e.check && d.d_rsp_rdata != e.data) {
                printf("FAIL seed %u cyc %llu: op %u addr %08x rdata %08x expected %08x\n", seed, (unsigned long long)cyc, e.op, e.addr, d.d_rsp_rdata, e.data);
                return 1;
            }
        }
        if (d.chk_lookup) {                       // the oldest request not yet looked up
            if (looked >= expect.size()) { printf("FAIL seed %u: a lookup with no request\n", seed); return 1; }
            expect[looked++].lookup = cyc;
        }
        bool accept = d.d_req_valid && d.d_req_ready;
        bool macc = d.m_req_valid && d.m_req_ready;
        if (macc && mq.size() - (mrsp ? 1 : 0) >= 2) {
            printf("FAIL seed %u cyc %llu: a third memory-side request in flight\n", seed, (unsigned long long)cyc);
            return 1;
        }
        if (accept) {
            if (expect.size() >= 2) { printf("FAIL: third in flight\n"); return 1; }
            bool err = !cacheable(paddr, bytes) && !io(paddr);
            Exp e{pop, paddr, 0, err, false, cyc};
            if (!err && remote_region(paddr)) {
                e.remote = true;                 // checked against the region's history
                if (pop == 1) { ++own_issued[paddr & ~3u]; mexp.push_back({pop, paddr, pdata, pbe}); }
                else if (kUncached) mexp.push_back({pop, paddr, pdata, pbe});   // (an access of its own, cache off)
                e.own = own_issued[paddr & ~3u];
            } else if (!err) {
                e.data = ref.perform(pop, paddr, pdata, pbe);
                e.check = pop != 1;
                if (!cacheable(paddr, bytes) || pop != 0 || kUncached) mexp.push_back({pop, paddr, pdata, pbe});
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
            bool refill = !kUncached && op == 0 && cacheable(a, bytes);
            if (!refill) {
                if (mexp.empty()) { printf("FAIL seed %u: unexpected mem access op %u %08x\n", seed, op, a); return 1; }
                Acc x = mexp.front(); mexp.pop_front();
                if (x.op != op || x.addr != a || x.be != d.m_req_be || (op != 0 && op != 2 && x.data != d.m_req_wdata)) {
                    printf("FAIL seed %u cyc %llu: mem access op %u %08x be %x, expected op %u %08x be %x\n", seed, (unsigned long long)cyc, op, a, d.m_req_be, x.op, x.addr, x.be); return 1;
                }
            } else if ((a & 3) || d.m_req_be != 0xf) { printf("FAIL: odd refill\n"); return 1; }
            if (refill) refilling[a >> 4] = cyc;
            uint32_t data = mem.perform(op, a, d.m_req_wdata, d.m_req_be);
            if (op == 1 && remote_region(a)) hist[a & ~3u].push_back({cyc, mem.rd(a), ++own_done[a & ~3u], cyc});
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
    if (L1D_SNOOPS > 1 && ncycles >= 1000000) {
        bool each = together > 0;
        for (int port = 0; port < L1D_SNOOPS; ++port) each = each && (kUncached || refill_snooped[port] > 0);
        if (!each) {
            printf("FAIL seed %u: coverage: %llu cycles with snoops on two ports or more; refills snooped by port:", seed,
                   (unsigned long long)together);
            for (int port = 0; port < L1D_SNOOPS; ++port) printf(" %llu", (unsigned long long)refill_snooped[port]);
            printf("\n");
            return 1;
        }
    }
    printf("PASS seed %u answered %llu snoops_together %llu refills_snooped", seed, (unsigned long long)answered,
           (unsigned long long)together);
    for (int port = 0; port < L1D_SNOOPS; ++port) printf(" %llu", (unsigned long long)refill_snooped[port]);
    printf("\n");
    return 0;
}
