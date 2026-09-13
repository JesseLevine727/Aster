# Verification strategy

The verification hierarchy follows the project roadmap:

```text
unit RTL checks → core instruction tests → SoC bare-metal tests
                 → subsystem randomized tests → AsterBench regressions
```

## Phase 5 development tests (not closeout)

`make multicore-adversarial-matrix` runs real-core fault and in-flight global
reset tests with caches off/on and async 0/4 or sync 1/4 wait cycles. Each of
eight configurations runs 12 secondary fault cases over two warm boots:
ECALL/EBREAK/illegal, misaligned LW/LH/SW/SH/JALR, execution from MMIO, peer
private RAM or unmapped memory, and unsupported AMO. Shared sentinels must
survive, hart 0 must continue retiring, and each secondary restart must clear
its private BSS. Six latency-bearing configurations also run three seeds, each
with 24 resets at actual stalled reads/writes from both cores, then 24 checks
that accepted stores survive reset. Verification-only RAM/transaction taps are
not part of any FPGA file set.

The fault regression exposed a genuine vendor-integration defect: a misaligned
store reached memory before trap. Core-wrapper qualification fixes both cached
and uncached/zero-wait paths. `make retirement` now checks exactly one legitimate
load and store (not just the final RAM value), forbids withdrawal even on trap,
and crosses all six fault endings with fixed 0/1/7 and seeded 0–7-cycle waits.
An old store fixture wrote the value already in RAM and could not reveal this
side effect; the faulting store now writes zero to a nonzero sentinel.

`make linux-dual-sim` tests the new bridge ABI, partial AXI write reset, ROM
programming protection, read-only hart/lifetime registers, secondary lifecycle,
and complete actual UART TX/RX streams for the runtime plus one/two-worker
parallel firmware. Each has two boots and delayed host reads; the parallel
runs exercise RX credit backpressure. Original `linux-sim` remains in `check`.

The new tests are additive; Phase 1–4 tests still target the legacy map and
measurement ABI. `make check` includes the default arbiter, shared-fabric and
multicore-runtime tests as well as all existing targets.

- `make arbiter`: an independent two-requester reference scoreboard checks
  round-robin fairness, a late arrival behind a locked grant, all 16 strobe
  masks, identical back-to-back requests, response isolation, target side
  effects and reset cancellation. Three seeds each run 100,000 random cycles.
- `make fabric-matrix`: one/two enabled requester ports × async/0-wait,
  async/4-wait, synchronous/1-wait and synchronous/4-wait × three seeds. The
  actual fabric, ROM/RAM, UART, hart control and counter blocks are instantiated.
  Checks cover IDs, owner-restricted registers, private-memory permissions,
  actual bounds, instruction permissions, masked stores, mailbox lanes,
  global counter commands, UART stall/ownership, secondary reset behind an
  in-flight store, global reset, random contention and final RAM readback.
  These requesters are **test drivers**, not evidence of CPU execution.
- `make multicore-runtime-matrix`: one/two actual PicoRV32 instances × L1 off/on
  × the same four timings. Each configuration boots twice from poisoned RAM;
  each dual-hart boot releases/restarts the secondary twice and runs eight
  producer/consumer jobs per release. Firmware tests separate aligned stacks,
  recursion, initialized odd bytes, shared/private BSS, non-reinitialization of
  primary state, denied cross-private stores and visible uncached results.
  The harness counts RVFI retirement and distinct PCs separately for both
  physical instances; a one-hart elaboration must report no hart-1 retirement.
- `verification/host/test_multicore_layout.py`: accepts exact shared/private
  capacity boundaries, rejects one-byte overflow into each stack and shared
  region, checks stack alignment/limits, rejects ROM overflow, and verifies a
  word-padded odd-byte shared-data segment with its ROM load address.

`make parallel` tests a separate, genuinely parallel array kernel. ELF symbol
bounds identify its instructions; actual RVFI PC/retirement signals prove each
participating core executes it. For substantial jobs (at least 64 words and
four rounds), kernel retirement intervals must overlap. Tiny jobs may finish
one slice before the other starts; their overhead is still measured honestly.
An external C++ scoreboard starts/freezes on accepted hardware control writes,
counts every per-hart event and compares all 16 emitted bank values exactly.
It checks each slice checksum using a separate host oracle; firmware separately
checks every output word against its word-major scalar reference.

`parallel-matrix` tests 24 configurations: hardware/worker counts 1/1, 2/1,
2/2 × L1 off/on × four memory timings. `parallel-workloads` adds ten cases
covering 2/7/129/1024-word boundary/odd sizes, 1/4/16/64 rounds and diverse seeds.
Each run executes three fresh jobs per boot and two full warm boots by default.
Strict Python/C++ v3 parser mutation tests cover missing/duplicate fields,
malformed numeric encodings, unknown fields, partition/seed/counter invariants
and inactive workers. The host stream checker rejects missing, reordered or
extra jobs and independently verifies their checksums. Capture mutation tests
bind serial records and per-hart observations to the actual hashed run log.

Further adversarial core-level trap/reset tests, full regression closeout and
actual FPGA validation remain tracked in [the Phase 5 contract](phase5.md).

## Phase 0 tests

`verification/unit/tb_aster_smoke.cpp` is the first Verilator test. It proves
that the repository can compile SystemVerilog, build a C++ harness, drive a
clock/reset and observe a deterministic result.

`verification/soc/tb_aster_hello.cpp` is the first system test. It observes the
UART events while the PicoRV32 RV32IM core fetches C firmware from ROM,
initializes the runtime, checks RAM, and prints its message.

`software/tests/rv32im_directed.S` includes 1,584 generated arithmetic,
immediate, branch and all-M-extension vector groups, followed by directed
load/store byte/halfword lanes, sign extension, x0, LUI/AUIPC, JAL/JALR and
FENCE checks. The integer oracle in `scripts/gen_rv32im_vectors.py` uses a fixed
seed and independent Python arithmetic. Its semantic references are the
[RV32I v2.1](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html)
and [M v2.0](https://docs.riscv.org/reference/isa/v20260120/unpriv/m-st-ext.html)
chapters. This does not replace architectural-suite certification.

`software/boot/hello.c` runs through `software/runtime/start.S`; its `.bss`
array verifies RAM accesses. `software/tests/runtime.c` separately verifies
nonempty initialized data, byte/word BSS and recursive stack use. It runs with
nonzero RAM initialization and two resets so absent BSS clearing or data
copying cannot be hidden by power-on zeros.

`software/benchmarks/memcpy_bench.c` is the first AsterBench workload. It
copies a deterministic 256-byte RAM buffer four times, validates every word,
and emits one complete machine-readable record.
`verification/soc/tb_asterbench.cpp` uses the shared strict v2 parser to check
all required fields, types and relationships, including actual retirement,
native/backing transactions and the cache configuration reported by hardware.
Inactive DMA and accelerator sources must remain explicitly zero.

`verification/soc/tb_pynq_z1.cpp` drives the PYNQ-Z1 shell reset and board
clock, samples the physical UART line at the 125 MHz shell-clock resolution,
and decodes the complete 8-N-1 stream. This catches clock-divider, reset-domain,
FIFO and baud-timing errors that a one-cycle simulation UART cannot see.

The FPGA build is checked by the Vivado reports under `build/fpga/pynq_z1/`:
the memory implementation must infer BRAM, report zero DRC errors and
unrouted nets, and contain no failing setup/hold endpoints in
`timing_summary.rpt`.

The Linux build also checks the exported HWH clock/reset/address contract
and simulates its actual generated `proc_sys_reset` netlist using Vivado XSim.
`verification/fpga/tb_linux_reset.sv` checks power-on release, warm reset,
clock-lock loss/recovery and auxiliary/debug polarity. This catches a shell
integration defect that the standalone AXI RTL test cannot see: active-low
auxiliary reset tied to zero holds the real interconnect and peripheral in
reset. The original netlist fails release; the corrected netlist passes.
Host mutation tests reject wrong/missing/duplicate HWH parameters, drivers,
ports, clocks and address maps, and prove rejection before any PYNQ import.
`scripts/audit_pynq_results.py` independently validates the retained physical
records, image hashes and exact benchmark/reference agreement. Host mutation
tests reject missing boots, corrupted output/counts/status, mistyped counters,
missing provenance and mismatched image/configuration fields.

Run both with:

```sh
make check
```

Run only the benchmark regression with:

```sh
make bench
```

## Test conventions

- Tests fail fast with a non-zero exit code.
- Firmware inputs and expected output are deterministic.
- Generated binaries and Verilator objects stay under `build/`.
- A test should name the architectural contract it proves.
- Assertions and randomized tests are added before each major subsystem is
  connected to the SoC.

## Next verification increments

1. Maintain Phase 2 physical Linux/firmware validation and reset/handoff gates.
2. Maintain the Phase 3 parser/counter/snapshot/provenance regression contract.
3. Maintain the Phase 4 randomized/stall/reset/configuration and experiment regressions.
4. Integrate a full external architectural reference suite where practical.
5. Add coherence/cache-maintenance tests when those protocols are introduced.
6. Add a reference-model comparison for the future NPU.

## Phase 3 measurement verification

- `make retirement`: an independent fixed instruction/PC fixture includes
  branches/jumps over prefetched instructions, MUL/DIV, load/store with seeded
  fixed 0/1/7 and seeded 0–7-cycle memory stalls, FENCE and six fault endings over two boots. Exactly
  16 successful instructions retire; the fault and repeated halted cycles do
  not. The memory protocol and final arithmetic result are also checked.
- `make counters`: seeded events/control writes are compared every cycle
  against eight independent 64-bit software counters. It tests disabled state,
  clear/start, freeze, resume, byte strobes, unrelated addresses, atomic frozen
  reads, reset and overflow. Rollover uses Verilator storage introspection to
  seed counters near 2^64; no test-only MMIO or altered production reset value
  exists. Wide DMA increments exercise low-half carry as well.
- `make host-tests`: every required record field is removed and duplicated;
  invalid syntax, widths, signs, ranges, consistency, inactive-source values,
  extra lines and unknown fields are rejected. One identical corpus runs
  against Python and C++ validators. Provenance and comparison tests reject
  missing metadata, altered typed/raw values, manifest mismatch and unequal
  workload definitions.
- `make check`: the 448-byte v2 record travels through standalone UART
  serialization; the accelerated-clock Linux bridge test emits 443 bytes
  because its clock-frequency field has fewer digits. Both paths run twice,
  require strict records and reject trailing bytes.

`scripts/bench_results.py capture` performs a clean isolated build and saves
the actual record plus provenance; `compare` checks comparability before
computing deltas. Cached/uncached runs must have the same checksum and exact
retirement count for this fixed workload. Reported cycle differences are
measurements, not an assumed cache speedup.

## Phase 1 closeout

`verification/soc/tb_aster_soc.cpp` is the shared firmware runner. `+rom=`
selects a ROM image without recompilation; `+ram_fill=` poisons initial RAM.
Both controls are simulation-only. Expected traps require a complete armed
marker first, remain asserted until reset, and are tested over two boots.
`software/tests/memory_map.c` covers actual memory boundaries, permissions,
UART store lanes and execution/update of RAM code. Twelve images compiled from
`software/tests/traps.S` exercise the negative paths. Host tests enforce linker
stack reservation/alignment, ROM limits and independent oracle fixtures.

```sh
make phase1
make phase1-matrix  # ENABLE_L1=0/1 crossed with SYNC_MEMORY=0/1
make runtime       # poisoned initial RAM + warm restart
make memory-map
make traps
make host-tests
```

The generic simulators use separate configuration directories under `build/`,
so toggling cache/memory mode cannot accidentally reuse another model.

## Phase 4 cache tests

`verification/unit/tb_aster_l1_cache.cpp` models an always-ready lower memory
and checks the cache contract directly: a cold read fetches exactly one line,
subsequent words hit locally, lower-level backpressure is tolerated, byte-masked
stores update both the resident line and lower memory, conflicting lines evict
and refill, store misses do not allocate, and uncached requests bypass the
cache. The SoC regressions then exercise the two-cache integration with the
real PicoRV32 firmware path.

`tb_aster_l1_random.cpp` adds separate architectural and serviced backing-memory
models plus an independent direct-mapped tag/valid model. Every lower beat is
checked for exact address, byte strobes, write data and count. It checks
request stability under 0–7-cycle seeded backpressure, poisons unaccepted
read data, permits idle-ready and requires exact access/miss event totals.
Directed preludes guarantee every nonzero byte mask, read/write hits/misses,
conflict eviction, no-write-allocate without eviction, side-effecting MMIO
reads and identical back-to-back requests with valid held high.

Five resets interrupt a request: before refill, halfway through refill, after
the last refill beat but before the CPU response, stalled write-through and
stalled MMIO write. No request/event may leak during reset, every old/partial
line becomes invalid, and already committed memory persists. Each seed then
runs 5,000 randomized transfers plus periodic resets, for 5,092 completed
and five explicitly aborted requests in total.

```sh
make cache                 # original directed tests + three scoreboard seeds
make cache-matrix          # words 2/4/8/16 × lines 2/4/8/16/32/64
make cache-boundaries      # remaining bit widths and extreme aspect ratios
make phase4-soc-matrix
```

The cache matrix has 24 geometries; boundaries add 12 (words 32..1024 with
16 lines, lines 128..1024 with four words, 2×1024 and 1024×1024). Three seeds
(`1`, `0xa57e`, `0xc0ffee`) run each, totaling 108 scoreboard runs. This is
representative width/boundary coverage, not an exhaustive Cartesian sweep or
a claim that the largest geometry fits this FPGA.

The 24-leg SoC matrix crosses L1 off/on, geometries 2×2/4×16/8×32 and memory
timings async/0 waits, async/4, sync/1 and sync/4. Every leg runs the complete
Phase 1 firmware/host regression and a strict benchmark record, exercising
both private caches with the actual CPU and memory permissions. Generic
model paths encode every hardware knob; the complete baseline `make check`
also covers retirement/counter units and both physical-UART simulation paths.

Host tests require the complete four-experiment study plan and reject altered
configuration/checksum/retirement/provenance. Matching sequential/random
kernels must have identical opcodes and PCs for each swept working set and
the 2/4096-word boundaries, with the measured ring at the same RAM base.
See [AsterBench](../software/benchmarks/README.md) for measurement windows,
warm-up, independent correctness checks, seeds and experiment commands.
