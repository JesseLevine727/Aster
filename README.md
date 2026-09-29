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
a 4×4 signed-INT8 NPU, timer, interrupts, and performance counters. The PicoRV32
is a small low-area in-order reference core; it is **not** a high-performance
CPU. The v1 system is a useful correctness and workload-placement baseline.

The all-engine PYNQ-Z1 v1.3 overlay closes timing at **50 MHz** and has physical
workload evidence. Earlier phases also retain large correctness, regression,
and workload studies. The results are indexed below; each phase has its own
configuration and acceptance contract.

### Phase 16: ASIC attempt, not clean signoff

The SKY130/LibreLane flow produced a routed/GDS result for a reduced **16 KiB
ROM + 16 KiB RAM** cut. At the 47 ns signoff point the reported worst setup slack
is **−1.154 ns**; worst hold slack is **−0.171 ns**; TritonRoute reports **106**
DRC errors and LVS reports **13** errors. The original 50 MHz target and full
memory configuration did not close. No Aster chip has been fabricated.

The Phase 16 result is a useful physical feasibility experiment, but it is not a
signoff-clean implementation. Its Fmax/power derivations, workload
comparability, and closeout audit need re-baselining before those figures can be
used as v2 performance claims. See the [Phase 16 status](docs/phase16.md),
[saved report](docs/results/phase16/REPORT.md), and the
[Phase 17+ performance plan](docs/phase17-plus.md).

## Why a new performance phase is needed

The v1 tests establish that the blocks work. They do not establish that the
whole SoC has a competitive CPU, efficient accelerator dataflow, or a trustworthy
ASIC PPA result. The current implementation serializes CPU/device memory
transactions, uses a small blocking cache, and feeds the NPU through the shared
memory service. Conv2D with N=1 activates only one NPU column, and per-byte
operand/result traffic dominates its compute.

The published Conv2D cycle records also use different SoC tops and default
asynchronous memory, while the ASIC top uses synchronous memory and a reduced
memory cut. `dma_bytes` includes NPU device stores, and NPU `accelerator_cycles`
is currently a last-job value rather than a sum over the workload. These are
Phase 17 measurement-contract items, not harmless formatting details.

The next version must be designed around workload, bandwidth, timing, area, and
energy budgets from the beginning. The historical v1 implementation and its
results remain the reference; v2 performance work gets its own measured gates.

## Proposed v2 performance targets

The [Phase 17+ plan](docs/phase17-plus.md) defines methods and exit criteria.
The targets below are goals to verify, not claims about the current design.

| Target | Goal |
| --- | --- |
| Operating frequency | **100 MHz** post-route on PYNQ-Z1 and a 100 MHz SKY130 design target, with positive setup/hold slack at all required corners. Feasibility is an early gate; functional overclocking alone does not count. |
| INT8 NPU | 4×4 array: **3.2 GOPS theoretical peak at 100 MHz** when one MAC counts as two operations; at least 50% peak on predeclared dense GEMM shapes. Report MAC/s, utilization, and end-to-end time as well as GOPS. |
| CPU baseline | A measured, pipelined in-order RV32 candidate should improve cycles by at least 2× over v1 on the fixed CPU-bound test set at the same clock and memory configuration. PicoRV32 remains a reference point. |
| Multicore / offload | Measure scaling and crossovers end-to-end. Large GEMM and the selected MLP should benefit from the NPU; small jobs are allowed to lose and must remain in the results. |
| Physical quality | Timing closure, zero routing/foundry DRC, zero LVS mismatch, zero antenna violations, and workload-specific energy evidence. Memory capacity and die-area budget must be chosen together. |

100 MHz is **not a limit imposed by the 130 nm node**; many 130 nm designs run
faster. A 100 MHz FPGA pass would be a strong milestone, but it would not imply
100 MHz ASIC closure. The FPGA and ASIC use different logic cells, SRAMs, clock
trees, and routing. The current FPGA design has 50 MHz routed timing signoff; its
earlier 100 MHz test was functional on a limited workload but did not close
static timing. The current SKY130 critical path is far from a 10 ns period.
Phase 18 will measure the architecture and area changes needed. Only positive
setup and hold slack at the required ASIC corners establishes 100 MHz SKY130
closure.

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
| 17 | Repair/rebaseline measurements; freeze v2 workload, memory, frequency, area, and power contracts | Raw records and audits reproduce; no mixed-top/mixed-memory comparisons; numeric targets are frozen. |
| 18 | CPU and memory/fabric performance architecture; early 100 MHz feasibility | A measured CPU choice and memory organization meet staged timing, area, and bandwidth gates. |
| 19 | NPU utilization and data movement | Packed/banked operand delivery, cumulative counters, dense-GEMM utilization and end-to-end targets pass. |
| 20 | Integrate CPU, multicore, caches, DMA, DOT8, NPU, and all workloads | Full workload matrix passes independent correctness and performance audits on the same declared configurations. |
| 21 | PYNQ-Z1 implementation and physical workload study | All-engine overlay closes at 100 MHz and repeated physical captures match independent references. |
| 22 | SKY130 implementation, STA, physical signoff, and workload PPA | Declared memory fits; all-corner timing and clean physical signoff pass; workload power/energy is activity-based. |
| 23 | Tapeout (optional stretch) | Only considered after Phase 22 artifacts and signoff are reproducible. |

The [detailed Phase 17+ plan](docs/phase17-plus.md) specifies the workload
matrices, record contents, verification levels, frequency feasibility checks,
and phase-by-phase acceptance gates.

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

`make check` is the fast default regression, not the full research campaign. Use
the phase-specific study/capture/audit commands to reproduce fixed matrices and
physical experiments. For example:

```sh
make xe-matrix
python3 scripts/xe_study.py plan
make fpga-linux-xe
```

Generated builds live under `build/`; physical FPGA and ASIC tools are separate
from the portable host/Verilator checks. See `docs/toolchain.md` before running
board or physical-design flows.
