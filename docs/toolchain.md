# Aster toolchain

## Required tools

- **GNU Make and Python 3** (3.12 used).
- **A 32-bit RISC-V bare-metal GCC**, `riscv32-unknown-elf-gcc` with its binutils. The evidence records GCC 16.1.0.
  If it is not on `PATH`, pass `RISCV_PREFIX=/path/to/riscv32-unknown-elf-`.
- **Verilator 5.x.** The evidence records 5.020. To use another binary, pass `VERILATOR=/path/to/verilator`.
- **Vivado 2025.1**, for the PYNQ-Z1 bitstreams and the out-of-context timing runs only. To use another binary, pass
  `VIVADO=/path/to/vivado`.
- **Spike and its plugins,** for the CPU lockstep (below).

`make tools` checks the compilers, Verilator and Spike (`scripts/check_tools.sh`); Vivado, the ACT4 tools and matplotlib (for `scripts/make_figures.py`) are found when used.

The evidence bundles record each tool's identity: its path, version and hash. That is what their builds were made
with. It is not a claim that another release reproduces their exact binaries or timing.

## CPU reference model (Phase 18)

**Spike.** Phase 18 checks the Aster core in lockstep with **Spike** (`riscv-isa-sim`).
- **Version:** built from upstream commit `0bff12123b1fd510e19e19634dd997dbade70e54` (2026-09-28).
- **Build:** into `~/tools/spike` (`../configure --prefix=$HOME/tools/spike && make && make install`, then
  `git rev-parse HEAD > ~/tools/spike/SPIKE_COMMIT`). Override its path with `SPIKE`.
- **dtc:** Spike runs the device-tree compiler `dtc` at start-up; this host uses the DTC 1.6.1 shipped with Vivado
  on `PATH`.
- **Checked by `make tools`:** both are present, and `SPIKE_COMMIT` names the pinned commit.

The CPU shell, the lockstep comparator and the test runners are described in [`phase18.md`](phase18.md).

**Plugins.** The CPU kernels run in Spike with an MMIO plugin, `aster_clock` (`verification/core/spike/aster_clock.cc`),
and DOT8 with `aster_dot8`. Both build against the installed Spike headers (`SPIKE_ROOT`, default `~/tools/spike`).

**Conformance programs.** They are vendored with checksums:
- `riscv-tests` at `2ebecad` (`vendor/riscv-tests`);
- `riscv-arch-test` 3.10.0 (`vendor/riscv-arch-test`). These programs are built with `-mno-relax` (see
  `scripts/run_core_tests.py`).

**ACT4** (riscv-arch-test 4.x; `make core-aster-act4`, in `make check`) builds self-checking programs for the Aster
core's configuration (`verification/core/act4/aster-rv32ima`), with expected results from the **Sail** model. Like
Spike's source, it lives outside the repository under `~/tools`; override each location with the variable named:
- **`ACT4_ROOT`:** `riscv-arch-test` tag `4.1.0`, commit `6e8a45123f14cebfb3df151a0e7b849b4389b33b`, cloned into
  `~/tools/src/riscv-arch-test-4.1.0` (`git clone --depth 1 --branch 4.1.0`). The build leaves the checkout
  unmodified.
- **`SAIL_RISCV`:** the Sail RISC-V model **0.13.1**, the release binary `sail-riscv-Linux-x86_64.tar.gz`
  (SHA-256 `ee052f64…2f9e9469`), unpacked into `~/tools/sail-riscv-0.13.1`.
- **`ACT4_VENV`:** a Python 3.12 virtual environment at `~/tools/act4-venv` with the checkout's packages:
  - installed by `pip install -e ./framework -e ./generators/testgen -e ./generators/coverage`;
  - giving act 0.3.0, testgen 0.5.0 and covergroupgen 0.2.0.
- **`ACT4_BIN`:** the UDB tools.
  - They are Ruby 3.3 gems installed by Bundler from the framework's own `Gemfile.lock`: udb 0.1.16, udb-gen 0.1.15,
    udb_helpers 0.1.3, idlc 0.1.7.
  - Bundler binstubs for `udb` and `udb-gen` go in `~/tools/act4-bin`
    (`BUNDLE_GEMFILE=<checkout>/framework/src/act/data/Gemfile BUNDLE_BIN=~/tools/act4-bin bundle binstubs udb udb-gen`).
    The gems install no `udb` executable of their own.

The framework's builds go to `build/act4` (about 130 MB; it rebuilds only what changed).

## The board

The PYNQ-Z1 runs PYNQ Linux on its ARM cores. The board scripts (`scripts/aster_board.py`,
`scripts/matrix_board.py` with `scripts/aster_board_remote.py`) work as follows:
- they copy the programs and the bitstream over ssh;
- they program the FPGA through the Linux FPGA manager;
- they run each program through `/dev/mem`.

The board's sudo password is read from the environment variable `ASTER_BOARD_SUDO` and given to sudo on stdin only.
It is never stored or logged.

## Firmware conventions

The harts implement RV32IMA with Zicsr, Zifencei and the DOT8 custom instruction. Compressed instructions are not
supported. The firmware is built freestanding:
- the flags are `-nostdlib -nostartfiles -nodefaultlibs -ffreestanding`;
- every runtime service is explicit, in `software/runtime`;
- the start-up code (`start_aster.S`, and `start_multicore_aster.S` for two harts) sets up the stacks, copies the
  initialized data, clears `.bss` and calls `main`.
