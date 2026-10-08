// The Phase 20 DMA's cycle model (milestone 20.3; docs/soc.md §6, §10.5): on memories that take every
// request as it is offered and answer each exactly 2 + WAIT cycles after it (the fabric's contract),
// the cycle of every read's and write's acceptance and the job's length in cycles, counted from the
// cycle its START takes effect (cycle 0). The engine's rules, as soc.md §6 and aster_dma2.sv's header
// state them:
// - the source's units are read in order, the first offered in cycle 1; a read is offered while fewer
//   than two are in flight once that cycle's answer is in, and the read buffer (four units, counting
//   those in flight) has room;
// - a read's data can be taken from the cycle after its answer;
// - the funnel takes one step a cycle: it primes on the first unit when the source's offset in its unit
//   is above the destination's, else forms the next destination unit when the write buffer (two
//   entries) has room and the unit's last source unit is buffered (or it needs none);
// - a formed unit is offered in the next cycle (the write port is the buffer's head);
// - the job ends in the cycle its last write is answered, with every read answered.
#pragma once
#include <cstdint>
#include <deque>
#include <vector>

namespace dma {

struct Schedule {
    std::vector<std::uint64_t> read_accept, write_accept;   // each request's acceptance cycle
    std::uint64_t end = 0;                                  // the cycle the job ends in (JOB_CYCLES)
};

inline Schedule schedule(std::uint32_t src, std::uint32_t dst, std::uint32_t len, unsigned wait) {
    Schedule out;
    const unsigned hist = 2 + wait;
    const unsigned s = src & 7, d = dst & 7;
    const std::uint64_t ns = (std::uint64_t(len) + s + 7) / 8, nd = (std::uint64_t(len) + d + 7) / 8;
    const bool lag = s > d;
    std::uint64_t reads = 0, taken = 0, formed = 0, answered_writes = 0;
    std::vector<std::uint64_t> data_ready;                  // per read: the first cycle its data can be taken
    std::deque<std::uint64_t> wbuf;                          // formed units, the cycle each can be offered
    bool primed = !lag;
    std::uint64_t last_write_answer = 0;
    for (std::uint64_t c = 1;; ++c) {
        // in flight after this cycle's answer; outstanding: accepted, not yet taken
        std::uint64_t inflight = 0;
        for (std::uint64_t r = 0; r < reads; ++r) if (out.read_accept[r] + hist > c) ++inflight;
        const std::uint64_t outstanding = reads - taken;
        if (reads < ns && inflight < 2 && outstanding < 4) {
            out.read_accept.push_back(c);
            data_ready.push_back(c + hist + 1);
            ++reads;
        }
        // writes: the head is taken as it is offered
        if (!wbuf.empty() && wbuf.front() <= c) {
            out.write_accept.push_back(c);
            wbuf.pop_front();
        }
        // the funnel (the write buffer's room as it stood at the cycle's start)
        const std::size_t room = 2 - std::min<std::size_t>(2, wbuf.size() + (out.write_accept.size() &&
                                                                              out.write_accept.back() == c ? 1 : 0));
        if (!primed) {
            if (taken < reads && data_ready[taken] <= c) { ++taken; primed = true; }
        } else if (formed < nd && room > 0) {
            const bool need = taken < ns;
            if (!need || (taken < reads && data_ready[taken] <= c)) {
                if (need) ++taken;
                ++formed;
                wbuf.push_back(c + 1);
            }
        }
        // the end: the cycle the last write is answered
        if (formed == nd && wbuf.empty() && out.write_accept.size() == nd) {
            last_write_answer = out.write_accept.back() + hist;
            if (c >= last_write_answer) { out.end = last_write_answer; break; }
        }
        if (c > 100000000) break;
    }
    return out;
}

}  // namespace dma
