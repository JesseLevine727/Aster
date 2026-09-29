# Aster

**A performance-driven, heterogeneous RISC-V SoC research project.** Aster
compares scalar CPU, multicore CPU, custom-instruction, DMA, and INT8 accelerator
execution on real workloads, then validates a selected design on FPGA and in a
SKY130 ASIC flow.

Aster is inspired by heterogeneous-compute systems, not an attempt to reproduce
an Apple A-series processor. Its research contribution is the architecture and,
equally, a fair and reproducible method for deciding where work should execute.

## Current status — 29 September 2026

### Aster v1: functionally verified reference system

The v1 RTL integrates two PicoRV32 RV32IMA harts, private instruction/coherent
data caches, serialized atomic/coherence service, optional L2, DMA, Xasterdot8,
a 4×4 signed-INT8 NPU, timer, interrupts, and performance counters. PicoRV32 is
a small, multi-cycle reference core (about 5.4–5.7 cycles per instruction on
the coherent-SoC reduction workload); it is **not** a high-performance CPU. The v1
system is the correctness and workload-placement reference for v2.

The all-engine PYNQ-Z1 v1.3 overlay closes timing at **50 MHz** and has physical
workload evidence. Earlier phases also retain large correctness, regression,
and workload studies. The results are indexed below; each phase has its own
configuration and acceptance contract.

### Phase 16: ASIC attempt, not clean signoff

The SKY130/LibreLane flow produced a routed GDS for a reduced **16 KiB ROM +
16 KiB RAM** cut, signed off with a **47 ns** clock. It is not a clean
implementation:

- setup fails at `max_ss` (−1.154 ns, 67 endpoints); hold fails at `nom_ff`,
  `max_tt`, and `max_ff` (worst −0.171 ns);
- TritonRoute reports **106** routing DRC errors, and STA reports 85,996
  max-slew and 5,443 max-capacitance violations at the worst corner;
- LVS fails: the netlists do not match, and the top-level `vccd1` pin resolves
  to the `vssd1` node (the SRAM macro supply is not connected as intended);
- one of the eight mandatory gate-level workloads passed at full size and one
  more at a reduced shape.

Several published Phase 16 figures were also mislabelled — for example, the
70.1 mW power figure is the fast `max_ff` corner, not typical (62.1 mW) — and
were corrected in Phase 17. The rewritten audit reports Phase 16 as incomplete
(4 of 11 contract gates) and the minimal Phase 15 flow as incomplete (6 of 8:
its gate-level SDF annotation left most timing paths unmatched and its SPEF is
not hash-bound). The original 50 MHz target and full memory
configuration did not close. No Aster chip has been fabricated. See the
[Phase 16 status](docs/phase16.md), [saved report](docs/results/phase16/REPORT.md),
and the [Phase 17+ plan](docs/phase17-plus.md).

## What limits v1

The v1 tests establish that the blocks work. They do not make the SoC fast, and
the reasons are architectural, not the process node:

- **One memory transaction at a time, chip-wide.** Both harts, instruction
  refills, atomics, DMA, and the NPU share one serialized fabric → arbiter →
  cache-controller → memory path. The data cache sits below that fabric, so even
  a cache hit costs about seven cycles.
- **A multi-cycle CPU.** PicoRV32 needs several cycles per instruction before
  any memory stall is added.
- **A starved accelerator.** Each NPU operand byte is a separate 32-bit read
  through the shared path, each 32-bit result is written as four byte stores, and
  operand addresses come from combinational 32×32 multiplies (also the v1.3 FPGA
  critical path). Inside NPU jobs the array computes in under 1% of busy cycles
  (16 of 2,492 cycles for a 4×4×16 GEMM; 124 of 14,608 for 8×8×31). The headline
  Conv2D shape (M=784, N=1, K=25) also activates only 4 of the 16 PEs.

A faster CPU alone would not fix this. v2 redesigns the CPU together with its
memory interface, and gives the accelerator a data path that can feed it.

### Measurement corrections

The published Conv2D ratios compared a scalar run on `aster_minimal` with
DOT8/NPU runs on the coherent SoC, and every simulation used asynchronous,
zero-wait memory that no physical target has. On one coherent SoC with the same
input, the NPU is **2.06×** and DOT8 **0.96×** relative to scalar (not 1.24× and
0.59×). The v10 `dma_bytes` field also counted NPU stores, and
`accelerator_cycles` reported only the last NPU job. Phase 17-A1–A4 fixed the
attribution in RTL, added independent scoreboards, and moved the coherent
workloads to corrected v11 records; the v11 Conv2D record shows the NPU array
computing in 0.42% of the workload's cycles.

## v2 performance targets

The [Phase 17+ plan](docs/phase17-plus.md) defines methods and exit criteria.
The targets below were frozen on 29 September 2026, with a 96 KiB unified on-chip
SRAM ([memory decision](docs/phase17-memory.md)) and the approved
[Aster core specification](docs/cpu.md). They are goals to verify, not claims
about the current design.

| Target | Goal |
| --- | --- |
| Operating frequency | **100 MHz** post-route on PYNQ-Z1 and a 100 MHz SKY130 design target, with positive setup/hold slack at all required corners. Feasibility is an early gate; functional overclocking alone does not count. |
| CPU | **Aster's own in-order RV32IMA core** (five-stage pipeline, designed and verified in Phase 18) with at least **2× fewer cycles** than v1 PicoRV32 on the fixed CPU-bound test set at the same clock and memory configuration. PicoRV32 remains the v1 reference point. |
| INT8 NPU | 4×4 array: **3.2 GOPS theoretical peak at 100 MHz** when one MAC counts as two operations; at least 50% peak on predeclared dense GEMM shapes. Report MAC/s, utilization, and end-to-end time as well as GOPS. |
| Multicore / offload | Measure scaling and crossovers end-to-end. Large GEMM and the selected MLP should benefit from the NPU; small jobs are allowed to lose and must remain in the results. |
| Physical quality | Timing closure, zero routing/foundry DRC, zero LVS mismatch, zero antenna and electrical (slew/capacitance/fanout) violations, and workload-specific energy evidence. Memory capacity and die-area budget must be chosen together. |

100 MHz is **not a limit imposed by the 130 nm node**; many 130 nm designs run
faster. A 100 MHz FPGA pass would be a strong milestone, but it would not imply
100 MHz ASIC closure. The FPGA and ASIC use different logic cells, SRAMs, clock
trees, and routing. The current FPGA design has 50 MHz routed timing signoff; its
earlier 100 MHz test was functional on a limited workload but did not close
static timing. The current SKY130 critical path is far from a 10 ns period.

The SKY130 SRAM macro is itself a first-order constraint. Its liberty model
exists only at the typical corner and launches read data from the falling clock
edge, leaving roughly half of a 10 ns cycle for logic after a read. Each 2 KiB
macro also occupies about 0.285 mm². Phase 18 will measure the architecture and
area changes needed. Only positive setup and hold slack at the required ASIC
corners establishes 100 MHz SKY130 closure.

## Research questions

1. When does a single CPU, two CPUs, DOT8, DMA, or the NPU win for the same work?
2. How do working set, access pattern, bank conflicts, and SRAM latency affect
   throughput?
3. What are the real cache capacity, coherence, false-sharing, and L2 trade-offs?
4. At what transfer size and alignment does DMA beat CPU copying, and is CPU time
   actually available for other work?
5. How do NPU geometry, tile shape, operand reuse, and memory bandwidth affect
   useful PE utilization and performance per area?
6. Does the heterogeneous ECG pipeline overlap work and meet a stated throughput
   or deadline target?
7. How do conclusions change between PYNQ FPGA and SKY130, using the same
   workload definitions and valid timing/power evidence?

## Workloads retained for v2

| Area | Workloads and methods |
| --- | --- |
| CPU | CoreMark under official timing rules, Dhrystone, sort/search, integer kernels |
| Memory | `memcpy`, sequential, strided, random/pointer-chase walks, working-set and latency sweeps |
| Multicore/coherence | Reductions, GEMM, producer/consumer, SPSC, ping-pong, atomics, false-sharing and padded controls |
| DSP | Dot product, FIR, FFT, direct and im2col Conv2D; scalar, multicore, DOT8, and NPU where applicable |
| Machine learning | Frozen MNIST MLP and tiny CIFAR-10 CNN, with accuracy and latency reported together |
| Streaming system | Real MIT-BIH ECG segment through CPU, DMA, filtering, feature processing, and classification |
| Data movement | Paired CPU/DMA transfers over size, alignment, cache, and memory-latency sweeps |

No workload is dropped because an accelerator loses. Correctness uses an
independent reference; performance includes both kernel-only and end-to-end
measurements. All comparable engine paths use the same image, input, memory
configuration, cache policy, compiler settings, and clock.

## Roadmap

| Phase | Focus | Exit condition |
| --- | --- | --- |
| 17 | Correct the v1 measurements (v11 records, one same-top baseline), correct Phase 15/16 reporting and audits, choose the memory/area point, freeze the v2 contract and CPU specification | v11 records and the retained v1 baseline reproduce; audits enforce their gates; targets, memory point, and CPU specification are frozen. |
| 18 | **Aster core:** design and verify our own five-stage RV32IMA CPU together with its L1/SRAM interface; early 100 MHz feasibility | Lockstep-verified against an independent reference model; ≥2× fewer cycles than PicoRV32 on the CPU set; block timing meets 10 ns on FPGA and SKY130, or the limiting path and its cost are quantified. |
| 19 | NPU utilization and data movement | Local operand buffers, full-word transfers, pipelined address generation, cumulative counters; dense-GEMM utilization and end-to-end targets pass. |
| 20 | Integrate CPU, multicore, caches, DMA, DOT8, NPU, and all workloads | Full workload matrix passes independent correctness and performance audits on the same declared configurations. |
| 21 | PYNQ-Z1 implementation and physical workload study | All-engine overlay closes at 100 MHz and repeated physical captures match independent references. |
| 22 | SKY130 implementation, STA, physical signoff, and workload PPA | Declared memory fits; all-corner timing and clean physical/electrical signoff pass; workload power/energy is activity-based. |
| 23 | Tapeout (optional stretch) | Only considered after Phase 22 artifacts and signoff are reproducible. |

The [detailed Phase 17+ plan](docs/phase17-plus.md) specifies the diagnosis,
CPU specification, workload matrices, record contents, verification levels,
frequency feasibility checks, and phase-by-phase acceptance gates. It was
revised on 29 September 2026; its final section records what changed and why.
**Phase 17 is complete** ([TODO and exit gate](docs/phase17-todo.md)): corrected
[AsterBench v11](docs/asterbench-v11.md) records, a
[retained same-top v1 baseline](docs/results/phase17/baseline-9b9c94f58a85/README.md)
with a drift-rejecting audit, corrected Phase 15/16 reporting and audits, the
96 KiB memory decision, and the approved [Aster core specification](docs/cpu.md).
**Phase 18 — designing and verifying the Aster core — is next.**

## Historical phase links

The phase contracts retain links to the former README roadmap headings. These
anchors keep those historical links working while the current plan lives in
`docs/phase17-plus.md`.

<a id="phase-7--dma"></a>**Phase 7 — DMA:** [contract](docs/phase7.md) · [results](docs/results/phase7/closeout-888c24b/README.md)

<a id="phase-8--custom-compute-instruction"></a>**Phase 8 — DOT8:** [contract](docs/phase8.md) · [results](docs/results/phase8/closeout-5b9c175/README.md)

<a id="phase-9--matrix-accelerator--npu"></a>**Phase 9 — NPU:** [contract](docs/phase9.md) · [results](docs/results/phase9/closeout-2493435/README.md)

<a id="phase-10--cpu-vs-multicore-vs-isa-vs-npu"></a>**Phase 10 — compute placement:** [contract](docs/phase10.md) · [results](docs/results/phase10/README.md)

<a id="phase-11--quantized-ml-inference"></a>**Phase 11 — quantized ML:** [contract](docs/phase11.md) · [results](docs/results/phase11/README.md)

<a id="phase-12--real-time-heterogeneous-demo"></a>**Phase 12 — streaming ECG:** [contract](docs/phase12.md) · [results](docs/results/phase12/closeout-f1f62e2/README.md)

<a id="phase-15--learn-sky130-on-a-minimal-configuration"></a>**Phase 15 — minimal SKY130 flow:** [contract](docs/phase15.md) · [results](docs/results/phase15/closeout-92bb0e26f53c/README.md)
<a id="phase-16--full-asic-implementation-and-ppa"></a>**Phase 16 — full-system ASIC attempt:** [contract/status](docs/phase16.md) · [report](docs/results/phase16/REPORT.md)

## Architecture and evidence

- [v1 architecture baseline and memory map](docs/architecture.md) (later phase
  contracts supersede portions of this historical document)
- [Frozen v1 interfaces](docs/v1.md)
- [Known limitations](docs/known-limitations.md)
- [Runtime guide](docs/runtime.md)
- [Toolchain and build targets](docs/toolchain.md)
- [Verification strategy](docs/verification.md)
- [AsterBench workload definitions](docs/workloads.md)
- [Phase 10 cross-engine study](docs/results/phase10/README.md)
- [Phase 11 quantized ML results](docs/results/phase11/README.md)
- [Phase 14 design-space results](docs/results/phase14/closeout-b050a81/README.md)
- [Phase 15 minimal SKY130 flow](docs/results/phase15/closeout-92bb0e26f53c/README.md)
- [Phase 16 physical/PPA report](docs/results/phase16/REPORT.md) — read together
  with the incomplete-signoff status in [docs/phase16.md](docs/phase16.md)

Historical evidence bundles retain their original source revisions. A historical
`make check` pass or a closeout bundle does not certify later source changes.

## Quick start

```sh
make tools
make check
make workloads
```

`make check` is the fast default regression (about five minutes on the
development host), not the full research campaign. Use the phase-specific
study/capture/audit commands to reproduce fixed matrices and physical
experiments. For example:

```sh
make xe-matrix
python3 scripts/xe_study.py plan
make fpga-linux-xe
```

Generated builds live under `build/`; physical FPGA and ASIC tools are separate
from the portable host/Verilator checks. See `docs/toolchain.md` before running
board or physical-design flows.
