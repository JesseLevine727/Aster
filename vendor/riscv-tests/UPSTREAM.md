# Pinned public RV32A reference programs

Source: https://github.com/riscv-software-src/riscv-tests
Revision: `2ebecad997fa58cd9e5724340ba75aa4b59bd1d0`.
Imported unchanged on 2026-09-12. The included Regents license is retained
verbatim in `LICENSE`; this is not a modification of the PicoRV32 vendor.

The 22 upstream files in `SHA256SUMS` comprise the license, scalar macro header,
ten RV32UA wrappers and their ten RV64UA word-operation bodies. RV32 wrappers
select XLEN=32 exactly as upstream does. All nine word AMOs and `lrsc` are
included. No test instruction, expected value, branch or disabled-case decision
is patched. The upstream LR/SC program itself restricts its active body to one
core; Aster runs that same body separately on each physical hart. It is not
presented as an upstream two-core contention test.

The owned `software/tests/riscv_reference/` environment initializes real shared
RAM, selects an actual core, and reports results in its protected private RAM.
It replaces only the platform startup/pass/fail transport macros, without
privileged CSR setup or an OS. `run_riscv_reference.py` resolves original test
case PCs from ELF; the RTL harness checks each case retires once on the selected
hart, actual A instructions execute, the final signature agrees, and a warm
stop retains all architectural RAM stores. This is a reference regression,
not RISC-V architectural certification or a privileged-ISA claim.

The owned negative-check helper changes a generated ROM instruction (the first
AMO test's expected value), verifies that the original failure path is taken,
and requires the harness to reject it. It never edits these vendored files.
