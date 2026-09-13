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

The accepted Phase 7 captures record GCC 16.1.0, binutils 2.47.20260726,
Verilator 5.020 and Vivado 2025.1. These are recorded tool identities, not a
claim that an arbitrary different release reproduces their exact binaries or
timing. Each accepted capture binds the actual compiler/linker/assembler and
simulator paths, versions and hashes. Physical collection uses PYNQ on ARM;
saved-record audits require only Python 3 and the repository's full Git history.

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
make multicore-adversarial-matrix  Test real-core faults and reset during transfers
make linux-dual-sim  Check the dual-hart AXI loader and actual serial backpressure
make fpga-linux-dual  Build the Phase 5 PCAP overlay, retaining all safety gates
make multicore-runtime-matrix  Cross 1/2 harts, caches off/on and four memory timings
make parallel    Run split-array AsterBench v3 and independent RTL scoreboards
make parallel-matrix  Cross 1/2 cores/workers, caches and four memory timings
make parallel-workloads  Test odd/boundary sizes, seeds and round counts
make atomic-runtime-matrix  Run real RV32IMA C with caches disabled
make coherent-runtime-matrix  Run full-A C with coherent caches enabled
make coherent-cache-matrix  Check independent coherence state/backing histories
make coherent-soc-matrix  Check real-core coherence and RAM-preserving lifecycle
make coherent-bench-matrix  Run AsterBench v4 configuration/workload combinations
make riscv-reference-matrix  Execute pinned upstream RV32UA test bodies
make coherent-litmus-matrix  Test ordering/publication/LRSC outcomes and progress
make linux-coherent-matrix  Test the Phase 6 AXI/serial runtime and lifecycle
make fpga-linux-coherent  Build a two-hart coherent RV32IMA PCAP overlay
make dma-engine dma-arbiter  Check autonomous copy/descriptor and CPU-group ordering
make dma-cache-matrix dma-cache-boundaries  Check coherent DMA and extreme geometry
make dma-counters dma-warm-stop  Check DMA event windows and safe stop escalation
make dma-runtime-matrix  Cross actual DMA C with harts, caches and memory timing
make dma-bench-cases dma-bench-sensitivity  Run paired v5 size/configuration checks
make linux-dma-matrix  Check actual DMA runtime and RAM-code publication over AXI
make linux-dma-bench-cases linux-dma-bench-baud  Check paired serial/physical-baud cases
make fpga-linux-dma  Build the explicitly DMA-enabled coherent PCAP overlay
make check       Run tool checks, directed tests and simulations
make clean       Remove generated files under build/
```

Both Verilator and Vivado define `RISCV_FORMAL` to expose the pinned core's
synthesizable RVFI observation ports. They do not define `FORMAL`.
RVFI itself changes measurement, not the ISA. Legacy firmware remains
RV32IM/ilp32; the explicit coherent/atomic/DMA targets build RV32IMA/ilp32.

Phase 5 simulation uses `HART_COUNT=1|2` (default 2) and the existing cache and
memory knobs. Legacy single-core targets ignore `HART_COUNT`; they retain their
original map. The multicore linker/runtime and simulation top are separate.
The [Phase 5 closeout](results/phase5/closeout-71e2570/README.md) documents its
actual dual-core FPGA/physical acceptance; Phase 6 and Phase 7 use separately
versioned coherent and DMA overlays.

The [current runtime guide](runtime.md) and [Phase 7 contract](phase7.md)
describe DMA-capable RV32IMA builds. Legacy targets do not enable DMA.
`make fpga-linux-dma LINUX_CACHE=0` and `LINUX_CACHE=1` retain 31.25 MHz and
the strict reset/HWH/routed signoff gates. They build images only; programming
requires the explicit [guarded PYNQ workflow](phase7-physical.md).

Use these separate clean-source runners for full acceptance, each with a new
output directory and isolated build tree:

```sh
python3 scripts/run_phase6_regressions.py --output /new/path/legacy-regressions
python3 scripts/run_phase7_regressions.py --output /new/path/dma-regressions
python3 scripts/audit_phase6_regressions.py /new/path/legacy-regressions/manifest.json
python3 scripts/audit_phase7_regressions.py /new/path/dma-regressions/manifest.json
```

They record all 22 legacy and 14 DMA targets respectively, complete logs and
exact source/tool identities. `run_phase6_regressions.py --check-only` is for
a fresh `make check`, not a replacement for either full run. Never rebuild
different configurations concurrently into the same `BUILD_DIR`. Failed or
partial evidence is retained; accepted captures refuse existing output paths.

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

The legacy architecture target is RV32IM. The PicoRV32 wrapper enables its
internal multiply/divide implementations, and the
directed ISA image tests all eight RV32M operations and integer corner cases.
Compressed instructions remain disabled so the ROM format and fetch path stay
32-bit word aligned. Coherent and DMA configurations additionally implement
full word RV32A through Aster-owned PCPI/memory integration; the pinned vendor
core is unchanged. This is a bare-metal ISA configuration, not privileged-mode
Linux support on the RISC-V harts.

Firmware uses `-nostdlib -nostartfiles -nodefaultlibs -ffreestanding`; every
runtime service is therefore explicit and reviewable. `start.S` establishes a
RAM stack, copies initialized data, clears `.bss`, and calls `main`. The first C
image writes directly to the UART MMIO register so the CPU/ROM/bus/RAM/
peripheral path is verified without depending on libc.
