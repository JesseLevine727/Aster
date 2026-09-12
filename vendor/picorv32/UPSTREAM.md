# PicoRV32 dependency

Aster vendors the upstream `picorv32.v` source so a fresh checkout does not
depend on a mutable external branch during simulation, synthesis, or future
ASIC builds.

- Upstream: https://github.com/YosysHQ/picorv32
- Pinned commit: `ef203c2b0a3fb793280f5114941416c425c5b461`
- Pinned revision date: 2026-09-07
- License: ISC; see [`COPYING`](COPYING)

Do not edit `picorv32.v` in the Aster tree. Integration belongs in
`rtl/core/aster_picorv32.sv` and Aster-owned adapters/tests.
