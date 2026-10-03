# Pinned RISC-V architectural tests

Source: https://github.com/riscv-non-isa/riscv-arch-test
Release: `3.10.0`, commit `fc32e41d49480fd99ba0a192dfff9c3319b44873` (2024-11-05).
Imported unchanged on 2026-09-29 for Phase 18 (the Aster core). The licenses
(`COPYING.BSD`, `COPYING.APACHE`) are copied verbatim; each test names its own.

Included, byte for byte (`SHA256SUMS` lists every file):

- `riscv-test-suite/env/`: `arch_test.h`, `encoding.h`, `test_macros.h`;
- `riscv-test-suite/rv32i_m/`: `I` (39), `M` (8), `A` (9), `Zifencei` (1) and
  `privilege` (15) programs, `src/*.S`.

These are the 3.x signature tests: each program writes results into a
signature region, and `scripts/run_core_tests.py --arch` requires the DUT's
region to equal Spike's (`+signature`, granularity 4), in addition to lockstep
on every retired instruction. The model macros are owned by Aster
(`verification/core/arch_env/model_test.h`), as the test format intends. The
programs are assembled with `-mno-relax`: their `LA` macro aligns with RVC
enabled, and linker relaxation would otherwise leave a 2-byte `c.nop` that a
core without the C extension cannot execute. No test is patched.

3.10.0 and 4.x: release 4.1.0 (ACT4, September 2026) generates
self-checking ELFs from a UDB configuration with the Sail model, which needs
the ACT4 Python framework, Ruby/UDB and Sail 0.13.1. It adds a second,
independent reference model; the owner adopted it on 2 October 2026, once the
Aster core's configuration was final (after milestone 18.5). It is not
vendored (the checkout is 690 MB): it is built from the pinned tag outside the
repository, as docs/toolchain.md records, for the configuration in
verification/core/act4. 3.10.0's pre-generated programs stay, compared
against Spike in lockstep (docs/phase18.md).
