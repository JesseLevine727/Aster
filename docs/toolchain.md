# Aster toolchain

## Required tools

The Phase 0 build is intentionally based on command-line tools:

- GNU Make;
- Verilator 5.x or newer;
- Python 3;
- `riscv32-unknown-elf-gcc`, `objcopy` and `objdump`.

The checked-in scripts do not require a Python package or a vendor FPGA/ASIC
installation. Yosys, OpenROAD, Vivado and PYNQ tooling are later-phase
dependencies and are not required for the first simulator milestone.

Run the repository check with:

```sh
make tools
```

The current development environment uses a 32-bit RISC-V bare-metal GCC
toolchain and Verilator. The Makefile accepts overrides when tools are in a
non-standard location:

```sh
make RISCV_PREFIX=/path/to/riscv32-unknown-elf- hello
make VERILATOR=/path/to/verilator smoke
```

If the RISC-V toolchain is not on `PATH`, prepend its `bin` directory before
running the build. Do not commit generated toolchains or build output.

## Build targets

```text
make tools       Verify host and RISC-V tools
make structure   Print the tracked project layout
make firmware    Build software/boot/hello.S into build/software/
make smoke       Build and run the first Verilator unit smoke test
make hello       Build firmware, compile the minimal SoC and run its UART test
make check       Run all Phase 0 checks
make clean       Remove generated files under build/
```

The firmware pipeline is:

```text
hello.S + link.ld
        ↓ riscv32-unknown-elf-gcc
      hello.elf
        ↓ objcopy -O binary
      hello.bin
        ↓ scripts/elf_to_hex.py
      hello.hex
        ↓ $readmemh
      aster_rom
```

The generated hex contains no address directives: line `N` is the little-endian
32-bit word at ROM address `4*N`. This makes it unambiguous in both Verilator
and future ROM initialization flows.

## Toolchain policy

The architecture target is RV32IM, but the first checked-in bring-up core and
firmware use RV32I. The M extension is a planned implementation milestone and
must not be enabled in firmware until the core and its tests support it.

Firmware uses `-nostdlib -nostartfiles -nodefaultlibs -ffreestanding`; every
runtime service is therefore explicit and reviewable. The first image writes
directly to the UART MMIO register so the CPU/ROM/bus/peripheral path is
verified without depending on libc.
