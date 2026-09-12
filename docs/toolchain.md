# Aster toolchain

## Required tools

The Phase 0 build is intentionally based on command-line tools:

- GNU Make;
- Verilator 5.x or newer;
- Python 3;
- `riscv32-unknown-elf-gcc`, `objcopy` and `objdump`.

The simulator targets do not require a vendor FPGA/ASIC installation. Vivado
2025.1 (or a compatible release) is required only for the PYNQ-Z1 bitstream
target; Yosys, OpenROAD and PYNQ programming tooling remain later-phase
dependencies.

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
make VIVADO=/path/to/vivado fpga
```

If the RISC-V toolchain is not on `PATH`, prepend its `bin` directory before
running the build. Do not commit generated toolchains or build output.

## Build targets

```text
make tools       Verify host and RISC-V tools
make structure   Print the tracked project layout
make firmware    Build software/boot/hello.c and runtime into build/software/
make smoke       Build and run the first Verilator unit smoke test
make directed    Run directed RV32IM instruction tests
make hello       Build firmware, compile the minimal SoC and run its UART test
make fpga-sim    Decode the board-facing 115200-baud UART in simulation
make fpga        Run the Vivado PYNQ-Z1 synthesis/place/route/bitstream flow
make check       Run tool checks, directed tests and simulations
make clean       Remove generated files under build/
```

The firmware pipeline is:

```text
start.S + hello.c + link.ld
        ↓ riscv32-unknown-elf-gcc
      hello.elf
        ↓ scripts/elf_to_hex.py (ELF load segments)
      hello.hex
        ↓ $readmemh
      aster_rom
```

The generated hex contains no address directives: line `N` is the little-endian
32-bit word at ROM address `4*N`. ELF load segments use their physical/load
address, so initialized `.data` can be copied from ROM into RAM by startup code
without creating a 256 MiB sparse ROM image.

## Toolchain policy

The architecture target and current bring-up configuration are RV32IM. The
PicoRV32 wrapper enables its internal multiply/divide implementations, and the
first firmware image executes both `mul` and `div` before printing its message.
Compressed instructions remain disabled so the ROM format and fetch path stay
32-bit word aligned.

Firmware uses `-nostdlib -nostartfiles -nodefaultlibs -ffreestanding`; every
runtime service is therefore explicit and reviewable. `start.S` establishes a
RAM stack, copies initialized data, clears `.bss`, and calls `main`. The first C
image writes directly to the UART MMIO register so the CPU/ROM/bus/RAM/
peripheral path is verified without depending on libc.
