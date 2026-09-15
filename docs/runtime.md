# Bare-metal runtime through Phase 10

The ARM runs PYNQ Linux; the RISC-V harts run freestanding C firmware. These
are separate execution environments. Aster has fatal fault reporting, not a
privileged RISC-V operating system, exception dispatcher or interrupt runtime.

## Select the matching architecture

| Firmware / hardware | ISA | Startup and memory contract |
| --- | --- | --- |
| Minimal single hart, Phases 1–4 | RV32IM / ilp32 | `software/runtime/start.S`, legacy single-core linker/map |
| Noncoherent multicore, Phase 5 | RV32IM / ilp32 | `start_multicore.S`, uncached shared RAM and private regions |
| Coherent / DMA, Phases 6–7 | RV32IMA / ilp32 | Same multicore startup/ownership map, coherent shared RAM and full word atomics |
| Optional packed compute, Phase 8 | RV32IMA + Xasterdot8 / ilp32 | Same RAM ownership/startup, explicit `.insn` helper and feature-matched image |
| Optional matrix accelerator, Phase 9 | RV32IMA + Xasterdot8 / ilp32 | Same RAM ownership/startup, NPU descriptor API at `0x40000000` |
| Cross-engine study, Phase 10 | RV32IMA + Xasterdot8 / ilp32 | Same image enables harts, caches, DMA, DOT8 and NPU together |

The [original runtime README](../software/runtime/README.md) describes the
legacy/Phase 5 variants. Its uncached-shared-memory and future-atomics notes
do not describe the separate Phase 6/7 coherent configuration. Linking a DMA
driver or compiling with `-march=rv32ima` does not enable those features in a
legacy bitstream. Match the actual bridge ABI, hart/cache/DMA configuration,
ELF and ROM before running firmware.

Phase 8 retains `-march=rv32ima`: its custom instruction is emitted explicitly
by the helper rather than advertised as a standard RISC-V extension. A legacy
image does not gain DOT8 by linking the header; the instruction traps when
disabled. The explicit Linux image reports bridge ABI `0x80001`, DOT8 instruction
ABI 1 and combined counter ABI 6.

## RAM initialization and ownership

[`start_multicore.S`](../software/runtime/start_multicore.S) reads the real hart
ID before touching a stack. Hart 0 copies initialized data from ROM, clears
shared BSS and its private BSS, then enters `main`. Hart 1 clears only its own
private BSS and enters `aster_secondary_main`; hart 0 releases it after shared
initialization/publication. Returning parks the corresponding hart.

[`link_multicore.ld`](../software/boot/link_multicore.ld) reserves 64 KiB ROM,
32 KiB shared RAM at `0x10000000`, and two 16 KiB private regions at
`0x10008000` and `0x1000c000`. Each hart has a 4 KiB, 16-byte-aligned stack.
Linker assertions reject private-section/stack overlap. Private sections are
NOLOAD, zero-initialized storage; do not put nonzero initializers there.
Neither a peer hart nor DMA can access another hart's private stack/results.

Phase 6/7 shared objects may be cached. Use C11 atomic release/acquire handoffs
or the explicitly documented memory/MMIO fence and ownership protocol;
`volatile` alone is not synchronization. Full RV32A includes LR/SC and all
word AMOs. Successful DMA destination writes invalidate matching word
reservations, even for same-value byte writes; DMA reads do not. Faults on
misaligned/denied atomic accesses are tested, not silently emulated by software.

## Coherent DMA from C

Use [`aster_dma.h` / `aster_dma.c`](../software/drivers/README.md), the
multicore startup/linker and the existing `dma-runtime` target. Payloads must
be wholly inside shared RAM; local stack buffers are not DMA payloads. Only
hart 0 owns the descriptor registers. A nonzero overlapping copy is rejected;
all byte alignments and zero-length no-ops are supported.

`aster_dma_copy(destination, source, bytes, maximum_polls)` includes submission,
polling and completion fences. The coherent hardware obtains dirty source
data, preserves dirty destination neighbors and invalidates destination cache
lines as required: no manual flush is needed. The caller must retain ownership
of both buffers until actual completion. Timeout means the poll budget expired,
not that the hardware
stopped; `aster_dma_abort_and_wait` drains an offered transaction and reports
the completed prefix. A second timeout still does not release the buffers.

Global warm STOP drains/settles admitted traffic, flushes dirty caches and
acknowledges STOPPED before ROM writes or host RAM snapshots. Secondary reset
pauses new DMA offers, preserves admitted effects, flushes the secondary and
resumes the remaining copy. A transient global stop during that sequence is
latched. These are RAM-preserving operations, distinct from initial POR.

## Packed signed INT8 computation from C

[`aster_dot8.h`](../software/drivers/aster_dot8.h) exposes
`aster_dot8_packed(uint32_t a, uint32_t b)`: four signed byte products are summed
exactly and returned as a 32-bit bit pattern. Lane zero is the least-significant
byte. For example, two `0x80808080` operands produce `65536`, not a saturated
8- or 16-bit result. Add results using `uint32_t` for defined modulo-2^32
accumulation; the hardware holds no persistent accumulator.

`aster_dot8_pack4` requires four remaining bytes in the source object. Its
aligned bit-copy path and unaligned byte loads avoid aliasing and alignment
undefined behavior. Handle the remaining zero to three bytes with scalar
arithmetic; do not overread a tail. The independently tested shared kernels in
[`dot8_kernels.c`](../software/benchmarks/dot8_kernels.c) implement dot product,
eight-output FIR and 3×5 row-major GEMM with packing/gather inside the custom
kernel, not in unmeasured preprocessing. Scalar kernels remain ordinary
optimized signed byte loads and MUL instructions.

DOT8 does not touch memory or LR/SC reservations. Normal release/acquire
ownership is still required for shared buffers and DMA-produced inputs.
`dot8_runtime.c` runs 736 scalar/custom pairs on each hart, all 16 input-byte
alignment combinations, 16 LR-dot-SC trials per hart and three DMA-published
GEMM jobs. A fourth copy overlaps register-only DOT8 work. It freezes all 50
counters before emitting `DOT8 RUNTIME PASS`, then parks both harts. Its
functional evidence is separate from the latency benchmark.

```sh
make dot8-runtime HART_COUNT=2 ENABLE_L1=1 SYNC_MEMORY=1
make linux-dot8-sim HART_COUNT=2 ENABLE_L1=1 DOT8_LINUX_BAUD=115200
python3 scripts/dot8_functional_results.py capture NEW_REFERENCE --l1 1
```

The reference collector retains both real warm boots, complete stopped RAM,
actual UART and independent events, followed by the active-compute stop tests.
See the [physical workflow](phase8-physical.md) for feature-matched FPGA runs.

## Matrix accelerator and cross-engine kernels from C

[`aster_npu.h`](../software/drivers/aster_npu.h) exposes
`aster_npu_submit`/`poll`/`wait`/`abort_and_wait` for the Phase 9 4×4 INT8 GEMM
accelerator. A and B are signed INT8 with byte strides; C is 32-bit
two's-complement written modulo 2^32. The driver validates dimensions, strides,
shared-RAM bounds and non-overlap before touching CONTROL, and fences descriptor
publication and result consumption. `aster_npu_scalar_gemm` is the independent
scalar reference used by firmware and the AsterBench pair. `A_STRIDE >= K`,
`B_STRIDE >= N` and `C_STRIDE >= 4*N`; `K=0` writes zeros without reading.

Phase 10 uses one shared descriptor shape for its cross-engine kernels
([`xe_kernels.c`](../software/benchmarks/xe_kernels.c)): signed-INT8 dot, FIR and
GEMM through the scalar CPU, two coherent harts, Xasterdot8 or the NPU. The
multicore path splits output rows (or the K reduction for dot) and publishes
results with the same release/acquire ownership contract as the other shared-RAM
workloads. The NPU FIR path materializes a `TAPS × K` Toeplitz input because the
descriptor requires `A_STRIDE >= K`.

```sh
make xe-bench-validate XE_KERNEL=gemm XE_METHOD=npu XE_M=4 XE_N=4 XE_K=16
make xe-matrix
python3 scripts/xe_study.py capture --output build/xe_study
```

## Existing programs and verification

```sh
make dma-runtime                 # Actual-core directed C driver/runtime
make dma-runtime-matrix          # 16 hart/cache/memory configurations
make linux-dma-sim               # AXI/serial runtime and RAM-code publication
make linux-dma-matrix            # Both hart counts and cache modes
make dma-bench SYNC_MEMORY=1     # Paired CPU/DMA AsterBench v5
```

Use an isolated fresh `BUILD_DIR` for independent runs; benchmark RAM captures
refuse to overwrite old evidence. `dma_runtime.c` checks buffers/guards,
invalid descriptors, aborts, LR/SC, peer publication and selective resets.
`dma_publication.c` copies actual executable RAM bytes and executes the
published instructions on both harts after an ownership handoff. The benchmark
uses a separate [fixed-placement linker](../software/boot/link_dma_bench.ld)
and [measurement contract](phase7-bench.md); its polling latency experiment
does not demonstrate freed CPU time. See [verification](verification.md#phase-7-dma-checkpoints)
and the [physical workflow](phase7-physical.md) for independent acceptance.
