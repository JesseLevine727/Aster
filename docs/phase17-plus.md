# Aster v2 performance roadmap: Phase 17 and beyond

Status: **in progress — Phase 17 (measurement corrections and v2 contract)**.
Revised 29 September 2026; [section 7](#7-plan-revision--29-september-2026)
records what changed from the first draft and why. This plan defines the
performance-oriented successor to the functionally verified v1 system. It does
not retroactively alter v1 interfaces, measurements, or closeout bundles. The
live Phase 17 checklist is [`phase17-todo.md`](phase17-todo.md).

Phase 17 requires no live FPGA board access: it uses RTL/host evidence and the
retained captures. Phase 18 performs local implementation/timing feasibility
checks. The first planned physical PYNQ/Linux board session is Phase 21; SKY130
physical design is Phase 22.

The objective is to answer Aster's research questions with comparable evidence
and produce an SoC whose CPU, memory system, accelerator, timing, and physical
implementation are designed against explicit performance budgets. Passing a
functional test is necessary; it is not a performance exit criterion.

## 1. What limits v1

The v2 architecture follows from these measured or traced properties of v1.
Each is a design input, not a tuning detail.

| Property | Evidence | Consequence for v2 |
| --- | --- | --- |
| One memory transaction in flight across the SoC | `aster_atomic_fabric` serializes both harts; the device arbiter admits one of CPU/DMA/NPU; one `aster_coherent_cache` FSM serves both D-cache banks, device traffic, and flush | Per-core L1 that can hit in parallel; a memory system that allows more than one outstanding transaction |
| Cache hits are slow | The coherent D-cache sits below the fabric: a load hit costs about seven wait cycles (qualifier, fabric check, four cache states, response) | The CPU and its L1 are designed together; single-cycle L1 hits are a Phase 18 requirement |
| Multi-cycle CPU | PicoRV32 reduction workload: 53,299 retired instructions in 289,251 cycles (async memory) and 301,635 cycles (gate level, synchronous memory) — about 5.4–5.7 CPI | A pipelined CPU, measured against PicoRV32 in the same shell |
| Starved NPU | Each operand byte is a separate 32-bit read; each 32-bit result is four byte stores; about 51 cycles per 4×4 array step by FSM trace. Phase 10 records: 16 compute cycles of 2,492 busy cycles (4×4×16), 124 of 14,608 (8×8×31) | Local operand buffers, full-word transfers, operand reuse; array utilization is a gate, not a by-product |
| NPU shape mismatch | The headline Conv2D is M=784, N=1, K=25: one output column, at most 4 of 16 PEs active; batch-one MLP layers are also N=1 | The NPU needs a mapping for N=1 (for example K-split across columns) or utilization targets cannot apply to the workloads that matter |
| Combinational address generation | `a_byte_address = a_base + (tile_row + load_index) * a_stride + k_index` drives the memory request; it is the v1.3 FPGA critical path and a leading SKY130 path | Address generation is pipelined; no multiply in a request path |
| Flop-array memories | The 4,096-bit D-cache data array and the 1 KiB L2 are flip-flops with multiple dynamic read ports | Choose SRAM, latch/flop RAM, or macro per array from measured area and timing |

### SKY130 SRAM macro constraints

The v1 ASIC used the OpenRAM `sky130_sram_2kbyte_1rw1r_32x512_8` macro. Its
characterization is a first-order input to the v2 memory design:

- the PDK ships a liberty model for the **typical corner only** (TT, 1.8 V,
  25 °C); slow-corner signoff through the macro therefore uses typical data;
- read data launches from the **falling** clock edge (about 0.38–0.53 ns after
  it), so at 10 ns only about 4.5 ns remains for logic after a read;
- the model's 1.956 ns minimum period comes from an analytical model and is not
  credible silicon evidence;
- each 2 KiB macro occupies about **0.285 mm²** (16 macros = 4.55 mm²).

Phase 18 must register macro outputs immediately, keep each read within the
half-cycle budget, derate or re-characterize the macro for slow-corner claims,
and compare macros against latch/flop RAM for small, fast arrays such as caches
and NPU operand buffers.

## 2. What v2 is trying to achieve

The end system remains a small heterogeneous RISC-V SoC, not a general-purpose
desktop processor. It should have a credible scalar baseline built in this
project, useful multicore scaling, a well-fed INT8 accelerator, and reproducible
FPGA/ASIC PPA. The primary design target is **100 MHz**. That is an engineering
goal to test and close, not a claim that the current RTL already meets it.

### Top-level targets (frozen 29 September 2026)

| Metric | v2 target | Acceptance evidence |
| --- | --- | --- |
| FPGA clock | **100 MHz** for the all-engine PYNQ-Z1 image | Routed timing has nonnegative setup and hold slack, zero failing endpoints, and the physical workload suite passes at the programmed clock. A functional run without timing closure does not count. |
| SKY130 clock | **100 MHz design target** at the slow signoff corner | 10 ns SDC, setup and hold closed at every required corner, real SRAM models with credible slow-corner timing, clean post-route signoff. Phase 18 must demonstrate feasibility early. A 50 MHz result is an intermediate milestone, not a silent substitute for the 100 MHz goal. |
| CPU | **Aster core**: our own in-order RV32IMA CPU with at least **2× fewer cycles** than the v1 PicoRV32 baseline on the predeclared CPU-bound kernel set at the same clock and memory configuration | Same firmware semantics, compiler settings, inputs, cache policy, and measurement window. Lockstep-verified against an independent reference model before any performance claim. |
| NPU peak | 4×4 INT8 array: 3.2 GOPS at 100 MHz, counting one MAC as two operations | Report peak separately from sustained throughput. Publish MAC/s as well as GOPS and state the MAC counting convention in every report. |
| NPU utilization | At least **50% of peak** on predeclared dense GEMM cases with M, N, K ≥ 64, and a declared N=1 mapping with its own measured utilization | Independently checked outputs, cumulative active cycles over every job, active-PE utilization, memory bytes/cycle, and end-to-end latency. |
| Accelerator speedup | At least **5×** over the optimized scalar implementation for large dense GEMM; at least **2×** end-to-end on the selected batch-one MLP | All methods run on the same SoC configuration and input. Include setup, transfer, and completion in end-to-end results; publish kernel-only results separately. Small workloads may be slower and remain in the report. |
| FPGA resource margin | ≤80% of each PYNQ-Z1 LUT, BRAM, and DSP resource | Post-route utilization report for the exact tested bitstream. Timing closure takes priority over fitting one more feature. |
| ASIC physical signoff | Zero setup/hold violations, zero routing DRC, zero foundry DRC, zero LVS mismatch, zero antenna violations, zero max-slew/capacitance/fanout violations | Hash-bound post-route reports, real macro models, and a fresh read-only audit that fails on any unmet release gate. |
| Energy | Measured or vector-based energy per workload, not one global power number divided among workloads | Workload-specific post-route activity including SRAM macros, or a clearly labeled board measurement. Separate FPGA PL from PS and ASIC from FPGA. State the corner of every power number. |

These targets were frozen at the Phase 17 review on 29 September 2026, together
with the memory point (a 96 KiB host-loaded unified SRAM;
[`phase17-memory.md`](phase17-memory.md)) and the Aster core specification
([`cpu.md`](cpu.md)). The workload definitions and sizes are those of the
[retained Phase 17 baseline](results/phase17/baseline-9b9c94f58a85/README.md); compiler flags follow the
current Makefile. Any target change must be recorded with the evidence and
trade-off that motivated it.

### 100 MHz feasibility

100 MHz is **not inherently too fast for 130 nm**; chips at that node can run
much faster. The process node alone does not set the SoC clock: logic depth, SRAM
timing, fanout, routing, pipeline boundaries, voltage, and signoff corner do.
A 100 MHz FPGA pass would be a strong milestone, but it would not establish
100 MHz SKY130 closure. The two implementations use different cells, SRAMs,
clock trees, and routing. For this design, 100 MHz is a plausible FPGA target
and a demanding ASIC target:

- The v1.3 FPGA implementation closes at 50 MHz. The earlier 100 MHz frequency
  sweep was a functional experiment on one workload with a pre-v1.3 bitstream;
  its 10 ns timing report still had widespread failing endpoints. It is not
  100 MHz timing signoff.
- The Phase 16 SKY130 run used a 47 ns signoff clock. Its worst-corner setup
  slack was −1.154 ns, implying a required period of about 48 ns for that path.
  A 10 ns target therefore requires a different microarchitecture: short
  register-to-register paths, pipelined address generation, registered SRAM
  outputs, and bounded fanout. The ratio describes this v1 design, not a 130 nm
  limit. Placement alone cannot establish closure.
- Phase 18 synthesizes and times each new block in the actual SKY130 standard
  cell library from its first milestone, and out-of-context on the FPGA, then
  iterates on pipeline boundaries and SRAM interfaces before full-chip
  place-and-route. If a target fails, record the limiting path, the
  pipeline/area cost, and an explicit decision. A functional overclock is never
  labelled as a closed operating point.

### Memory capacity and area are one decision

The Phase 16 physical result used 16 KiB ROM plus 16 KiB RAM (16 two-kilobyte
macros), not the default 64 KiB ROM plus 64 KiB RAM map. Its 16 macros occupy
4.55 mm². Scaling that same macro choice to the full map requires about 64
macros, or roughly 18.2 mm² of macro area before standard cells, power
distribution, routing, and whitespace. A 20 mm² die cannot simply be assumed to
fit the full map. Phase 17 had to choose one of these explicit product points
(decision: a 96 KiB unified SRAM, a deliberately sized on-chip budget of type 2 —
see [`phase17-memory.md`](phase17-memory.md)):

1. a larger die with the full memory map;
2. a deliberately smaller on-chip SRAM budget with workloads/models tiled or
   streamed into it; or
3. a documented external-memory interface and a benchmark contract that includes
   its latency and bandwidth.

The choice also covers which arrays use macros and which use latch/flop RAM.
Do not shrink memory silently to make place-and-route complete, then describe the
result as the full frozen design.

## 3. Measurement contract

### Comparable engine comparisons

Every scalar, multicore, DOT8, DMA, and NPU comparison must bind the same:

- logical operation, precision, input values, seed, output layout, and expected
  result;
- SoC top, memory capacity, SRAM implementation/latency, clock, cache geometry,
  and enabled peripherals;
- compiler, optimization flags, firmware ABI, and benchmark version;
- warm/cold-cache policy and measurement boundaries.

The preferred cross-engine study uses one all-engine image. If a workload must
use a different SoC image, it is a separate data point, not a direct engine
speedup. In particular, `aster_minimal` CPU records must not be compared as though
they were captured on `aster_coherent_soc`.

### Simulated memory must match a physical target

Every v1 simulation used asynchronous, zero-wait memory by default, a model that
neither the FPGA nor the ASIC implements (both use synchronous memory with one
wait cycle). Conclusions such as "the L1 cache is a net slowdown" are artifacts
of that model. From Phase 17 on, a result is reported under the memory model of
the target it describes; the zero-wait model may appear only as an explicitly
labelled idealization next to it.

### Report both useful-work and delivered-work performance

For each workload, preserve two intervals where they are meaningful:

1. **Kernel interval:** the compute kernel after data is ready in its documented
   memory location.
2. **End-to-end interval:** dispatch, packing/materialization, DMA or NPU setup,
   memory movement, compute, wait/join, and result availability.

UART formatting and host transport remain outside the measured interval. Input
generation and scalar validation must be outside it unless the experiment
explicitly measures whole-application latency. Never compare one method's kernel
time to another method's end-to-end time.

Each record contains cycles, retired instructions, per-hart activity, cache and
backing-memory transactions, bytes moved by each engine, engine-busy and
compute-active cycles, tile/PE utilization, clock, configuration, output check,
and source/toolchain/build provenance. Device counters must identify the actual
requester: NPU stores are not DMA bytes. Cumulative counters sum every job in the
measurement window; last-job counters are named and reported separately.
The exact corrected coherent-workload schema is frozen in the
[AsterBench v11 contract](asterbench-v11.md); v2–v10 remain historical formats.

### Throughput and PPA definitions

- Report **MAC/s and GOPS** together. This project uses two operations per MAC
  for GOPS; state that convention in each artifact.
- Report cycle count and seconds at the measured, timing-closed clock. Do not
  infer a supported clock from one successful functional run.
- Report workload speedup against a frequency-normalized baseline and against
  the actual board/ASIC time as separate values.
- Report sustained throughput, theoretical peak, useful PE utilization, memory
  bandwidth, and offload overhead. Peak MAC/cycle alone is not a workload result.
- Energy/op must use workload-specific switching activity or measured workload
  power. A single vectorless whole-chip power estimate multiplied by each
  workload's time is a rough estimate, not per-engine energy.
- Every Fmax is derived from the STA report of the run it describes, with the
  SDC period used by that report; every power number names its corner.
- Save raw UART/host records, ELF/ROM hashes, full configuration, tool versions,
  timing constraints, per-workload activity files, and all audit inputs in a
  versioned evidence bundle. Generated `build/` files alone are not retained
  evidence.

### CoreMark and other benchmark rules

CoreMark's standard duration rule requires a real timer and an actual run of at
least ten seconds. A cycle counter may be used to compute the score, but it must
be converted using the measured clock; relabeling CPU cycles as milliseconds is
not valid timing. Short CRC tests remain useful as correctness tests and must be
identified as such, not as an official CoreMark score. A valid CoreMark score is
produced for the Aster core (Phase 18/21), not retrofitted to PicoRV32.

Dhrystone may remain as an adapted functional workload; if its floating-point
port is altered, report raw cycles and the adaptation rather than claiming a
standard Dhrystone/sec score. Every workload's reference model must be independent
of the implementation being measured.

## 4. Workload plan: retain the whole catalog

The workloads below are deliberately retained. Each answers a different question;
results are not collapsed into one average speedup.

| Workload family | Required cases | Main question and outputs |
| --- | --- | --- |
| CPU baseline | CoreMark, Dhrystone, sort/search, integer kernels | CPU cycles, retired instructions, IPC/CPI, CoreMark/MHz only under valid timing rules, code/data footprint. |
| Memory hierarchy | `memcpy`, sequential, strided, random walk/pointer chase, working-set sweep | Latency and effective bandwidth versus size, stride, cache state, memory wait, cache geometry, and SRAM bank conflicts. |
| DMA | CPU copy versus DMA across zero through large transfers, alignment, cache policy, memory timing | Crossover size, end-to-end latency, bytes/cycle, requester activity, CPU occupancy, and whether useful CPU work overlaps the transfer. Polling alone does not count as freed CPU time. |
| Multicore/coherence | Scalar versus 1/2 workers; reduction, GEMM, producer/consumer, SPSC queue, ping-pong, atomic counters, false-sharing and padded controls | Speedup, dispatch/join overhead, per-hart cycles, real overlap, coherence traffic, retries, and scaling as working sets grow. |
| DSP / custom ISA | Dot product, FIR, FFT, direct Conv2D and im2col Conv2D through scalar, multicore, DOT8, and NPU where supported | Crossover by K/M/N, packing/materialization cost, alignment, cycles/MAC, active units, and all retained slowdowns. |
| NPU GEMM | M/N/K sweep including partial tiles and N=1, 2, 4, 8, 16, 32, 64+ dimensions | PE utilization, tile count, operand reuse, bytes/cycle, setup cost, kernel-only and end-to-end throughput. Use dense shapes that fill the array as well as awkward shapes. |
| Quantized ML | Frozen MNIST MLP and tiny CIFAR-10 CNN, all supported execution paths | Latency/image, images/s, batch-size crossover, accuracy versus the frozen model, NPU work/idle time, and energy/inference. Keep model and quantization fixed across engines. |
| Streaming ECG | Frozen MIT-BIH segment, chunk-size/filter/model sweeps, CPU+DMA+DOT8+NPU | Sustained samples/s and per-chunk latency; report hard deadlines separately. If calling it a pipeline, prove stage overlap rather than serially waiting between stages. |

The fixed survey matrix crosses:

- one versus two harts and one versus two active workers;
- cache off/on plus selected L1/L2 geometries and cold/warm state;
- the physical target's synchronous memory model, additional wait cycles, and
  (labelled as an idealization) zero-wait memory;
- 2×2, 4×4, and 8×8 NPU geometry where resource budgets allow;
- scalar, multicore, DOT8, DMA, and NPU methods where the operation is supported;
- aligned/unaligned placement, small/large working sets, and multiple seeds.

Every planned combination is either captured or marked unsupported with a reason.
Slowdowns, failed timing points, and failed configurations remain in the report.

## 5. Verification method and release gates

### Block-level gates

Each block has an executable interface contract, independent reference model,
directed boundary tests, randomized seeded tests, reset/stall/backpressure tests,
and assertions for held requests and ownership. Add synthesis and timing reports
for the same representative configurations. A block does not advance merely
because its output checksum is correct if it already exceeds its area or timing
budget.

### CPU verification (Aster core)

The CPU is the largest new block and gets its own layered method:

1. **Independent reference model.** Spike (`riscv-isa-sim`) is the golden model;
   the core's RVFI-compatible retirement port is compared instruction by
   instruction (PC, instruction, register write, memory access, trap) in
   lockstep.
2. **Conformance.** The vendored `riscv-tests` and the official `riscv-arch-test`
   suite for every implemented extension.
3. **Directed microarchitecture tests.** Forwarding from every stage, load-use,
   branch/jump flush, CSR read-modify-write ordering, traps in every stage, and
   interrupts arriving in every pipeline state, with memory back-pressure.
4. **Constrained-random streams.** Seeded instruction streams, with memory
   stalls injected, under lockstep comparison.
5. **System regression.** The existing firmware and workload suites on the new
   core, with independent oracles.
6. **Formal (stretch).** riscv-formal through the same RVFI port.

### SoC gates

1. Run all supported workloads on the exact configuration used for the claim.
2. Verify every output independently, including full buffers, guards, model
   outputs, and final shared-memory state.
3. Prove measured workers actually execute and overlap when parallelism is
   claimed; prove accelerator activity and cumulative bytes/cycles when offload
   is claimed.
4. Deterministic RTL simulations need one capture plus one repeat to prove
   determinism; physical board workloads need at least two warm boots for each
   release-critical capture. Investigate variance rather than selecting a
   favorable run.
5. Freeze the precise image, source revision, toolchain, clock, timing constraints,
   memory model, cache policy, and record schema in the artifact bundle.

### Gate-level method

Phase 16 showed that UART-reported gate-level runs are impractical: Icarus ran at
roughly 130–350 cycles/s, and printing one record cost about two million cycles.
v2 gate-level tests write their result record to a memory buffer that the
testbench reads directly, use a fast functional gate-level simulator for
workload coverage, rely on STA for timing, and apply SDF-annotated simulation to
representative paths with a log that proves annotation took place.

### FPGA and ASIC gates

- **FPGA:** positive routed setup/hold slack at 100 MHz, no failing endpoints,
  no unrouted nets/DRC errors, resource limits met, then physical execution at
  100 MHz with independent result checking. The 50 MHz v1.3 image remains a
  comparison point.
- **ASIC:** 10 ns target SDC; setup and hold close at every declared corner;
  zero routing DRC, foundry DRC, antenna, LVS, and max-slew/capacitance/fanout
  errors; correct SRAM macros and capacity; gate-level evidence on
  representative CPU, multicore, DOT8, and NPU paths; vector-based power for the
  selected workloads.
- **Closeout:** an audit must reject negative slack, nonzero physical or
  electrical errors, missing required workloads, source drift, missing raw
  records, incorrect counter attribution, or unbound ASIC source/configuration
  (the ASIC directory is part of the hashed source state). `PASS` means every
  contractual gate passed; documented failures are status **incomplete**, not
  “complete with residuals.” Audit checks must be able to fail: substring
  matches such as `"0" in report` are not checks.

`make check` remains the fast developer regression. It is not a substitute for
the fixed full-study matrices, routed implementation, physical board capture, or
ASIC closeout.

## 6. Phase 17+ sequence

### Phase 17 — Correct the v1 baseline and freeze the v2 contract

**Purpose:** make the existing evidence honest and comparable, then decide the
end design's memory, CPU, frequency, area, and workload budgets before
performance RTL work. Phase 17 is deliberately lean: it corrects what v1 reports
and does not polish a design that v2 replaces.

**Work:**

- corrected v11 coherent-workload records with DMA/NPU attribution and
  cumulative per-window engine totals, keeping v10 parsers and history (A1–A4);
- one retained same-top v1 baseline for scalar/multicore/DOT8/NPU, captured
  under both the physical synchronous memory model and the zero-wait
  idealization (A5/A6);
- label the current CoreMark run as a fixed-iteration correctness check (B);
- correct Phase 15/16 documents and make their audits enforce their gates,
  without new ASIC runs (C);
- choose the memory/area point, including macro versus latch/flop RAM (D);
- freeze the v2 targets and write the Aster core specification in
  `docs/cpu.md` (E);
- align live documentation and housekeeping (F, H).

**Exit:** v11 records validate in Python and C++ against a shared mutation
corpus; the retained baseline's audit rejects a mismatched top, clock, memory
mode, counter, or source hash; Phase 15/16 report their true status; the memory
point, v2 targets, and CPU specification are approved. Phase 18 RTL starts after
this review.

### Phase 18 — Aster core CPU, L1/SRAM interface, and 100 MHz feasibility

**Purpose:** replace PicoRV32 with a CPU designed and verified in this project,
together with the L1 and SRAM interface that feed it, and establish 100 MHz
feasibility early.

**CPU specification** (frozen in `docs/cpu.md` during P17-E):

- **ISA:** RV32IM + Zicsr + Zifencei + A (LR/SC and AMOs, required by the
  multicore/coherence workloads) + Xasterdot8 as a native custom-0 instruction
  with the v1 encoding. Machine mode only; no MMU; no compressed instructions in
  the first version (revisit once code size versus SRAM area is measured).
- **Traps and interrupts:** standard RISC-V machine-mode traps and interrupts
  (`mtvec`, `mepc`, `mcause`, `mtval`, `mstatus`, `mie`/`mip`,
  `mcycle`/`minstret`). This replaces PicoRV32's custom IRQ convention, so the
  start-up code and handlers are ported; the timer and interrupt-controller MMIO
  pages stay.
- **Microarchitecture:** single-issue, in-order, five stages (fetch, decode,
  execute, memory, write-back) with full forwarding and a one-cycle load-use
  stall; branches resolve in execute with static backward-taken/forward-not-taken
  prediction (dynamic prediction only if measured to pay); pipelined multiplier;
  iterative divider.
- **Interfaces:** separate instruction and data ports with valid/ready
  back-pressure, shaped for synchronous SRAM (address in one stage, data in the
  next), and an RVFI-compatible retirement port for lockstep and formal checking.
- **Targets:** about 1.2–1.5 CPI on the CPU-bound set with single-cycle memory;
  at least 2× fewer cycles than PicoRV32 at the same clock and memory
  configuration; 10 ns block timing out-of-context on the PYNQ-Z1 and in SKY130
  block-level STA at the declared corners.

**Milestones** (each passes its verification layer before the next starts):

| Milestone | Content | Gate |
| --- | --- | --- |
| 18.0 | Tooling: Spike, lockstep harness, `riscv-arch-test`, committed Vivado out-of-context and SKY130 block synthesis/STA scripts | Harness detects a deliberately injected mismatch |
| 18.1 | RV32I pipeline on single-cycle tightly coupled memory | `riscv-tests`/arch tests, random lockstep, timing report |
| 18.2 | M extension | Same, plus multiply/divide corner cases |
| 18.3 | Zicsr, traps, interrupts, counters | Trap/interrupt directed tests in every pipeline state |
| 18.4 | A extension, `fence`/`fence.i` | Atomic tests and litmus programs on the core |
| 18.5 | Xasterdot8 | Exhaustive-style DOT8 reference tests from v1 |
| 18.6 | L1 instruction/data caches with single-cycle hits and the SRAM interface; runtime port | Cache reference model, stalls, firmware regression |
| 18.7 | Evaluation and feasibility | CPU set versus PicoRV32 in the same shell; 100 MHz feasibility report for FPGA and SKY130 including SRAM read timing |

**Exit:** a lockstep-verified core that meets the CPU cycle target, a memory
bandwidth/latency contract, and an early 100 MHz feasibility report for FPGA and
SKY130. If a target is infeasible, the report names the path and quantifies the
proposed pipeline/area trade-off before the target is revised.

### Phase 19 — High-utilization NPU and data movement

**Purpose:** make the array useful on real shapes rather than merely functional.

**Work:** add local operand buffers fed by DMA, full-word operand reads and
result writes, tile reuse, a pipelined address/descriptor path with no
combinational multiply in the request path, and a mapping for N=1 shapes (for
example splitting K across columns). Cumulative hardware counters are checked
against per-job sums. Evaluate direct Conv2D and GEMM lowering separately.
Double-buffer input tiles only when the memory arbitration and measured overlap
prove the benefit. Retain 4×4 as a baseline and evaluate 8×8 only with throughput,
resource, timing, and area-per-throughput results.

**Exit:** independent random/edge oracle tests, abort/reset/stall tests,
accumulated counters, and at least 50% PE utilization on the selected dense GEMM
cases; end-to-end speedup gates from section 2 pass without changing the input
or excluding required movement/setup costs.

### Phase 20 — Whole-SoC workload placement and concurrency

**Purpose:** answer the compute-placement and system-level research questions on
one coherent, timed design.

**Work:** integrate two Aster cores with per-core caches that can hit in
parallel, coherence, memory banks, DMA, DOT8, NPU, timer/interrupts, and the
software runtime. Run the complete workload matrix. Tune DMA thresholds, cache
geometry, multicore partitioning, and task placement from captured bottleneck
data. Demonstrate actual stage overlap for ECG or label the system as sequential
per-chunk processing. Keep functional and performance acceptance separate.

**Exit:** all required workload outputs match independent oracles; comparable
records exist for every supported method; multicore overlap and NPU/DMA byte and
cycle totals are proven; all design-space results preserve both wins and losses.

### Phase 21 — FPGA 100 MHz closure

**Purpose:** establish the 100 MHz PYNQ-Z1 operating point as a timed and measured
configuration, not just a functional experiment.

**Exit:** all-engine routed timing passes setup/hold at 10 ns with no failing
endpoints, routing/methodology checks pass, resource limits are respected, and
the workload acceptance subset (including a valid CoreMark run) executes
physically at that clock on repeated boots. A lower-frequency functional image
is not reported as 100 MHz success.

### Phase 22 — SKY130 implementation and PPA closure

**Purpose:** carry the selected performance architecture—not an unmeasured copy
of the v1 feature set—through a reproducible SKY130 flow.

**Exit:** chosen memory capacity fits the declared die budget; all-corner timing
closes at the v2 target (100 MHz goal); routing, foundry DRC, antenna, LVS, and
electrical checks are clean; gate-level evidence follows the method in
section 5; and workload-specific power/energy is supported by activity data. If
100 MHz fails, present the measured limit and trade-offs for an explicit project
decision.

### Phase 23 — Fabrication (optional stretch)

Tapeout remains optional. It begins only after Phase 22's source, constraints,
GDS, DRC/LVS, timing, SRAM, and software artifacts are reproducible and audited.

## 7. Plan revision — 29 September 2026

The first draft of this plan (commits `30ecb75`, `6f054c0`) correctly identified
the v1 measurement errors and set the 100 MHz direction. A second review of the
RTL, the retained reports, and the agent session history changed the plan as
follows.

1. **Phase 17 is lean.** The draft asked for three fresh captures, full
   provenance bundles, and a re-derivation of Phase 16 Fmax/power for v1. v1 is
   being replaced, so Phase 17 now keeps the v11 counter work, captures one
   retained same-top baseline (one capture plus a determinism repeat per
   configuration), corrects Phase 15/16 documents and audits without new ASIC
   runs, and moves valid CoreMark timing to the Aster core.
2. **Aster designs its own CPU.** Instead of choosing among candidate cores,
   Phase 18 designs and verifies a five-stage RV32IMA core with the
   specification in section 6 and the verification method in section 5.
3. **CPU and memory are designed together.** A new CPU dropped into the v1
   memory system would still pay about seven cycles per cache hit behind a
   chip-wide serialized fabric. Single-cycle L1 hits and an SRAM-shaped
   interface are part of Phase 18, not a later integration detail.
4. **The NPU diagnosis is sharper.** The draft attributed the weak Conv2D result
   mainly to its N=1 shape. The array computes in under 1% of busy cycles even
   on shapes that fill it (section 1), and the 13.3× GEMM result is measured
   against a CPU running at about 5.4 CPI. Phase 19 therefore targets the data
   path first and adds an N=1 mapping.
5. **SRAM macro constraints are explicit.** The typical-only liberty,
   falling-edge read data, and 0.285 mm² per macro now shape both the 100 MHz
   analysis and the P17-D memory/area decision.
6. **Phase 15/16 findings the draft missed.** Phase 16 LVS reports a netlist
   mismatch in which the top-level `vccd1` pin resolves to the `vssd1` node;
   hold fails at `nom_ff`, `max_tt`, and `max_ff`, not only at one corner; STA
   reports 85,996 max-slew and 5,443 max-capacitance violations while the flow
   summary shows zero violations; the 70.1 mW power figure is the `max_ff`
   corner (typical is 62.1 mW); the "~43 MHz TT" figure did not come from the
   `p16-f2` signoff run, whose own STA reports 40.96 MHz at `nom_tt`; only one
   of eight mandatory gate-level workloads passed
   at full size; and SDF annotation of those runs is not demonstrated. Phase 15
   also carries unreported electrical violations (2,262 max-slew and 274
   max-capacitance at `max_ss`), and its recorded `power__total` (18.26 mW) is
   likewise the `max_ff` corner (typical is 15.85 mW). These are recorded in
   P17-C.
7. **Simulation and gate-level method.** The v1 default simulation memory model
   matches no physical target, and UART-based gate-level runs are too slow to
   be a practical gate; section 3 and section 5 now define the replacements.

## 8. Decision rule

The v2 design is successful when it answers the workload-placement questions with
repeatable data **and** meets its declared CPU, NPU, frequency, memory, area, and
power budgets. Correctness, performance, and physical signoff are independent
gates. Do not trade away one silently to make another look better.
