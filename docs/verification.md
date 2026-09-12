# Verification strategy

The verification hierarchy follows the project roadmap:

```text
unit RTL checks → core instruction tests → SoC bare-metal tests
                 → subsystem randomized tests → AsterBench regressions
```

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

1. Recover the board and complete Phase 2 physical Linux/firmware validation.
2. Maintain the Phase 3 parser/counter/snapshot/provenance regression contract.
3. Complete the Phase 4 randomized/stall/reset/configuration verification.
4. Integrate a full external architectural reference suite where practical.
5. Add coherence/cache-maintenance tests when those protocols are introduced.
6. Add a reference-model comparison for the future NPU.

## Phase 3 measurement verification

- `make retirement`: an independent fixed instruction/PC fixture includes
  branches/jumps over prefetched instructions, MUL/DIV, load/store with seeded
  0–7-cycle memory stalls, FENCE and six fault endings over two boots. Exactly
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
