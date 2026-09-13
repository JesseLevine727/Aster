# Bare-metal runtime through Phase 7

The ARM runs PYNQ Linux; the RISC-V harts run freestanding C firmware. These
are separate execution environments. Aster has fatal fault reporting, not a
privileged RISC-V operating system, exception dispatcher or interrupt runtime.

## Select the matching architecture

| Firmware / hardware | ISA | Startup and memory contract |
| --- | --- | --- |
| Minimal single hart, Phases 1–4 | RV32IM / ilp32 | `software/runtime/start.S`, legacy single-core linker/map |
| Noncoherent multicore, Phase 5 | RV32IM / ilp32 | `start_multicore.S`, uncached shared RAM and private regions |
| Coherent / DMA, Phases 6–7 | RV32IMA / ilp32 | Same multicore startup/ownership map, coherent shared RAM and full word atomics |

The [original runtime README](../software/runtime/README.md) describes the
legacy/Phase 5 variants. Its uncached-shared-memory and future-atomics notes
do not describe the separate Phase 6/7 coherent configuration. Linking a DMA
driver or compiling with `-march=rv32ima` does not enable those features in a
legacy bitstream. Match the actual bridge ABI, hart/cache/DMA configuration,
ELF and ROM before running firmware.

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
