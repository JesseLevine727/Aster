// Spike extension for Xasterdot8 (docs/cpu.md §2, §6; milestone 18.5), so
// programs using it run in lockstep: dot8 rd, rs1, rs2 — custom-0, funct3 0,
// funct7 0 (match 0x0000000b, mask 0xfe00707f, the v1 decode of
// rtl/core/aster_pcpi_dot8.sv) — writes the sum of the four products of the
// operands' signed bytes (lane i: bits 8i+7..8i), sign-extended. Every other
// custom-0 encoding stays illegal. Spike enables it from the ISA string, which
// also sets misa's X bit (as the core's misa does). Spike also assumes that a
// custom extension is a RoCC coprocessor, with an interrupt of its own (mie's
// bit 12, IRQ_COP, writable) and state that mstatus.XS tracks (XS writable,
// and SD with it); the Aster core has neither, so the extension installs mie
// and mstatus as Spike's own with those bits never written:
//
//   spike --extlib=libaster_dot8.so --isa=rv32ima_..._xasterdot8 ...
#include <riscv/csrs.h>
#include <riscv/decode.h>
#include <riscv/disasm.h>
#include <riscv/extension.h>
#include <riscv/processor.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace {
constexpr uint32_t kMatch = 0x0000000b, kMask = 0xfe00707f;

// The architectural value, with integer arithmetic only (no packed multiply).
uint32_t dot8(uint32_t a, uint32_t b) {
    int32_t sum = 0;
    for (int lane = 0; lane < 4; ++lane)
        sum += int32_t(int8_t(a >> (8 * lane))) * int32_t(int8_t(b >> (8 * lane)));
    return uint32_t(sum);
}

// As Spike's WRITE_RD: the logged form records the write for the commit log.
template <bool logged>
reg_t execute(processor_t* p, insn_t insn, reg_t pc) {
    state_t& state = *p->get_state();
    const reg_t value = reg_t(int64_t(int32_t(dot8(uint32_t(state.XPR[insn.rs1()]),
                                                   uint32_t(state.XPR[insn.rs2()])))));
    if (logged) state.log_reg_write[insn.rd() << 4] = {value, 0};
    state.XPR.write(insn.rd(), value);
    return pc + 4;
}

reg_t illegal(processor_t*, insn_t insn, reg_t) {
    throw trap_illegal_instruction(insn.bits() & 0xffffffffu);
}

struct xreg_t : public arg_t {
    explicit xreg_t(int field) : field(field) {}
    std::string to_string(insn_t insn) const override {
        return xpr_name[field == 0 ? insn.rd() : field == 1 ? insn.rs1() : insn.rs2()];
    }
    int field;
};
const xreg_t xrd(0), xrs1(1), xrs2(2);

// A CSR whose reads and writes go to Spike's own (which the rest of Spike —
// its interrupt logic, trap entry, mret — uses directly), with some bits never
// written: mie's coprocessor interrupt enable, mstatus's XS.
class without_coprocessor_t : public csr_t {
 public:
    without_coprocessor_t(processor_t* proc, csr_t_p inner, reg_t cleared)
        : csr_t(proc, inner->address), inner(std::move(inner)), cleared(cleared) {}
    reg_t read() const noexcept override { return inner->read(); }

 protected:
    bool unlogged_write(const reg_t value) noexcept override {
        inner->write((value & ~cleared) | (inner->read() & cleared));
        return true;
    }

 private:
    csr_t_p inner;
    reg_t cleared;
};
}  // namespace

class aster_dot8_t : public extension_t {
 public:
    const char* name() const override { return "asterdot8"; }

    std::vector<insn_desc_t> get_instructions(const processor_t&) override {
        return {{kMatch, kMask, execute<false>, illegal, illegal, illegal,
                 execute<true>, illegal, illegal, illegal}};
    }

    std::vector<csr_t_p> get_csrs(processor_t& proc) const override {
        state_t* state = proc.get_state();
        return {std::make_shared<without_coprocessor_t>(&proc, state->mie, reg_t(1) << IRQ_COP),
                // the CSR instructions' mstatus (on RV32, a view of the low half)
                std::make_shared<without_coprocessor_t>(&proc, state->csrmap.at(CSR_MSTATUS), reg_t(SSTATUS_XS))};
    }

    std::vector<disasm_insn_t*> get_disasms(const processor_t*) override {
        return {new disasm_insn_t("dot8", kMatch, kMask, {&xrd, &xrs1, &xrs2})};
    }
};

REGISTER_EXTENSION(asterdot8, []() { static aster_dot8_t extension; return &extension; })
