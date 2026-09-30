// Spike MMIO plugin for the Phase 18 CPU shell: the SoC performance-counter
// page (0x2000_3000, 4 KiB) with a deterministic stand-in for its cycle
// counter, so the CPU kernels' timers (CoreMark, Dhrystone) run identically in
// Spike and in the shells (verification/core/shell_common.h implements the same
// rule). Each 32-bit load of the cycle counter's low word (page offset 0)
// returns the previous value plus kStep, starting at kStep; the high word
// (offset 4) reads 0; every other word of the page is plain memory.
//
//   spike --extlib=libaster_clock.so --device=aster_clock,0x20003000 ...
#include <riscv/abstract_device.h>
#include <riscv/sim.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace {
constexpr reg_t kPageBytes = 0x1000;
constexpr uint32_t kStep = 1000000;
}  // namespace

class aster_clock_t : public abstract_device_t {
 public:
  aster_clock_t() : page(kPageBytes, 0) {}

  bool load(reg_t addr, size_t len, uint8_t* bytes) override {
    if (addr + len > kPageBytes) return false;
    if (addr == 0 && len == 4) {
      counter += kStep;
      std::memcpy(bytes, &counter, 4);
      return true;
    }
    if (addr == 4 && len == 4) {
      std::memset(bytes, 0, 4);
      return true;
    }
    std::memcpy(bytes, &page[addr], len);
    return true;
  }

  bool store(reg_t addr, size_t len, const uint8_t* bytes) override {
    if (addr + len > kPageBytes) return false;
    std::memcpy(&page[addr], bytes, len);
    return true;
  }

  reg_t size() override { return kPageBytes; }

 private:
  std::vector<uint8_t> page;
  uint32_t counter = 0;
};

static aster_clock_t* aster_clock_parse_from_fdt(const void*, const sim_t*, reg_t* base,
                                                 const std::vector<std::string>& args) {
  *base = args.empty() ? 0x20003000 : std::stoull(args[0], nullptr, 0);
  return new aster_clock_t();
}

static std::string aster_clock_generate_dts(const sim_t*, const std::vector<std::string>&) {
  return "";
}

REGISTER_DEVICE(aster_clock, aster_clock_parse_from_fdt, aster_clock_generate_dts)
