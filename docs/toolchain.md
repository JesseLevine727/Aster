# Aster toolchain

## Required tools

The Phase 0 build is intentionally based on command-line tools:

- GNU Make;
- Verilator 5.x or newer;
- Python 3;
- `riscv32-unknown-elf-gcc`, `objcopy` and `objdump`.

The simulator targets do not require a vendor FPGA/ASIC installation. Vivado
2025.1 (or a compatible release) is required only for the PYNQ-Z1 bitstream
targets. PYNQ Python/XRT is needed on the board for Linux loading. Yosys and
OpenROAD remain later ASIC-phase dependencies.
The Linux FPGA build also uses Vivado's `xvlog`, `xelab` and `xsim` to test the
generated reset netlist; it finds them through `XILINX_VIVADO` or `PATH`.

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
make phase1      Run directed ISA, runtime, memory-map, trap and host tests
make phase1-matrix  Cross ENABLE_L1=0/1 with SYNC_MEMORY=0/1
make hello       Build firmware, compile the minimal SoC and run its UART test
make bench       Run AsterBench memcpy or a sequential/random pointer walk
make cache       Run directed and seeded reference-model L1 tests
make cache-matrix  Run 24 geometries, three seeds each
make cache-boundaries  Run 12 larger/boundary geometries, three seeds each
make phase4-soc-matrix  Run 24 cache/geometry/memory-latency configurations
make fpga-sim    Decode the board-facing 115200-baud UART in simulation
make fpga        Run the Vivado PYNQ-Z1 synthesis/place/route/bitstream flow
make linux-sim   Test AXI boot loading, host pauses and FPGA UART serial capture
make fpga-linux  Build the PYNQ Linux PCAP/AXI overlay (no JTAG)
make uart        Test TX backpressure/reset and RX framing/glitch handling
make retirement  Check an exact RVFI retirement sequence and trap exclusion
make counters    Test common counter windows, event semantics and rollover
make arbiter     Run directed/seeded two-requester arbitration scoreboards
make fabric-matrix  Test shared decode/control for 1/2 harts and four memory timings
make multicore-runtime  Run the Phase 5 C runtime with real per-hart retirement
make multicore-runtime-matrix  Cross 1/2 harts, caches off/on and four memory timings
make parallel    Run split-array AsterBench v3 and independent RTL scoreboards
make parallel-matrix  Cross 1/2 cores/workers, caches and four memory timings
make parallel-workloads  Test odd/boundary sizes, seeds and round counts
make check       Run tool checks, directed tests and simulations
make clean       Remove generated files under build/
```

Both Verilator and Vivado define `RISCV_FORMAL` to expose the pinned core's
synthesizable RVFI observation ports. They do not define `FORMAL`. Firmware is
still RV32IM/ILP32; the extra interface changes measurement, not the ISA.

Phase 5 simulation uses `HART_COUNT=1|2` (default 2) and the existing cache and
memory knobs. Legacy single-core targets ignore `HART_COUNT`; they retain their
original map. The multicore linker/runtime and simulation top are separate,
and no dual-core FPGA target or physical closeout is claimed yet.

`PARALLEL_WORDS=2..1024`, `PARALLEL_ROUNDS=1..64`, `PARALLEL_JOBS=1..16`,
`PARALLEL_WORKERS=1|2` and `PARALLEL_SEED` configure the new workload. The default
is 64 words, four rounds, three jobs and one worker per instantiated hart.
For an apples-to-apples same-hardware comparison use `HART_COUNT=2` in both
runs and vary `PARALLEL_WORKERS` only. One-worker firmware holds hart 1 reset.
Legacy `BENCH_*` flags continue to apply only to v2 workloads.

```sh
python3 scripts/parallel_results.py capture --workers 1 --output build/parallel/one.json
python3 scripts/parallel_results.py capture --workers 2 --output build/parallel/two.json
python3 scripts/parallel_results.py compare build/parallel/one.json build/parallel/two.json
python3 scripts/parallel_results.py audit build/parallel/two.json
```

Captures contain both warm boots, every raw job record, independent per-hart
kernel observations, the hash-bound raw build/run log and source/toolchain/
firmware/model provenance. Default capture builds are fresh temporary roots;
existing outputs/logs are rejected. `parallel-config` exposes actual build
paths and flags. `parallel-firmware` builds only the ELF/hex for deployment.

Save and compare real benchmark runs with complete source/toolchain hashes:

```sh
python3 scripts/bench_results.py capture --l1 0 --output build/results/no-cache.json
python3 scripts/bench_results.py capture --l1 1 --output build/results/cache.json
python3 scripts/bench_results.py compare build/results/no-cache.json build/results/cache.json
```

Each capture uses a fresh temporary build directory to avoid stale flags or
images, validates the record and requested RTL settings, and retains a build
log alongside the JSON. Existing output paths are never silently overwritten.
See [`software/benchmarks/README.md`](../software/benchmarks/README.md).

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

AsterBench uses the same startup/runtime and linker contract as the Hello
image, but emits a fixed-width CSV-like record containing comparable counter
fields. `make -s bench-config` prints the exact artifact paths. The default
image is `build/software/bench_memcpy_w64_r4_s0x13570000/benchmark.hex`.
Firmware paths encode workload/size/repetitions/seed, and generic model paths
encode cache enable, synchronous memory, wait cycles and cache geometry.
The generic model loads each firmware with `+rom=...`, allowing an isolated
experiment batch to reuse a model without embedding the wrong ROM.

```sh
make ENABLE_L1=1 SYNC_MEMORY=1 MEMORY_WAIT_CYCLES=4 \
  L1_LINE_WORDS=8 L1_LINE_COUNT=32 \
  BENCH_WORKLOAD=walk_random BENCH_WORDS=1024 BENCH_REPETITIONS=8 BENCH_SEED=0 bench
python3 scripts/cache_experiments.py --output-dir build/results/cache-study
python3 scripts/cache_experiments.py --output-dir build/results/cache-study --audit-only
```

Wait cycles are **total added backing-memory waits**, not extra waits on top
of the synchronous default. Defaults are 0 (async) and 1 (sync). These knobs
configure the generic `bench`/`phase1` simulators; FPGA shells retain their
documented default geometry and synchronous timing. Use fresh `BUILD_DIR`
paths or the capture tool when changing compiler/toolchain flags.

The generated hex contains no address directives: line `N` is the little-endian
32-bit word at ROM address `4*N`. ELF load segments use their physical/load
address, so initialized `.data` can be copied from ROM into RAM by startup code
without creating a 256 MiB sparse ROM image.

## Toolchain policy

The architecture target and current bring-up configuration are RV32IM. The
PicoRV32 wrapper enables its internal multiply/divide implementations, and the
directed ISA image tests all eight RV32M operations and integer corner cases.
Compressed instructions remain disabled so the ROM format and fetch path stay
32-bit word aligned.

Firmware uses `-nostdlib -nostartfiles -nodefaultlibs -ffreestanding`; every
runtime service is therefore explicit and reviewable. `start.S` establishes a
RAM stack, copies initialized data, clears `.bss`, and calls `main`. The first C
image writes directly to the UART MMIO register so the CPU/ROM/bus/RAM/
peripheral path is verified without depending on libc.
