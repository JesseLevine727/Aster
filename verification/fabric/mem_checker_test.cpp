// The memory checker's self-tests (milestone 20.0): event streams a correct
// SoC produces must pass, and each planted fault must be rejected — a stale
// value (a load performed after a write, answered with the old value), a
// value never written, loads retired out of turn, a load never performed, a
// wrong byte under the mask — while bytes outside a load's mask are ignored.
#include "mem_checker.h"

#include <cstdio>
#include <functional>
#include <string>
#include <vector>

namespace {
constexpr std::uint32_t BASE = 0x80000000u;
fabric::Write store(std::uint32_t address, std::uint32_t value) {
    const bool high = address & 4u;
    return {1, address & ~7u, std::uint8_t(high ? 0xF0 : 0x0F), std::uint64_t(value) << (high ? 32 : 0)};
}
}  // namespace

int main() {
    struct Case { const char* name; bool must_pass; std::function<bool(fabric::MemoryChecker&)> run; };
    const std::vector<Case> cases = {
        {"a load after a write sees it", true, [](fabric::MemoryChecker& m) {
             m.write(store(BASE, 7)); m.performed(0, BASE, 2);
             return m.retired(0, BASE, 0xF, 7).empty(); }},
        {"a load performed before a write keeps the old value", true, [](fabric::MemoryChecker& m) {
             m.performed(1, BASE + 4, 1); m.write(store(BASE + 4, 9));
             return m.retired(1, BASE + 4, 0xF, 0).empty(); }},
        {"each hart in its own order", true, [](fabric::MemoryChecker& m) {
             m.write(store(BASE, 1)); m.performed(0, BASE, 1); m.performed(1, BASE, 1); m.write(store(BASE, 2));
             m.performed(0, BASE, 2);
             return m.retired(1, BASE, 0xF, 1).empty() && m.retired(0, BASE, 0xF, 1).empty()
                    && m.retired(0, BASE, 0xF, 2).empty() && m.idle(); }},
        {"bytes outside the mask are ignored", true, [](fabric::MemoryChecker& m) {
             m.write(store(BASE, 0x11223344)); m.performed(0, BASE, 1);
             return m.retired(0, BASE, 0x2, 0xFFFF33FF).empty(); }},
        {"a stale value", false, [](fabric::MemoryChecker& m) {
             m.write(store(BASE, 3)); m.performed(0, BASE, 5);
             return m.retired(0, BASE, 0xF, 0).empty(); }},
        {"a device load with its device's value", true, [](fabric::MemoryChecker& m) {
             m.performed_value(0, 0x20000010u, 0xabcd, 1);
             return m.retired(0, 0x20000010u, 0xF, 0xabcd).empty(); }},
        {"a reset drops the loads in flight", true, [](fabric::MemoryChecker& m) {
             m.performed(1, BASE, 1); m.drop(1); m.write(store(BASE, 5)); m.performed(1, BASE, 3);
             return m.retired(1, BASE, 0xF, 5).empty() && m.idle(); }},
        {"a device load with another value", false, [](fabric::MemoryChecker& m) {
             m.performed_value(0, 0x20000010u, 0xabcd, 1);
             return m.retired(0, 0x20000010u, 0xF, 0xabce).empty(); }},
        {"a value never written", false, [](fabric::MemoryChecker& m) {
             m.performed(0, BASE + 8, 1);
             return m.retired(0, BASE + 8, 0xF, 0xdead).empty(); }},
        {"loads retired out of turn", false, [](fabric::MemoryChecker& m) {
             m.performed(0, BASE, 1); m.performed(0, BASE + 4, 2);
             return m.retired(0, BASE + 4, 0xF, 0).empty() && m.retired(0, BASE, 0xF, 0).empty(); }},
        {"a load never performed", false, [](fabric::MemoryChecker& m) {
             return m.retired(1, BASE, 0xF, 0).empty(); }},
        {"a wrong byte under the mask", false, [](fabric::MemoryChecker& m) {
             m.write(store(BASE, 0x11223344)); m.performed(0, BASE, 1);
             return m.retired(0, BASE, 0x4, 0x11003344).empty(); }},
    };
    int bad = 0, rejected = 0, accepted = 0;
    for (const Case& c : cases) {
        fabric::MemoryChecker m(BASE, 4096);
        const bool passed = c.run(m);
        if (passed != c.must_pass) {
            std::printf("FAIL: memory checker self-test '%s': %s\n", c.name, passed ? "accepted" : "rejected");
            ++bad;
        }
        (c.must_pass ? accepted : rejected) += passed == c.must_pass;
    }
    if (bad) return 1;
    std::printf("PASS: the memory checker's self-tests: %d correct streams accepted, %d planted faults rejected "
                "(stale value, value never written, out of turn, never performed, wrong byte, wrong device value)\n", accepted, rejected);
    return 0;
}
