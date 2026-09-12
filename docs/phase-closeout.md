# Phase 1–4 closeout

The README roadmap is the acceptance contract. Completion requires evidence;
simulation, FPGA implementation and physical execution are recorded separately.
The earlier Phase 4 completion claim was premature. Phase 5 is out of scope.

| Phase | State | Evidence / remaining acceptance |
| --- | --- | --- |
| 1 | Verified in simulation | `make phase1-matrix`, `make check`; [verification details](verification.md) |
| 2 | Implementation present; closeout pending | Fix UART flow control, long serial regressions, clock/reset/constraint review, enforce implementation gates, rebuild, execute on physical PYNQ-Z1 |
| 3 | Initial benchmark; closeout pending | Strict parser; accurate retirement and common snapshot; counter tests; metadata; save/compare results; full serial delivery |
| 4 | Basic I/D caches; closeout pending | Randomized reference/invariant tests, stalled/reset cases, geometry matrix and all four README experiments |

## Phase 1 evidence (2026-09-12)

- Reference-generated RV32I arithmetic/immediate/branch and all RV32M cases:
  1,584 vector groups at seed `0xa57e`, plus load/store lanes, signed/unsigned
  extensions, LUI/AUIPC, JAL/JALR link values, x0 and FENCE.
- Explicit signed division, zero divisors, overflow, multiply high halves,
  shift masking and destination/source overlap cases; Python oracle fixtures
  check independently known corner values.
- Real 64 KiB memory boundaries, ignored ROM/unmapped stores, UART byte-lane
  behavior, RAM code execution and modification without stale I$ data.
- Runtime begins with `0xa5a5a5a5` RAM, checks initialized word/odd-byte data,
  zero BSS and recursive aligned stack use, then poisons globals and resets.
  The second boot proves reinitialization without RAM power-on initialization.
- Twelve trap cases each run across two resets: illegal, ECALL, EBREAK,
  misaligned LW/LH/SW/SH/JALR/JAL/taken branch, MMIO execution and unmapped
  execution. A completed UART marker must precede the expected trap; an early
  trap cannot pass. Trap must persist until reset.
- Host linker tests check aligned initialized-data ROM load addresses,
  the exact RAM/4 KiB stack boundary and ROM overflow rejection.
- `make phase1-matrix` runs the firmware scenarios for all combinations of
  `ENABLE_L1=0/1` and `SYNC_MEMORY=0/1`. These are implementation regressions,
  not an assertion of RISC-V architectural-suite certification.

Test environment: Verilator 5.020; RISC-V GCC 16.1.0 (`g6afcc4f6d`), RV32IM,
ILP32. Generated ELF/hex, vector includes and simulator objects remain in
`build/`; no prebuilt binary is needed by a fresh checkout.

## Physical workflow

The user requires SSH into PYNQ Linux, not JTAG programming. SSH access and a
Pynq-Z1 environment have been identified. This is connectivity evidence only;
it does not prove Aster has executed on the board. Use an isolated Aster output
directory and preserve other board projects. Record the loaded image, firmware,
configuration and actual board output when Phase 2 validation is performed.

Each completed phase receives its own commit-and-push checkpoint. Do not mark
the overall goal complete while any requirement above remains pending.
