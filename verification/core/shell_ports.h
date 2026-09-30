// The two-port shell's memory response model (tb_core_ports.cpp), kept
// separate so it is unit-tested on its own (test_shell_ports.cpp): the
// pipelined, in-order answers of docs/cpu.md §5.
#ifndef ASTER_SHELL_PORTS_H
#define ASTER_SHELL_PORTS_H

#include <cstddef>
#include <cstdint>
#include <deque>

namespace shell {

// One port's response side: the responses owed, oldest first. `wait` counts
// the cycles until the response is driven; the head responds when it is 0.
struct Response {
    int wait;
    std::uint32_t data;                   // read data; stores and errors answer with garbage
    bool error;
};

struct Port {
    std::deque<Response> owed;
    bool responding() const { return !owed.empty() && owed.front().wait == 0; }
    std::size_t remaining() const { return owed.size() - (responding() ? 1 : 0); }
    // After an edge: the delivered response leaves, the others age by a cycle.
    void advance() {
        if (responding()) owed.pop_front();
        for (Response& r : owed) if (r.wait > 0) --r.wait;
    }
    // A request accepted at this edge is answered at least `latency` cycles
    // later. Only the head of the queue is ever answered, so answers stay in
    // acceptance order, one per cycle, whatever each one's own wait.
    void accept(int latency, int extra, std::uint32_t data, bool error) {
        owed.push_back({latency - 1 + extra, data, error});
    }
};

}  // namespace shell

#endif
