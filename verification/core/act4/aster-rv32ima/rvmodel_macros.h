// rvmodel_macros.h — ACT4 macros for the Aster core in the Phase 18 CPU shell
// (verification/core/tb_core_ports.cpp; docs/phase18.md, "ACT4").
// - Halting: the riscv-tests tohost convention the shell watches (1 passes;
//   3 fails, which the shell reports as test 1).
// - Console: the shell's UART TX word at 0x2000_0000 (+io_page, +console),
//   where a failing test prints its diagnostics.
// - Interrupts: the shell's interrupt device (+irq_device), a word at
//   0x3000_0000 whose load returns the interrupt lines (MEIP bit 11, MSIP
//   bit 3) and whose store sets them (after a delay in bits 31:16, here 0),
//   so a line is set or cleared by a read-modify-write.
// - Timer: the shell's machine timer (+timer), in the CLINT layout Sail uses,
//   drives the core's MTIP pin and its mtime input (which time/timeh read), as
//   a SoC's timer would; for ACT4 mtime counts once per 8 cycles
//   (+timer_divider, scripts/run_core_tests.py), slower than the core as a
//   platform timer is: the timer tests allow fewer ticks than misses and
//   back-pressure could otherwise take.
// - Access faults: 0x4000_0000 is outside the shell's memory (and outside
//   Sail's regions), so loads, stores and fetches there fault.
#ifndef _RVMODEL_MACROS_H
#define _RVMODEL_MACROS_H

#define STANDARD_SM_SUPPORTED

##### DATA #####
// As the Sail build's (tests/env/sail_macros.h), so both place it alike.
#define RVMODEL_DATA_SECTION \
        .pushsection .tohost,"aw",@progbits;                \
        .balign 8; .global tohost; tohost: .dword 0;         \
        .balign 8; .global fromhost; fromhost: .dword 0;     \
        .popsection

##### STARTUP #####
// The core starts in machine mode at 0x8000_0000 (TEST_BASE): nothing to do.

#define RVMODEL_ACCESS_FAULT_ADDRESS 0x40000000

##### TERMINATION #####
// One store, then a spin: the shell stops at the store's retirement and
// requires every accepted write to have retired by then.
#define RVMODEL_HALT_PASS  \
  li t1, 1                ;\
  la t0, tohost           ;\
  sw t1, 0(t0)            ;\
  aster_halt_pass: j aster_halt_pass ;

#define RVMODEL_HALT_FAIL \
  li t1, 3                ;\
  la t0, tohost           ;\
  sw t1, 0(t0)            ;\
  aster_halt_fail: j aster_halt_fail ;

##### IO #####
#define RVMODEL_IO_INIT(_R1, _R2, _R3)
#define RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR_PTR) \
1:                              ; \
  lbu  _R1, 0(_STR_PTR)         ; \
  beqz _R1, 2f                  ; \
  li   _R2, 0x20000000          ; \
  sb   _R1, 0(_R2)              ; \
  addi _STR_PTR, _STR_PTR, 1    ; \
  j 1b                          ; \
2:

##### Interrupt latency #####
// Cycles from the device store to the interrupt: the line at the next edge,
// the core's registered input a cycle later, taken with an instruction in M1.
#define RVMODEL_INTERRUPT_LATENCY 20

##### Machine timer #####
#define RVMODEL_MTIME_ADDRESS    0x0200BFF8
#define RVMODEL_MTIMECMP_ADDRESS 0x02004000
#define RVMODEL_MAX_CYCLES_PER_TIMER_TICK 8
#define RVMODEL_TIMER_INT_SOON_DELAY 100

##### Machine interrupts #####
// (bit 11 does not fit an immediate, so it is built in _R2)
#define RVMODEL_SET_MEXT_INT(_R1, _R2) \
  li _R2, 0x30000000 ; lw _R1, 0(_R2) ; li _R2, 0x800 ; or _R1, _R1, _R2 ; \
  li _R2, 0x30000000 ; sw _R1, 0(_R2)
#define RVMODEL_CLR_MEXT_INT(_R1, _R2) \
  li _R2, 0x30000000 ; lw _R1, 0(_R2) ; li _R2, ~0x800 ; and _R1, _R1, _R2 ; \
  li _R2, 0x30000000 ; sw _R1, 0(_R2)
#define RVMODEL_SET_MSW_INT(_R1, _R2) \
  li _R2, 0x30000000 ; lw _R1, 0(_R2) ; ori _R1, _R1, 0x8 ; sw _R1, 0(_R2)
#define RVMODEL_CLR_MSW_INT(_R1, _R2) \
  li _R2, 0x30000000 ; lw _R1, 0(_R2) ; andi _R1, _R1, -9 ; sw _R1, 0(_R2)

##### Supervisor interrupts (no supervisor mode) #####
#define RVMODEL_SET_SEXT_INT(_R1, _R2)
#define RVMODEL_CLR_SEXT_INT(_R1, _R2)
#define RVMODEL_SET_SSW_INT(_R1, _R2)
#define RVMODEL_CLR_SSW_INT(_R1, _R2)

#endif // _RVMODEL_MACROS_H
