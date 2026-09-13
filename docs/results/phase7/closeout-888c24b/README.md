# Phase 7: coherent DMA and measured CPU-copy crossover

All seven [Phase 7 requirements](../../../phase7.md#required-verification-and-final-audit)
pass. The [manifest](manifest.json) binds **2,003 raw artifacts** and maps every
requirement to its independently audited evidence.

The [README Phase 7 experiment](../../../../README.md#phase-7--dma) requires
an actual memory-to-memory engine, descriptor/status/completion registers and
CPU `memcpy` versus DMA over increasing sizes. This bundle retains actual
bitstreams/HWH, routed/reset reports, complete ELF/ROM/map/disassembly, raw
simulation/physical UART and 64-KiB RAM, compiler/source identities and logs.
No simulator output is relabeled as board execution. README prose is not
included in the artifact hashes.

## Read-only reproduction

From the repository root, with Python 3 and the full repository Git history:

```sh
python3 scripts/audit_phase7.py audit docs/results/phase7/closeout-888c24b
```

No board, PYNQ, compiler or Vivado installation is required for this saved-record
audit. It resolves recorded source blobs, checks every artifact and reruns the
independent semantic validators; it never executes saved commands/tool paths
or connects to hardware. `--current` additionally requires current build/audit
inputs to match the fresh-checkout verification. Omit it for historical evidence
after later phases change those inputs.

## Requirement-to-evidence map

| Requirement | Principal evidence |
| --- | --- |
| Autonomous engine, descriptor ABI and C driver | [DMA regressions](regressions/dma/manifest.json): engine, arbiter, real-core runtime |
| Coherence, atomic reservations, safe abort/reset | DMA cache matrices/boundaries, full-A fabric, warm-stop and Linux matrices |
| Actual RISC-V functional execution | [Cache-off reference](reference/functional-c0/functional.json), [cache-on reference](reference/functional-c1/functional.json), four physical runtime/publication packages |
| Preserve Phases 1–6 | [Complete legacy run](regressions/legacy/manifest.json): all 22 targets, 2,397 emitted scenarios |
| AsterBench v5 size/crossover and reproducibility | [Simulation study](reference/study/study.json), [physical study](physical/study/physical-study.json), counter/benchmark regressions |
| Routed FPGA and actual safe Linux deployment | [Cache-off overlay](fpga/c0/overlay.json), [cache-on overlay](fpga/c1/overlay.json), raw board logs and [final register read](physical/final_state.json) |
| Clean-source tests and fresh checkout | Complete DMA supplement plus [fresh manifest](verification/manifest.json) and [raw log](verification/01-check.log) |

The outer manifest enumerates every required artifact and these seven gates.
An incomplete matrix, copied PASS count, hash-consistent damaged record or
invented summary cannot substitute for the actual nested evidence.

## Explicit clean source revisions

| Evidence | Revision |
| --- | --- |
| RTL/firmware, legacy regression, both FPGA builds and fixed simulation study | `888c24b9d63a3650065102741e6bec65709bc4a6` |
| Complete DMA supplement and physical-study collector | `694ae0e789d5365c2f99e33ccde15ea63edcfa09` |
| Functional reference exporter/captures | `6bed91b088862679c36eb9567081fea713d74278` |
| Functional physical collector | `68999899972e98b92b0e60baf1c9dd4d0e44d81c` |
| Final validators and fresh-checkout verification | `b592cdc9c7555a3dbc942d87dc3f94621230da28` |

Later host tooling does not imply later hardware. RTL, firmware, pinned vendor
cores, FPGA/build inputs and hardware tests are identical across acceptance
packages except the explicitly audited optional UART/RAM/event exporter in
`tb_pynq_linux_coherent.cpp`. That exact exporter is bound to both functional
references and the fresh build. Original versions and hashes remain recorded;
the exception does not permit arbitrary harness changes.

The fresh detached checkout rebuilt into an empty isolated build tree:
`make check` passed in **93.3 s**, with **149 host tests and 157 emitted
scenarios**. The full 22-target regression is separate, not inferred from
`check`. The unchanged historical Phase 6 closeout also passes its own audit.

The [complete DMA supplement](regressions/dma/manifest.json) passes all **14
targets and 569 emitted scenarios**, with its 130-test host suite at the
recorded revision. It includes all 16 runtime configurations, 48 full runtime
boots, 80 in-flight global stops and 32 selective/global escalations; 42 regular
plus nine alternate benchmark configurations; all four Linux runtime/code
configurations; and ten serial plus two physical-baud benchmark configurations.
The later 149-test fresh suite includes the final functional and requirement
auditors. Neither suite count is substituted for actual RTL/physical evidence.

## Actual PYNQ-Z1 study

The clean simulation and physical studies each contain **144 captures,
288 warm boots, 1,152 paired jobs and 2,304 method records**. Twenty-three
sizes from zero through 8 KiB cross three alignments and cache off/on; six
separately rebuilt 1-KiB cases test repeatability. Each capture runs four
balanced CPU/DMA pairs per boot, alternating method order, on the same buffers
and input seed. Every physical reference counter matches, and every complete
output, source/destination guard and RAM-published result passes independently.
All six fresh-repeat record comparisons are identical.

The primary runs both methods; the secondary stays reset in this latency
experiment. The common window includes dispatch, descriptor/driver setup,
copy, polling and completion/fence costs, excluding preparation, validation,
UART output and final global stop. Buffers are deterministically rewritten
before each method: this is prepared cache state, not a cold-cache experiment.
The CPU baseline is the audited freestanding optimized word-copy kernel,
not an artificially volatile byte-only loop or a claim about every libc.

| Alignment / caches | First all-pairs DMA win | Later sampled reversal | All sampled sizes win from, through 8 KiB | CPU cycles / DMA cycles at 8 KiB |
| --- | ---: | ---: | ---: | ---: |
| Aligned / off | 63 B | 64 B | 127 B | 1.645768× |
| Aligned / on | 255 B | 256 B | 511 B | 1.103809× |
| Same offset / off | 31 B | none | 31 B | 1.653920× |
| Same offset / on | 128 B | none | 128 B | 1.109817× |
| Different offsets / off | 8 B | none | 8 B | 3.967321× |
| Different offsets / on | 63 B | none | 63 B | 1.622970× |

These are sampled boundaries, not an interpolated universal crossover. The
aligned 64-byte/cache-off case measures CPU 1,204 versus DMA 1,443 cycles:
**0.834373× is a genuine DMA slowdown**. Alignment paths, cache behavior,
coherent maintenance and fixed setup costs remain in the results. Polling
occupies the CPU; faster copying is not proof of freed CPU time. See the
[full experiment and interpretation](../../../phase7-bench.md).

## Separate physical correctness proof

Four packages (`runtime-c0`, `publication-c0`, `runtime-c1`, `publication-c1`)
contain two warm boots each, separate from benchmark counts. The four runtime
boots execute **1,280 directed copies, 24 LR/SC interactions, 32 selective
secondary-reset epochs and 12 two-hart publication jobs**. Every runtime boot
records 339 successful descriptors, one bounded-prefix abort and six invalid
descriptor errors. The four code boots add **32 real eight-byte DMA jobs**
and execute the published RAM instructions successfully on both actual harts.

Each boot retains complete 64-KiB stopped RAM and actual 97-byte runtime or
14-byte publication UART. Host validators check all result words, executable
bytes and guards, CPU/DMA counter banks and frozen host DMA diagnostics.
Simulation independently observes every architectural store and RAM-code
retirement; the physical acceptance does not invent a physical PC trace or
claim serial-inclusive functional cycles equal faster-baud simulation.

Linux/PCAP deployment uses verified-key SSH and guards the exact previous
bitstream path/hash, HWH/AXI map, clock and idle state before programming.
Actual PL UART TX-to-RX is captured through AXI/SSH; this is **not an external
Pmod electrical test**. ARM writes only established RUN/STOP and stopped ROM,
never copy descriptors or payload buffers. Paired boots reuse the image
without another download. The complete study switches cache modes once;
the later functional sequence downloads cache-off then cache-on, reusing each
image for code publication after its runtime proof.

An independent post-study observation and [final read](physical/final_state.json)
bind that programming chain. At **2026-09-13 17:20:50 UTC**, the final image
reports 31.25 MHz, two harts, ABI `0x70001`, features 7, empty FIFO,
`CONTROL/STATUS/HART_STATUS/STOP_STATUS = 0/0/0/1`, and zero DMA status, bytes,
cycles, counting flag and all 14 counters. Linux remained available; root SSH
closed normally. No JTAG, ARM reset, SD/QSPI or unrelated project was touched.

## Routed FPGA signoff

Both actual bitstream/HWH pairs are included. Vivado 2025.1 at **31.25 MHz**
passes generated reset-netlist simulation (five scenarios per image), clock/
reset/ABI HWH checks and routed setup/hold/pulse-width, routing, DRC and
methodology gates.

| Configuration | Setup / hold slack | LUTs | Flip-flops | BRAM tiles | DSPs |
| --- | --- | ---: | ---: | ---: | ---: |
| Caches off | 6.455 / 0.024 ns | 16,107 | 12,119 | 32 | 0 |
| Coherent caches on | 6.872 / 0.013 ns | 19,562 | 16,514 | 32 | 0 |

Both pulse-width slacks are 14.750 ns. There are zero unconstrained internal
endpoints and zero routing/DRC/methodology findings. Five deliberate asynchronous
output ports have no output delay; external wiring is not claimed fully
constrained. Positive routed slack does not establish a higher operating Fmax.

## Scope and limitations

The autonomous engine is an explicit `ENABLE_DMA=1` elaboration choice with
sticky pollable completion/error status at `0x30000000`. It supports all byte
alignments within shared RAM, rejects nonzero overlaps and checks complete
ranges before I/O. It snoops dirty CPU source lines and drains/invalidates
destination lines without allocating a device cache. Atomic CPU groups stay
indivisible; accepted DMA writes invalidate matching reservations. Abort and
selective/global STOP preserve exactly the admitted effects and acknowledged
RAM. This is not scatter-gather, AXI DDR DMA, a whole-buffer atomic snapshot
or permission to race C accesses against DMA-owned buffers.

Regression and host validators test missing/reordered/duplicated cases,
changed seeds/geometry, invalid atomic/reset evidence, full bytes/guards,
counter/observer disagreement, ELF/ROM/source/tool identity, unsafe deployment,
changed programming chains and invented closeout summaries. Synthetic fixtures
test the validators; they are never substituted for hardware runs. Emitted
scenario counts are not formal ISA certification or exhaustive state coverage.

Separately, all 22 actual legacy logs and 14 actual DMA logs were accepted,
then missing-gate, duplicate-gate and late-failure versions of every log were
rejected: **66 legacy and 42 DMA damaged-log checks**. Original raw captures
were never edited to perform those in-memory negative tests.

The RISC-V runtime remains bare metal. Shared L2, interrupts, OS/MMU, clock
optimization and README Phase 8+ are deferred. The next README phase is a
packed INT8 dot-product/MAC custom instruction with a scalar reference; no
Phase 8 implementation is included here.
