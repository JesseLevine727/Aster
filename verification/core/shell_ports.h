// The two-port shell's memory response model and request-protocol checks
// (tb_core_ports.cpp), kept separate so they are unit-tested on their own
// (test_shell_ports.cpp): the pipelined, in-order answers of docs/cpu.md §5,
// and the rules a core's requests must follow there.
#ifndef ASTER_SHELL_PORTS_H
#define ASTER_SHELL_PORTS_H

#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>

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

// One cycle's request on a port, as the core presents it before the edge.
struct Request {
    bool valid;
    std::uint32_t addr;             // the instruction port's word address, or the data port's byte address
    int op;                         // data port: 0 load, 1 store, others atomics; 0 on the instruction port
    std::uint32_t wdata;
    std::uint32_t be;
};

// docs/cpu.md §4-§5: a request presented and not accepted stays stable until
// it is accepted, except that the core may withdraw or replace it in a cycle
// it marks as excused (a redirect on the instruction port, a pipeline flush
// on the data port). Write data is compared only for operations that write
// (op != 0; when 18.4 adds `lr`, whose write data is don't-care, it joins the
// loads here). Call once per cycle with the request and the memory's readiness;
// returns "" or what was violated.
struct StableCheck {
    bool waiting = false;           // last cycle's request was presented and not accepted
    Request held{};

    std::string cycle(const Request& request, bool ready, bool excused) {
        std::string violation;
        if (waiting && !excused) {
            if (!request.valid) violation = "withdrawn while waiting";
            else if (request.addr != held.addr || request.op != held.op || request.be != held.be ||
                     (held.op != 0 && request.wdata != held.wdata))
                violation = "changed while waiting";
        }
        waiting = request.valid && !ready;
        held = request;
        return violation;
    }
};

// A data request's shape (docs/cpu.md §4-§5): the byte enables are one
// naturally aligned byte, halfword or word, and the address is the word's
// (low bits 0) or the access's own byte address (low bits naming the lowest
// enabled byte); the memory uses the word address and the enables.
inline bool well_formed(std::uint32_t addr, std::uint32_t be) {
    std::uint32_t lowest;
    switch (be) {
        case 0x1: case 0x3: case 0xf: lowest = 0; break;
        case 0x2: lowest = 1; break;
        case 0x4: case 0xc: lowest = 2; break;
        case 0x8: lowest = 3; break;
        default: return false;
    }
    return (addr & 3u) == 0 || (addr & 3u) == lowest;
}

}  // namespace shell

#endif
