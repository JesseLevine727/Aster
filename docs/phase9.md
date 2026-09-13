# Phase 9: INT8 GEMM matrix accelerator

Status: **contract frozen; implementation and acceptance are in progress**.
Baseline: pushed Phase 8 closeout `6f5b91b6a9318e3adf98438d872626f58d0dafab`.
The [README roadmap](../README.md#phase-9--matrix-accelerator--npu) defines this
phase as processing element → array → 4×4 MAC array → memory/control interface,
with an optional 8×8 configuration only after the 4×4 path is proven. The first
workload is INT8 GEMM. CNN, Conv2D, MNIST, and the CPU/multicore/DOT8/NPU
comparison belong to later phases.

This document is the Phase 9 contract. It preserves the completed Phase 1–8
maps, ABIs, source and evidence; no Phase 8 closeout artifact is modified.
Pinned PicoRV32 remains unchanged and vendor RTL is never edited.

## Scope and design principles

The first accepted accelerator is an Aster-owned, CPU-programmed matrix engine
attached to the coherent RV32IMA/DMA SoC. It computes row-major

```text
C[M][N] = A[M][K] × B[K][N]
```

where A and B contain signed two's-complement INT8 values and C contains the
32-bit two's-complement result of each dot product. The engine is a memory
master for shared RAM; the CPU supplies a descriptor and owns the buffers until
completion. There is no hidden accumulator, floating-point operation,
saturation, tensor compression, interrupt dependency, or compiler extension.

The implementation order is deliberately vertical:

1. A signed INT8 processing element with a public valid/ready interface.
2. A parameterized multiply-accumulate array, defaulting to 4×4.
3. A tile controller that handles partial edges, K accumulation and output stores.
4. A bounded shared-RAM master and descriptor/control registers.
5. Coherent/DMA/lifecycle SoC integration and a RAM-backed C driver.
6. A versioned AsterBench experiment, FPGA signoff and physical acceptance.

Correctness is the exit criterion. Performance and area are measurements, not
assumptions; a matrix engine that loses to the CPU at a problem size remains a
valid result.

## Arithmetic contract

### Processing element

`aster_int8_pe` accepts `a` and `b` as signed 8-bit operands and `acc_in` as a
32-bit accumulator. On one accepted operation it computes:

```text
product = signed(a) * signed(b)
acc_out = (acc_in + sign_extend(product)) modulo 2^32
```

The product is formed at signed 16-bit precision before sign extension. The
output is a 32-bit bit pattern; software and the oracle interpret it as a
two's-complement result. `valid` remains asserted and all inputs remain stable
until `ready`. Reset drops `ready/valid` state and clears the accumulator. No
operation is accepted while reset is asserted.

For the contracted K limit of 1024, the mathematical sum fits in signed 32-bit
range (`1024 × 128 × 128 < 2^31`), but the modulo-2^32 rule is still mandatory
at every accumulation boundary. This keeps the RTL, C model and future larger
configurations unambiguous.

### 4×4 array

`aster_int8_array` contains sixteen independently reset PEs. A tile step presents
four A row operands and four B column operands and updates all 16 accumulators:

```text
acc[row][col] += signed(A[row]) * signed(B[col])   for row,col in 0..3
```

Inactive rows or columns at an edge are masked and contribute zero. `start_tile`
clears the sixteen accumulators, `step` accepts one K position, and
`finish_tile` exposes sixteen stable 32-bit results until the next tile or
reset. The array has no RAM side effects and is testable without a SoC.

The optional 8×8 configuration is not part of the initial exit gate. If built,
it must use the same interface and semantics, pass the complete 4×4 oracle, and
be reported as a separate configuration rather than silently replacing 4×4.

## Matrix layout and bounds

Descriptors use byte addresses and byte strides:

```text
A element (i,k) = A_BASE + i*A_STRIDE + k
B element (k,j) = B_BASE + k*B_STRIDE + j
C element (i,j) = C_BASE + i*C_STRIDE + 4*j
```

`A_STRIDE >= K`, `B_STRIDE >= N`, and `C_STRIDE >= 4*N`. M, N and K are
unsigned values in `0..1024`. M=0 or N=0 is a successful no-op. K=0 writes
zero to every C element without reading A or B. The ordinary accepted test
cases use nonzero dimensions and include K=0 separately.

A, B and C must each lie wholly in the coherent shared-RAM window
`0x10000000..0x10008000`; private hart RAM, ROM, MMIO and external DDR are
rejected. Descriptor arithmetic is checked in widened arithmetic before any
memory request. A and B may be byte-unaligned, and C may begin at any byte
offset, subject to the containing 32-bit bus word remaining inside shared RAM.
The controller may fetch a containing word for an INT8 load but never writes
outside the exact C byte lanes. The driver rejects overlapping A/B/C ranges in
its normal API so ownership remains simple and deterministic.

Tiles are traversed in increasing M tile, N tile, then K order. For each output
tile, all sixteen accumulators start at zero, every valid K position is applied,
and only the valid M×N edge results are stored. No input or output guard byte
is written. The independent oracle computes every output with widened signed
Python arithmetic and masks the final bit pattern to 32 bits.

## Control and memory interface

Phase 9 uses the reserved RISC-V data page `0x40000000..0x40001000` in the
coherent/DMA configuration. Legacy/minimal builds retain their current reserved
zero/unsupported behavior. The initial integrated feature requires
`ENABLE_COHERENCE=1`, `ENABLE_DMA=1`, and `ENABLE_NPU=1`; a future single-hart
NPU build must prove an equivalent coherent ownership contract before it is
added.

The descriptor registers are:

| Offset | Name | Access | Meaning |
| ---: | --- | --- | --- |
| `0x00` | CONTROL | W | bit 0 START, bit 1 ABORT, bit 2 ACK; byte lane 0 only |
| `0x04` | STATUS | R | bit 0 busy, bit 1 done, bit 2 error, bit 3 aborted, bit 4 counting |
| `0x08` | ABI | R | NPU descriptor ABI = 1 |
| `0x0c` | COUNTER_ABI | R | AsterBench NPU counter ABI = 1 |
| `0x10` | A_BASE | RW | signed-INT8 A base byte address |
| `0x14` | B_BASE | RW | signed-INT8 B base byte address |
| `0x18` | C_BASE | RW | signed-32 C base byte address |
| `0x1c` | A_STRIDE | RW | A row stride in bytes |
| `0x20` | B_STRIDE | RW | B row stride in bytes |
| `0x24` | C_STRIDE | RW | C row stride in bytes |
| `0x28` | M | RW | output rows, 0..1024 |
| `0x2c` | N | RW | output columns, 0..1024 |
| `0x30` | K | RW | reduction length, 0..1024 |
| `0x34` | ERROR | R | stable error code until ACK or START |
| `0x38` | BYTES_READ | R | completed shared-RAM read bytes |
| `0x3c` | BYTES_WRITTEN | R | completed C byte writes |
| `0x40/44` | JOB_CYCLES | R | low/high total accepted-job cycles |
| `0x48/4c` | COMPUTE_CYCLES | R | low/high active-array cycles |
| `0x50` | TILES | R | completed output tiles |
| `0x54` | FEATURES | R | geometry and capability bits |

Configuration writes are accepted only while idle. START atomically validates
and captures the complete descriptor. START while busy is rejected without
changing the active job. ACK clears done/error/aborted status but retains
completed-byte, tile and last-job cycle accounting; a new accepted START or
reset clears those diagnostics. ABORT prevents new memory offers, lets an
already accepted transaction complete, then stops at a tile-safe boundary. It
may leave previously completed C tiles, which the report records; it never
claims a full result after an abort.

The NPU memory master uses the existing coherent device path. Its requests hold
address, data and byte strobes until ready, never issue instruction fetches,
and are restricted to shared RAM. Device writes invalidate matching private
cache lines using the established DMA/coherence mechanism. Before starting a
job, software publishes DMA-produced A/B data with the existing release/fence
contract. CPU and DMA must not modify descriptor-owned buffers until the NPU
completes or aborts. The first accepted implementation may serialize NPU and
DMA device offers through a fair arbiter, but it may not starve either master
or fabricate coherence by reading stale cached data.

## Reset and lifecycle

The engine is idle after POR and after an acknowledged global warm STOP. A
global STOP blocks new NPU starts, cooperatively aborts an admitted job after
the current lower transaction settles, and participates in the existing
`fabric_busy`/flush drain. Selective hart reset cannot cancel or corrupt a
device-owned job; the integrated contract uses global stop for NPU abort and
requires the complete RAM-preserving stopped-state oracle afterward.

No result, descriptor, or input is written by the ARM host during physical
acceptance. The PYNQ host may only use the existing guarded control/ROM/STOP
path; the RISC-V driver performs descriptor writes and owns the payload buffers.
The final state must report NPU idle, no pending request, no active tile,
empty serial FIFO, and CPU/DMA/NPU STOPPED at the preserved 31.25 MHz clock.

## Software and golden model

`software/drivers/aster_npu.h` / `.c` will provide a narrow descriptor API:

```c
int aster_npu_submit(const struct aster_npu_gemm *job);
int aster_npu_poll(struct aster_npu_status *status);
int aster_npu_wait(unsigned maximum_polls, struct aster_npu_status *status);
int aster_npu_abort_and_wait(unsigned maximum_polls, struct aster_npu_status *status);
```

The API validates dimensions, strides, shared-RAM bounds, non-overlap and
ownership before touching CONTROL. It uses compiler barriers and `fence
iorw,iorw` around descriptor publication and result consumption. Timeouts are
poll-count limits: timeout does not release buffers or pretend that the engine
stopped. C contains a scalar signed-INT8 GEMM reference used by firmware and
the independent host oracle; it must avoid signed-overflow and aliasing UB.

## AsterBench v7 measurement contract

Phase 9 extends AsterBench with a new strict schema/version; v2 through v6 are
not changed. Every v7 record binds the exact scalar and NPU inputs, dimensions,
strides, byte placements, seed, cache/timing configuration, descriptor ABI,
counter ABI, full C output allocation, guard bytes, scalar output, NPU output,
status/error, CPU/DMA/cache counters, NPU read/write/compute/job cycles,
completed tiles, FPGA clock and source/toolchain/build provenance. Unknown,
duplicate, noncanonical, truncated or rehashed fields fail validation.

The primary measurement window starts at the documented CPU command edge and
ends after completion/result visibility. It reports total offload cost
separately from NPU active-array cycles and memory-only cycles. A paired scalar
job uses the same A/B/C allocations, seed, output guards, cache preparation and
correctness checks. Setup, descriptor traffic, memory reads, partial tiles and
completion are included in the end-to-end comparison; UART formatting and
global STOP are excluded. Ratios below one remain valid results.

The fixed first study is predeclared as 96 primary captures: twelve GEMM shapes
`(M,N,K)` = `(1,1,1)`, `(1,3,4)`, `(3,1,7)`, `(3,5,8)`, `(4,4,16)`, `(5,7,3)`,
`(7,5,15)`, `(8,8,31)`, `(15,3,32)`, `(16,16,64)`, `(31,5,7)` and `(32,32,32)`;
four A/B/C byte-placement patterns (all aligned, A+1, B+2, C+3); and cache
off/on. A separate functional matrix adds zero-K, zero-dimension, every
partial-tile edge, seeded extrema/cancellation and randomized dimensions.
At least four independently fresh-build repeats must reproduce complete
records, outputs, RAM and observations. Any later 8×8 study is separate and
cannot replace these 4×4 captures.

## Verification and acceptance gates

- [x] README scope, Phase 8 baseline, memory ownership and Phase 9 arithmetic/control contract frozen.
- [ ] PE directed signed products, zero/extrema/cancellation and handshake/reset tests.
- [ ] 4×4 array edge masks, tile sequencing, K=0 and exact scalar-oracle tests.
- [ ] RAM master bounds, byte placement, stride, guard and malformed-descriptor tests.
- [ ] Actual CPU-controlled RAM-backed C GEMM on one/two harts, cache off/on and supported waits.
- [ ] DMA publication/coherence, safe global STOP/ABORT, reset/error/timeout and disabled/legacy tests.
- [ ] AsterBench v7 records, independent output/RAM/event/provenance audits, mutation tests and fresh repeats.
- [ ] 31.25 MHz routed/reset/HWH/resource/bitstream gates and guarded PYNQ Linux execution where applicable.
- [ ] Applicable Phase 1–8 regressions, immutable requirement mapping, fresh checkout and pushed closeout.

The README Phase 9 exit is satisfied only when randomized INT8 GEMM is
bit-correct against the independent scalar model and every applicable gate
above passes. Area, frequency, utilization and speedup claims require retained
simulation/FPGA evidence; they may not be inferred from a unit test.

## Explicit non-goals

This phase does not add CNN/Conv2D, pooling, activation functions, quantized
model formats, interrupts, privileged software, shared L2, DDR/scatter-gather
DMA, dynamic scheduling, floating point, saturation, or a compiler fork. It
does not rewrite PicoRV32, change Phase 1–8 maps/ABIs, claim ASIC readiness, or
perform the README Phase 10 cross-engine experiment.

## Repository layout

| Area | Phase 9 responsibility |
| --- | --- |
| `rtl/accelerator/` | PE, 4×4 array, tile controller, RAM master and control block |
| `rtl/interconnect/` | Fair CPU/DMA/NPU device arbitration and coherent request path |
| `rtl/soc/` | Explicit `ENABLE_NPU` integration and lifecycle wiring |
| `software/drivers/` | Descriptor API, fences, polling and scalar reference boundary |
| `software/tests/` | RAM-backed GEMM, guards, randomized and lifecycle firmware |
| `software/benchmarks/` | Paired scalar/NPU GEMM workload |
| `verification/unit/` | PE/array/controller protocol scoreboards |
| `verification/soc/` | Actual-core, RAM, coherence, DMA and STOP scoreboards |
| `scripts/` | AsterBench v7 records, studies, audits and immutable evidence |
| `docs/results/phase9/` | Only completed, audited closeout artifacts |
