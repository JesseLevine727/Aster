// Test environment for the Phase 18 CPU shell and Spike lockstep.
// Programs start at _start (0x8000_0000), zero every register so the DUT and
// Spike (whose boot ROM leaves values in t0/a0/a1) begin from the same state,
// and report through the riscv-tests tohost convention: 1 = pass,
// (TESTNUM << 1) | 1 = fail.
//
// Built without Zicsr (PicoRV32, `-march=rv32im`) the environment touches no
// CSR. Built with it (the Aster core from 18.3; the compiler defines
// __riscv_zicsr) it also runs the machine-mode set-up the rv32mi tests expect,
// in place of upstream's (whose `env` submodule is not vendored):
// - minstret is zeroed, since Spike's boot ROM retires five instructions
//   first (docs/cpu.md §6);
// - mtvec is the test's `mtvec_handler` if it defines one (upstream's trap
//   vector jumps there; here the trap goes there directly, with no register
//   changed), else the environment's handler below, and mscratch points at the
//   handler's save area (in .bss, outside every test's signature region);
// - with ASTER_INTERRUPTS defined (the runner's --interrupts mode), MEIE and
//   MSIE and mstatus.MIE are set: interrupts from the shell's interrupt device
//   then arrive at random, and the handler clears them. Spike never raises
//   MEIP or MSIP, so the same program runs uninterrupted there (MTIE stays
//   off: Spike's CLINT holds MTIP high).
// The environment's handler preserves every register. It takes an interrupt
// (clearing the shell's lines with a store of 0 to its device, 0x3000_0000,
// and counting it), emulates a misaligned load or store (the core traps on
// them, as Spike does: rv32ui's `ma_data` runs through this), and fails the
// test with code 1337 on any other trap.
#ifndef ASTER_CORE_SHELL_ENV_H
#define ASTER_CORE_SHELL_ENV_H

#ifdef __riscv_zicsr
#include "encoding.h"
#endif

#define TESTNUM gp
#define RVTEST_RV32U
#define RVTEST_RV64U
#define RVTEST_RV32M
#define RVTEST_RV64M
#define RVTEST_RV32S
#define RVTEST_RV64S

#define ASTER_ZERO_XREGS \
    li x1, 0;  li x2, 0;  li x3, 0;  li x4, 0;  li x5, 0;  li x6, 0;  li x7, 0; \
    li x8, 0;  li x9, 0;  li x10, 0; li x11, 0; li x12, 0; li x13, 0; li x14, 0; \
    li x15, 0; li x16, 0; li x17, 0; li x18, 0; li x19, 0; li x20, 0; li x21, 0; \
    li x22, 0; li x23, 0; li x24, 0; li x25, 0; li x26, 0; li x27, 0; li x28, 0; \
    li x29, 0; li x30, 0; li x31, 0;

#define ASTER_REPORT(value_reg) \
    fence; la t6, tohost; sw value_reg, 0(t6); \
    1: j 1b;

#ifdef __riscv_zicsr

#define ASTER_IRQ_DEVICE 0x30000000
#define ASTER_SLOT(n) (4 * (n))
#define ASTER_IRQ_COUNT ASTER_SLOT(32)

#ifdef ASTER_INTERRUPTS
#define ASTER_ENABLE_INTERRUPTS \
    li t0, MIP_MEIP | MIP_MSIP; csrw mie, t0; csrsi mstatus, MSTATUS_MIE;
#else
#define ASTER_ENABLE_INTERRUPTS
#endif

// The environment's trap handler: sp is swapped with mscratch (the save area,
// slot n holding xn; slot 0 stays zero, so x0 reads as zero).
#define ASTER_TRAP_HANDLER \
    .align 2; \
aster_trap_handler: \
    csrrw sp, mscratch, sp; \
    sw t0, ASTER_SLOT(5)(sp); sw t1, ASTER_SLOT(6)(sp); \
    csrr t0, mcause; \
    bltz t0, aster_interrupt; \
    li t1, CAUSE_MISALIGNED_LOAD; beq t0, t1, aster_misaligned; \
    li t1, CAUSE_MISALIGNED_STORE; beq t0, t1, aster_misaligned; \
    li TESTNUM, 1337; \
    sll TESTNUM, TESTNUM, 1; or TESTNUM, TESTNUM, 1; ASTER_REPORT(TESTNUM) \
aster_interrupt: \
    li t0, ASTER_IRQ_DEVICE; sw zero, 0(t0); \
    lw t0, ASTER_IRQ_COUNT(sp); addi t0, t0, 1; sw t0, ASTER_IRQ_COUNT(sp); \
    lw t1, ASTER_SLOT(6)(sp); lw t0, ASTER_SLOT(5)(sp); \
    csrrw sp, mscratch, sp; \
    mret; \
aster_misaligned: \
    sw x1, ASTER_SLOT(1)(sp);   sw x3, ASTER_SLOT(3)(sp);   sw x4, ASTER_SLOT(4)(sp); \
    sw x7, ASTER_SLOT(7)(sp);   sw x8, ASTER_SLOT(8)(sp);   sw x9, ASTER_SLOT(9)(sp); \
    sw x10, ASTER_SLOT(10)(sp); sw x11, ASTER_SLOT(11)(sp); sw x12, ASTER_SLOT(12)(sp); \
    sw x13, ASTER_SLOT(13)(sp); sw x14, ASTER_SLOT(14)(sp); sw x15, ASTER_SLOT(15)(sp); \
    sw x16, ASTER_SLOT(16)(sp); sw x17, ASTER_SLOT(17)(sp); sw x18, ASTER_SLOT(18)(sp); \
    sw x19, ASTER_SLOT(19)(sp); sw x20, ASTER_SLOT(20)(sp); sw x21, ASTER_SLOT(21)(sp); \
    sw x22, ASTER_SLOT(22)(sp); sw x23, ASTER_SLOT(23)(sp); sw x24, ASTER_SLOT(24)(sp); \
    sw x25, ASTER_SLOT(25)(sp); sw x26, ASTER_SLOT(26)(sp); sw x27, ASTER_SLOT(27)(sp); \
    sw x28, ASTER_SLOT(28)(sp); sw x29, ASTER_SLOT(29)(sp); sw x30, ASTER_SLOT(30)(sp); \
    sw x31, ASTER_SLOT(31)(sp); \
    csrr t0, mscratch; sw t0, ASTER_SLOT(2)(sp); \
    csrr t0, mepc; lw t1, 0(t0);                  /* the instruction */ \
    csrr t0, mtval;                               /* the access's address */ \
    srli a1, t1, 12; andi a1, a1, 7;              /* funct3 */ \
    andi a2, t1, 0x20; bnez a2, 4f;               /* opcode 0x23: a store */ \
    lbu a3, 0(t0); lbu a4, 1(t0); slli a4, a4, 8; or a3, a3, a4; \
    andi a5, a1, 3; li a6, 1; beq a5, a6, 1f; \
    lbu a4, 2(t0); slli a4, a4, 16; or a3, a3, a4; \
    lbu a4, 3(t0); slli a4, a4, 24; or a3, a3, a4; \
    j 2f; \
1:  andi a5, a1, 4; bnez a5, 2f;                  /* lhu */ \
    slli a3, a3, 16; srai a3, a3, 16; \
2:  srli a5, t1, 7; andi a5, a5, 31; beqz a5, 5f; /* rd's slot (x0: none) */ \
    slli a5, a5, 2; add a5, a5, sp; sw a3, 0(a5); \
    j 5f; \
4:  srli a5, t1, 20; andi a5, a5, 31;             /* rs2's slot */ \
    slli a5, a5, 2; add a5, a5, sp; lw a3, 0(a5); \
    sb a3, 0(t0); srli a3, a3, 8; sb a3, 1(t0); \
    andi a5, a1, 3; li a6, 1; beq a5, a6, 5f; \
    srli a3, a3, 8; sb a3, 2(t0); srli a3, a3, 8; sb a3, 3(t0); \
5:  csrr t0, mepc; addi t0, t0, 4; csrw mepc, t0; \
    lw x1, ASTER_SLOT(1)(sp);   lw x3, ASTER_SLOT(3)(sp);   lw x4, ASTER_SLOT(4)(sp); \
    lw x6, ASTER_SLOT(6)(sp);   lw x7, ASTER_SLOT(7)(sp);   lw x8, ASTER_SLOT(8)(sp); \
    lw x9, ASTER_SLOT(9)(sp);   lw x10, ASTER_SLOT(10)(sp); lw x11, ASTER_SLOT(11)(sp); \
    lw x12, ASTER_SLOT(12)(sp); lw x13, ASTER_SLOT(13)(sp); lw x14, ASTER_SLOT(14)(sp); \
    lw x15, ASTER_SLOT(15)(sp); lw x16, ASTER_SLOT(16)(sp); lw x17, ASTER_SLOT(17)(sp); \
    lw x18, ASTER_SLOT(18)(sp); lw x19, ASTER_SLOT(19)(sp); lw x20, ASTER_SLOT(20)(sp); \
    lw x21, ASTER_SLOT(21)(sp); lw x22, ASTER_SLOT(22)(sp); lw x23, ASTER_SLOT(23)(sp); \
    lw x24, ASTER_SLOT(24)(sp); lw x25, ASTER_SLOT(25)(sp); lw x26, ASTER_SLOT(26)(sp); \
    lw x27, ASTER_SLOT(27)(sp); lw x28, ASTER_SLOT(28)(sp); lw x29, ASTER_SLOT(29)(sp); \
    lw x30, ASTER_SLOT(30)(sp); lw x31, ASTER_SLOT(31)(sp); \
    lw t0, ASTER_SLOT(2)(sp); csrw mscratch, t0;  /* the trapped sp (or an emulated load's) */ \
    lw t0, ASTER_SLOT(5)(sp); \
    csrrw sp, mscratch, sp; \
    mret;

#define ASTER_MACHINE_INIT \
    csrw minstret, zero; csrw minstreth, zero; \
    .weak mtvec_handler; \
    la t0, mtvec_handler; bnez t0, 1f; la t0, aster_trap_handler; \
1:  csrw mtvec, t0; \
    la t0, aster_trap_save; csrw mscratch, t0; \
    ASTER_ENABLE_INTERRUPTS \
    li t0, 0; \
    j aster_test_begin; \
    ASTER_TRAP_HANDLER \
    .pushsection .bss, "aw", @nobits; .align 4; \
aster_trap_save: .zero ASTER_SLOT(33); \
    .popsection; \
aster_test_begin:

#else
#define ASTER_MACHINE_INIT
#endif

#define RVTEST_CODE_BEGIN \
    .section .text.init, "ax", @progbits; \
    .align 2; .globl _start; _start: \
    ASTER_ZERO_XREGS \
    ASTER_MACHINE_INIT

#define RVTEST_CODE_END \
    unimp;

#define RVTEST_PASS \
    li TESTNUM, 1; ASTER_REPORT(TESTNUM)

// As upstream: a failure before any test case ran (TESTNUM still 0) never
// reports, so it cannot look like a pass; the shell times out instead.
#define RVTEST_FAIL \
    1: beqz TESTNUM, 1b; \
    sll TESTNUM, TESTNUM, 1; or TESTNUM, TESTNUM, 1; ASTER_REPORT(TESTNUM)

#define RVTEST_DATA_BEGIN \
    .pushsection .tohost, "aw", @progbits; \
    .align 6; .globl tohost; tohost: .dword 0; .size tohost, 8; \
    .align 6; .globl fromhost; fromhost: .dword 0; .size fromhost, 8; \
    .popsection; \
    .align 4; .globl begin_signature; begin_signature:

#define RVTEST_DATA_END \
    .align 4; .globl end_signature; end_signature:

#endif
