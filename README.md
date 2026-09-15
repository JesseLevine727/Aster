# Aster

**An open, Apple-Silicon-inspired heterogeneous RISC-V SoC — from simulation to FPGA to SKY130 ASIC.**

Aster is a learning and research project to design a small heterogeneous system-on-chip around open RISC-V CPUs, a memory hierarchy, DMA, custom compute instructions, and an INT8 neural-network accelerator. The design will be developed incrementally in RTL, verified in simulation, validated on a PYNQ-Z1 FPGA, characterized with a purpose-built benchmark suite, and ultimately taken through an open-source SKY130 ASIC physical-design flow.

The goal is **not to reproduce an Apple A-series processor**. Modern Apple silicon is the architectural inspiration: general-purpose CPUs coexist with specialized hardware, and workloads are placed on the compute engine best suited to them. Aster asks how much of that heterogeneous-computing philosophy can be explored in a small, understandable, open design.

> **Core research question:** When should a workload execute on a scalar CPU, across multiple CPU cores, through an ISA-level accelerator, or on a dedicated hardware accelerator?

## Current status

Phase 1 is implemented and verified: PicoRV32 RV32IM executes bare-metal C
using 64 KiB ROM, 64 KiB RAM, a RAM stack and UART. The closeout regression
checks 1,584 generated ISA vector groups, load/store lanes, control flow,
memory-map boundaries, RAM execution, initialized data, nonzero initial RAM,
warm-reset startup, linker limits and 12 trap scenarios. It runs with caches
off/on and asynchronous/synchronous memory using `make phase1-matrix`.

Phase 3 measurement closeout is verified: non-trapping RVFI retirement, a
common frozen counter window, separate CPU/backing-memory traffic, strict
AsterBench v2 records and reproducible capture/comparison with source and
toolchain provenance. [Saved clean-revision results](docs/results/phase3/README.md)
include all cached/uncached and async/sync baseline combinations.

Phase 2 is physically verified on PYNQ-Z1 through PYNQ Linux: Hello, a
1,060-byte UART stress stream and three AsterBench workloads each pass two
warm boots. Every benchmark field matches its Verilator reference. The first
attempt exposed a reset-polarity defect in the FPGA shell; it is fixed and
guarded by generated-netlist simulation and pre-download handoff checks.
[Physical records and routed reports](docs/results/phase2/README.md) distinguish
real FPGA serial TX/RX captured over AXI/SSH from external Pmod wiring, which
was not tested.

Phase 4 closeout is verified: 108 seeded cache scoreboard runs across 36
geometries, a 24-configuration SoC/latency matrix, and all four README cache
experiments. [Retained clean-revision results](docs/results/phase4/README.md)
cover 60 configurations plus a repeat, with correctness/provenance checks and
analysis of both cache benefits and slowdowns. Phases 1–4 are complete;
[`docs/phase-closeout.md`](docs/phase-closeout.md) maps the acceptance contract
to retained evidence.

Phase 5 is complete: two independent PicoRV32 RV32IM harts execute a protected,
separate-stack C runtime and a correctly checked parallel workload on the
PYNQ-Z1 through Linux/PCAP. Private I/D caches, uncached shared RAM, round-robin
arbitration, hart lifecycle and polling mailboxes follow the
[dual-hart contract and acceptance audit](docs/phase5.md). Strict AsterBench v3
records use common measurement windows, per-hart counters and full provenance.

[Retained physical evidence](docs/results/phase5/closeout-71e2570/README.md)
contains 18 warm boots, 48 benchmark jobs, clean FPGA signoff and complete
legacy/multicore regression matrices. The default 64-word workload measures
**1.973× physical speedup**, including dispatch/copy/completion overhead;
small-job overhead and cache-dependent scaling remain visible. Older captures
retain their original source/timing meaning. `make linux-dual-sim` tests the
new bridge; `make fpga-linux-dual` builds its overlay. Original `fpga` and
`fpga-linux` targets preserve the single-core map. Phase 6 is now complete:
the [coherence and full RV32A contract](docs/phase6.md) defines staged PCPI,
atomic-memory, MSI-like cache, runtime, benchmark and physical acceptance gates.
Legacy builds remain RV32IM. The new RV32IMA bring-up passes
independent atomic-fabric tests and compiled one-/two-core C runtime tests.
The new MSI-like D-cache controller also passes geometry, dirty-data, flush and
real-core atomic tests. The new SoC passes RAM-preserving warm-stop and repeated
secondary-reset tests. The Phase 6 AXI bridge and stopped-only host helpers pass
serial, RAM-retention and protocol tests. AsterBench v4 now exercises nine
atomic/coherent workloads with exact simulated per-hart event scoreboards and
independent RAM results. Both cache-off/on overlays pass clean-source routed
signoff. The complete physical Linux/PCAP study now passes all 57 captures,
114 warm boots and 342 jobs, matching every reference counter with full UART
and stopped-RAM evidence. Full-A C runtime and selective-reset lifecycle tests
also pass two physical boots each with caches off and on. The matching
clean-source simulation study and strict 22-target regression audit pass.
The [self-contained Phase 6 evidence](docs/results/phase6/closeout-215b2d0/README.md)
passes all seven requirement audits and a fresh clean rebuild with 84 host
tests. Its 835 hash-checked artifacts include both actual bitstreams and full
raw regression/firmware/physical records. Phase 7 (DMA) is also complete:
coherent autonomous copies, a RAM-backed C driver and AsterBench v5 size
experiments pass all seven [acceptance gates](docs/results/phase7/closeout-888c24b/README.md).
The 2,003-artifact bundle retains all 22 legacy and 14 DMA regression targets,
144 physical benchmark captures, eight additional functional board boots,
both routed 31.25 MHz overlays and a fresh rebuild with 149 host tests.
The [current runtime guide](docs/runtime.md) covers the coherent RV32IMA/DMA
configuration separately from the preserved legacy maps. Phase 8 is complete:
the packed signed INT8 instruction, RAM-backed runtime, full regressions, routed
overlays and guarded PYNQ Linux acceptance all pass the [immutable closeout
audit](docs/results/phase8/closeout-5b9c175/README.md). Phase 9 is complete: the
4×4 signed-INT8 GEMM matrix accelerator runs a RAM-backed C runtime on the
coherent RV32IMA/DMA SoC, passes AsterBench v7 with an independent oracle, and is
physically verified through PYNQ Linux/PCAP in both cache modes at 31.25 MHz.
The [Phase 9 contract](docs/phase9.md) and [immutable evidence
bundle](docs/results/phase9/closeout-2493435/README.md) retain the 16-case
actual-core matrix, routed overlays and physical records.

Phase 10 (CPU vs multicore vs ISA vs NPU) is complete under the
[Phase 10 contract](docs/phase10.md): identical signed-INT8 dot, FIR and GEMM
kernels run through the scalar CPU, two coherent harts, the Xasterdot8
instruction and the NPU on one all-engine SoC image. The fixed 288-capture
AsterBench v8 study passes with two fresh repeats, per-engine FPGA utilization
and routed timing are measured, and six physical Pynq-Z1 captures at 31.25 MHz
match the simulation ratios. The [self-contained closeout
bundle](docs/results/phase10/closeout-8371c3d/README.md) retains the raw records,
routed reports and physical evidence and passes `scripts/audit_phase10.py`.

Phase 11 (quantized ML inference) is complete under the
[Phase 11 contract](docs/phase11.md): a frozen INT8 MLP `784→32→10` runs end to
end on the coherent RV32IMA/NPU SoC with the CPU owning control,
requantization, activation and classification and the NPU owning the matrix
multiply. All four execution paths (scalar, multicore, DOT8, NPU) are bit-exact
against an independent integer reference, and the four-path study and physical
captures agree (NPU ≈4.5× the scalar baseline). The [closeout
bundle](docs/results/phase11/closeout-ff56683/README.md) passes
`scripts/audit_phase11.py`.

Start here:

```sh
make check
```

See [`docs/architecture.md`](docs/architecture.md) for the current contract,
[`docs/toolchain.md`](docs/toolchain.md) for setup and build targets, and
[`docs/verification.md`](docs/verification.md) for the test strategy. The
PicoRV32 is pinned under `vendor/` and integrated through an Aster-owned
wrapper, so the CPU can be replaced later without rewriting the SoC fabric.

Run `make bench` to execute the comparable benchmark record directly in
Verilator.

---

## Vision

```text
                    ASTER SoC

       +-------------+   +-------------+
       | RISC-V CPU 0|   | RISC-V CPU 1|
       +------+------+   +------+------+
              |                 |
          L1 I$ / D$        L1 I$ / D$
              |                 |
              +--------+--------+
                       |
                 Shared L2
                       |
                System Fabric
          +------------+------------+
          |            |            |
         DMA       INT8 NPU     Peripherals
          |        / MAC array   UART/Timer
          +------------+------------+
                       |
                     Memory
```

A reasonable frozen v1 target is:

- 2 × RV32IM in-order RISC-V cores
- Per-core L1 instruction and data caches
- Shared L2 cache
- Two-core cache coherence
- DMA engine
- Custom packed INT8 dot-product instruction(s)
- Small INT8 matrix/NPU accelerator (initially 4×4, potentially 8×8)
- Interrupt controller, timer, UART and performance counters
- Bare-metal software stack
- PYNQ-Z1 FPGA implementation
- SKY130 synthesis and physical design
- Reproducible performance/area/power experiments

Linux, out-of-order execution, a GPU, a large NoC, RV64 and high core counts are deliberately **not v1 requirements**. Scope discipline is part of the project.

---

## The idea

The interesting part of a modern SoC is not simply the number of CPU cores. It is the interaction between general-purpose compute, specialized compute, memory, data movement and software.

Aster is intended to make those trade-offs measurable. The same operation — for example matrix multiplication — can eventually run in four ways:

```text
                         GEMM
                          |
          +---------------+---------------+
          |               |               |
     Scalar CPU       2 CPU cores      DOT8 ISA
                                              |
                                              +---- Dedicated NPU
```

Rather than assuming specialization is always better, Aster will measure **where each approach wins and where its overhead makes it lose**.

## Questions Aster should answer

- How well does performance scale from one to two cores?
- When does memory bandwidth become the bottleneck?
- How much do L1/L2 cache sizes affect real workloads?
- What is the cost of coherence and false sharing?
- At what transfer size does DMA become preferable to CPU copying?
- When is a custom RISC-V instruction enough, and when is a separate accelerator justified?
- At what problem size does NPU offload overcome setup and data-movement overhead?
- How do accelerator dimensions affect utilization, area and performance?
- Which architecture provides the best performance per area and estimated energy?
- How different are the conclusions on FPGA versus SKY130?

---

## Workloads

### CPU baseline

CoreMark, Dhrystone, integer kernels, sorting/search and small general-purpose C programs establish CPU correctness and baseline performance.

### Memory hierarchy

`memcpy`, sequential and strided access, random access, pointer chasing and working-set sweeps characterize memory latency, bandwidth and cache behavior.

### Multicore and coherence

Parallel GEMM, reductions, producer/consumer queues, shared atomic counters, cache-line ping-pong, false sharing and synchronization tests measure scaling and deliberately torture coherence.

### DSP

Dot product, FIR filtering, FFT and convolution provide useful targets for scalar code, custom packed operations and streaming execution.

### Machine learning

INT8 GEMM, INT8 Conv2D, a tiny fully connected network, MNIST inference and eventually a small CIFAR-10 CNN exercise the dedicated accelerator.

### Full-system streaming workload

A final demonstration will combine multiple engines concurrently:

```text
ECG samples -> DMA -> memory -> FIR/DSP -> feature processing -> NPU -> result
                         |                         |
                       CPU 0                    CPU 1
```

This demonstrates the real objective: making a heterogeneous system cooperate on a useful workload.

---

## Datasets

### MNIST

The first end-to-end ML target. Its 28×28 grayscale inputs and simple models are ideal for validating quantized inference and accelerator/software correctness.

### CIFAR-10

A later, more demanding image-classification workload. A small quantized CNN can stress Conv2D, memory reuse and accelerator utilization more realistically than MNIST.

### PhysioNet ECG

An open ECG dataset can provide a real-world streaming signal-processing workload involving DMA, filtering/feature processing and inference.

### Synthetic benchmark data

GEMM, FIR, FFT, cache and coherence tests will use deterministic generated inputs, fixed seeds and known reference outputs for reproducible regression testing.

---

## Development philosophy

> **Never add several major unverified subsystems at once.**

Every stage must boot, execute its tests and pass regressions before the next architectural feature is introduced. A one-core system that is completely understood is more useful than a four-core/NPU system whose failures cannot be isolated.

---

# Roadmap: zero to silicon

## Phase 0 — Specification and toolchain

Define the memory map, ISA target, coding conventions, interfaces and v1 scope. Establish Verilator, waveform viewing, RISC-V GCC/binutils and automated builds/tests.

**Exit:** RTL can be simulated and a bare-metal RISC-V binary can be built reproducibly.

## Phase 1 — Minimal single-core computer

Bring up one RV32I/RV32IM core with ROM/RAM and UART. A proven open core can initially be used so SoC infrastructure is developed independently of CPU microarchitecture.

**Exit:** a compiled program executes and prints `Hello from Aster` in simulation.

## Phase 2 — Early PYNQ-Z1 bring-up

Put the minimal CPU + BRAM + UART system on the FPGA immediately.

**Exit:** Aster executes real RISC-V firmware on PYNQ-Z1 and communicates with the outside world.

## Phase 3 — AsterBench and performance counters

Create the benchmark framework early. Add counters for cycles, retired instructions, cache accesses/misses, memory transactions, DMA bytes and accelerator cycles.

**Exit:** results are emitted in a machine-readable format and can be compared across RTL revisions.

## Phase 4 — L1 caches

Add simple instruction and data caches and verify them thoroughly.

**Experiments:** no-cache vs cache, working-set sweeps, sequential vs random access and cache-size sensitivity.

## Phase 5 — Second RISC-V core

Add hart IDs, startup/reset, synchronization and inter-core signaling.

**Exit:** both cores execute independently and a parallel workload produces the correct result.

## Phase 6 — Coherent memory

Introduce a simple two-core snooping coherence protocol, such as MSI/MESI-like behavior, and eventually a shared L2 where appropriate.

**Tests:** ping-pong, producer/consumer, atomics, false sharing and adversarial ownership transitions.

The active [Phase 6 contract](docs/phase6.md) adds full RV32A (LR/SC and all word
AMOs) through Aster-owned PicoRV32 integration. Shared L2 is deferred; coherent
shared RAM, ordering/fault/reset correctness and physical validation are not.
**Complete:** [full-A/coherence acceptance and reproducible physical evidence](docs/results/phase6/closeout-215b2d0/README.md).

## Phase 7 — DMA

Build a memory-to-memory DMA engine with source, destination, length, start/status and completion signaling.

**Experiment:** CPU `memcpy` vs DMA across increasing transfer sizes to identify the crossover point.

**Complete:** [seven-gate acceptance and immutable evidence](docs/results/phase7/closeout-888c24b/README.md).
The [Phase 7 contract](docs/phase7.md) implements DMA in the reserved
`0x3000_0000` register page, participates in coherent shared-RAM access and
retains the verified 31.25 MHz baseline. Completion is pollable; interrupts,
shared L2 and Phase 8+ remain outside this phase.
The [paired AsterBench v5 tools](docs/phase7-bench.md) now verify actual CPU/DMA
copies, full outputs, counters and saved provenance. The clean fixed simulation
study passes 144 captures / 288 warm boots / 1,152 paired jobs, and both
31.25 MHz DMA overlays pass routed/reset/HWH signoff. All 144 physical benchmark
captures pass the complete physical/Git audit with exact reference counters.
Eight separate functional board boots also pass directed runtime, atomic/reset
interactions and DMA-copied RAM-code publication. The final independent read
confirms safely stopped CPUs/DMA and Linux available. All 22 legacy and 14 DMA
regression targets pass their scenario/provenance audits, and the fresh rebuild
passes 157 emitted scenarios and 149 host tests. All seven combined requirements
pass against 2,003 hash-checked artifacts.
The DMA-enabled Linux shell also passes actual-core AXI/serial runtime,
RAM-code publication and safe-stop tests; its v5 benchmark has separate
freeze-time CPU/DMA observations and physical-baud simulation coverage.
Clean cache-off/on functional references retain actual ELF/ROM/UART/RAM and
independent event/retirement evidence for both C programs over eight warm boots.
The [physical workflow](docs/phase7-physical.md) uses guarded Linux/PCAP only.
At 8 KiB with caches enabled, measured CPU cycles / DMA cycles is **1.104×
aligned** and **1.623× with different offsets**. Small transfers can be slower
with DMA; the report retains alignment/cache-specific sampled crossover points
and reversals. Polling does not demonstrate freed CPU time.

## Phase 8 — Custom compute instruction

Add a packed INT8 dot-product/MAC-style RISC-V extension with software support and a scalar reference implementation.

**Experiment:** scalar dot product/FIR/GEMM vs custom instruction.

**Complete:** [Phase 8 contract and results](docs/phase8.md). Xasterdot8
computes four signed INT8 products into a 32-bit result through the pinned
PicoRV32 PCPI path, with a RAM-backed C runtime and explicit modulo-2^32
accumulation. The fixed AsterBench v6 study covers 174 simulation captures and
the matching 174-capture PYNQ Linux study; both cache modes, full regressions,
routed/reset/HWH signoff, guarded functional boots and the final stopped-state
audit pass at 31.25 MHz. The [self-contained evidence bundle](docs/results/phase8/closeout-5b9c175/README.md)
retains the raw records and provenance. Phase 9's NPU remains separate.

## Phase 9 — Matrix accelerator / NPU

**Complete:** [Phase 9 contract and results](docs/phase9.md). Build from small verified
pieces: processing element → array → 4×4 MAC array → memory/control interface
→ coherent SoC integration → RAM-backed C runtime → guarded PYNQ Linux/PCAP.
AsterBench v7 remains mandatory; it adds a strict accelerator/GEMM record
version while preserving v2–v6. The [self-contained Phase 9 evidence bundle](docs/results/phase9/closeout-2493435/README.md)
retains the 100-capture study, fresh repeats, routed artifacts, physical
cache-mode boots, independent audits and full regressions.

Start with INT8 GEMM only. CNN support comes later.

**Exit:** accelerator GEMM is bit-correct against a software reference over randomized test cases, with routed and physical acceptance evidence.

## Phase 10 — CPU vs multicore vs ISA vs NPU

Run identical kernels through all execution paths and measure cycles, latency, instructions, cache behavior, memory traffic, accelerator utilization, FPGA resources and maximum clock.

This is one of Aster's central experiments.

**Complete:** the [Phase 10 contract](docs/phase10.md) fixes signed-INT8 dot,
FIR and GEMM semantics and runs them through all four engines on one all-engine
SoC image with AsterBench v8 and an independent oracle. The 288-capture study
plus two fresh repeats, per-engine FPGA utilization/routed timing and six
physical Pynq-Z1 captures are retained in the
[results bundle](docs/results/phase10/README.md).

## Phase 11 — Quantized ML inference

Deploy a small INT8 model beginning with MNIST. Map supported operations to the NPU while the CPU handles control and unsupported operations.

**Experiment:** scalar CPU vs multicore vs custom ISA vs NPU inference, including offload overhead.

**Complete:** the [Phase 11 contract](docs/phase11.md) deploys a frozen INT8 MLP
on MNIST through all four paths with an independent integer reference and
AsterBench v9. The [closeout bundle](docs/results/phase11/closeout-ff56683/README.md)
retains the model, the four-path study with repeats, the routed overlay and the
physical captures (NPU ≈4.5× scalar, DOT8 ≈2.3×, two harts ≈1.95×).

## Phase 12 — Real-time heterogeneous demo

Use a streaming dataset such as ECG and exercise CPU, DMA, DSP/custom instructions and NPU together.

**Exit:** Aster sustains the target stream in real time while reporting utilization/performance counters.

## Phase 13 — Freeze Aster v1

Stop feature development and stabilize the architecture, software, documentation and tests.

Likely freeze point: 2× RV32IM + L1s + shared L2/coherence + DMA + DOT8 + INT8 NPU + UART/timer/interrupts/performance counters.

## Phase 14 — Design-space exploration

Sweep parameters rather than adding features:

- 1 vs 2 cores
- cache capacities/organizations
- 2×2 vs 4×4 vs 8×8 accelerator
- scalar vs ISA extension vs NPU
- different working-set/problem sizes

The goal is to discover **crossovers and bottlenecks**, not simply build the largest configuration.

## Phase 15 — Learn SKY130 on a minimal configuration

Take a tiny Aster configuration through the complete open ASIC flow first:

```text
RTL -> Yosys -> SKY130 -> floorplan -> placement -> CTS -> routing -> STA -> DRC/LVS -> GDSII
```

**Exit:** a minimal configuration completes the physical-design flow.

## Phase 16 — Full ASIC implementation and PPA

Move the frozen architecture through SKY130/OpenROAD. Collect post-synthesis/post-layout area, timing and estimated power and analyze metrics such as performance/mm², energy/op and accelerator GOPS/W where meaningful.

## Phase 17 — Tapeout (stretch goal)

Fabrication is not required for project success. The primary ASIC finish line is a reproducible, timing-analyzed, DRC/LVS-clean GDSII. If an accessible shuttle is available, fabrication, packaging, board bring-up and first UART output become the final stretch goal.

---

## AsterBench

AsterBench is a first-class project component:

```text
benchmarks/
  coremark/
  dhrystone/
  memcpy/
  pointer_chase/
  fir/
  fft/
  gemm/
  conv2d/
  multicore_gemm/
  cache_pingpong/
  false_sharing/
  npu_gemm/
  mnist/
  streaming_ecg/
```

Each benchmark should report reproducible metadata and measured counters. Placeholder performance claims are not results: numbers in reports should come from simulation, FPGA measurements or the documented ASIC flow.

---

## Verification strategy

1. Unit-test ALUs, arbiters, FIFOs, cache controllers, DMA and accelerator PEs independently.
2. Compare CPU behavior against known RISC-V tests/reference models where practical.
3. Use deterministic software tests with known outputs.
4. Add assertions around protocols and invariants.
5. Add randomized tests for caches, coherence and the NPU.
6. Maintain regression tests before architectural changes are accepted.
7. Compare accelerator results against simple software golden models.
8. Re-run AsterBench after major architecture revisions.

**Correctness comes before optimization.**

---

## Proposed repository structure

```text
Aster/
├── rtl/
│   ├── core/
│   ├── cache/
│   ├── interconnect/
│   ├── memory/
│   ├── dma/
│   ├── accelerator/
│   └── peripherals/
├── verification/
│   ├── unit/
│   └── soc/
├── software/
│   ├── boot/
│   ├── drivers/
│   ├── runtime/
│   └── benchmarks/
├── fpga/
│   └── pynq_z1/
├── asic/
│   └── sky130/
├── scripts/
├── docs/
└── README.md
```

---

## What success looks like

1. **Computer:** one RISC-V core executes bare-metal software.
2. **FPGA SoC:** the system runs reproducibly on PYNQ-Z1.
3. **Multicore SoC:** two cores execute parallel workloads correctly.
4. **Heterogeneous SoC:** DMA, custom instructions and NPU cooperate with the CPUs.
5. **Research platform:** AsterBench produces reproducible architectural comparisons.
6. **ASIC implementation:** the frozen SoC completes the SKY130 physical-design flow.
7. **Stretch:** fabricated Aster silicon boots and communicates with the outside world.

---

## Potential research direction

> **Design and Evaluation of a Heterogeneous Multicore RISC-V System-on-Chip for Edge AI Acceleration**

The contribution would not simply be “a RISC-V CPU was built.” It would be the architecture plus a reproducible methodology and experimental characterization of **compute placement** across scalar CPU, multicore CPU, ISA-level specialization and dedicated acceleration under realistic memory, area and energy constraints.

---

## Inspiration and related ecosystems

Aster is inspired by the heterogeneous-compute philosophy of modern Apple silicon while remaining an independent open RISC-V project. Useful projects and ecosystems to study include RISC-V, Chipyard/Rocket/BOOM, BlackParrot, PULP/HERO, PicoRV32, Gemmini, PYNQ/Zynq, Verilator, Yosys, OpenROAD and SkyWater SKY130.

These are references and tools, not a requirement that Aster simply assemble existing projects. Mature infrastructure should be reused where it saves time, while Aster focuses implementation effort on architectural components relevant to its research questions.

---

## The journey

```text
Specification
     ↓
Single RISC-V
     ↓
Hello World
     ↓
PYNQ-Z1
     ↓
AsterBench
     ↓
Caches
     ↓
2 cores
     ↓
Coherence
     ↓
DMA
     ↓
DOT8
     ↓
NPU
     ↓
GEMM comparison
     ↓
TinyML
     ↓
Real-time ECG pipeline
     ↓
Design-space experiments
     ↓
Freeze RTL
     ↓
SKY130 / OpenROAD
     ↓
Post-layout PPA
     ↓
DRC/LVS-clean GDSII
     ↓
[Optional tapeout]
```

Aster starts with one core printing a line of text. It ends, ideally, as a measured, verified heterogeneous computer with a physical chip layout.
