// Unit test of the two-port shell's memory response model (shell_ports.h),
// driven the way tb_core_ports.cpp drives it: in each cycle the head response
// is visible and ready is decided, then at the clock edge the delivered
// response leaves and a new request may be accepted. A requester that keeps a
// request presented every cycle (as the Aster core's fetch unit does) must get,
// for every configuration: answers in acceptance order, at most one per cycle,
// none earlier than `latency` edges after acceptance, never more than
// `max_inflight` requests outstanding, and — with no extra delay and two in
// flight — one request accepted and answered per cycle.
#include "shell_ports.h"

#include <cstdio>
#include <random>
#include <vector>

namespace {
int failures = 0;

void expect(bool condition, const char* what, int latency, int inflight, int seed) {
    if (!condition) {
        std::printf("FAIL: %s (latency %d, max_inflight %d, seed %d)\n", what, latency, inflight, seed);
        ++failures;
    }
}

// Returns the cycle count to answer `count` requests.
int run(int latency, std::size_t max_inflight, int seed, int count) {
    shell::Port port;
    std::mt19937 rng(seed);
    std::vector<int> accepted_at(count, -1), answered_in(count, -1);
    int issued = 0, answered = 0, cycle = 0;
    while (answered < count && cycle < 100000) {
        // Low phase of cycle `cycle` (after edge `cycle`).
        if (port.responding()) {
            const int id = int(port.owed.front().data);
            expect(id == answered, "answers in acceptance order", latency, int(max_inflight), seed);
            answered_in[id] = cycle;
            ++answered;
        }
        expect(port.owed.size() <= max_inflight, "never more than max_inflight outstanding",
               latency, int(max_inflight), seed);
        const bool ready = port.remaining() < max_inflight;
        // Edge `cycle + 1`.
        port.advance();
        if (ready && issued < count) {
            port.accept(latency, seed ? int(rng() % 3) : 0, std::uint32_t(issued), false);
            accepted_at[issued++] = cycle + 1;
        }
        ++cycle;
    }
    expect(answered == count, "every request answered", latency, int(max_inflight), seed);
    for (int i = 0; i < count; ++i) {
        // Visible in the cycle after edge answered_in[i], so sampled at edge answered_in[i] + 1.
        expect(answered_in[i] + 1 >= accepted_at[i] + latency, "no answer earlier than the latency",
               latency, int(max_inflight), seed);
    }
    return cycle;
}
}  // namespace

int main() {
    const int count = 1000;
    for (int latency : {1, 2}) {
        for (std::size_t inflight : {std::size_t(1), std::size_t(2), std::size_t(3)}) {
            for (int seed = 0; seed < 20; ++seed) run(latency, inflight, seed, count);
        }
    }
    // Full rate: two in flight answer a request every cycle at latency 2 (and
    // one in flight at latency 1); one in flight at latency 2 halves the rate.
    const int two_two = run(2, 2, 0, count), one_one = run(1, 1, 0, count), one_two = run(2, 1, 0, count);
    expect(two_two <= count + 3, "two in flight sustain one answer per cycle at latency 2", 2, 2, 0);
    expect(one_one <= count + 2, "one in flight sustains one answer per cycle at latency 1", 1, 1, 0);
    expect(one_two >= 2 * count - 2, "one in flight at latency 2 answers every other cycle", 2, 1, 0);
    std::printf("%s: shell port model — %d configurations x 20 seeds, full rate %d cycles for %d requests\n",
                failures ? "FAIL" : "PASS", 6, two_two, count);
    return failures ? 1 : 0;
}
