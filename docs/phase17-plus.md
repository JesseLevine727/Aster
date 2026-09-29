# Aster v2 performance roadmap: Phase 17 and beyond

Status: **proposed engineering charter**. This plan defines the performance-oriented
successor to the functionally verified v1 system. It does not retroactively alter
v1 interfaces, measurements, or closeout bundles.

The objective is to answer Aster's research questions with comparable evidence
and produce an SoC whose CPU, memory system, accelerator, timing, and physical
implementation are designed against explicit performance budgets. Passing a
functional test is necessary; it is not a performance exit criterion.

## 1. What v2 is trying to achieve

The end system remains a small heterogeneous RISC-V SoC, not a general-purpose
desktop processor. It should have a credible scalar baseline, useful multicore
scaling, a well-fed INT8 accelerator, and reproducible FPGA/ASIC PPA. The primary
design target is **100 MHz**. That is an engineering goal to test and close, not
a claim that the current RTL already meets it.

### Proposed top-level targets

| Metric | v2 target | Acceptance evidence |
| --- | --- | --- |
| FPGA clock | **100 MHz** for the all-engine PYNQ-Z1 image | Routed timing has nonnegative setup and hold slack, zero failing endpoints, and the physical workload suite passes at the programmed clock. A functional run without timing closure does not count. |
| SKY130 clock | **100 MHz design target** at the slow signoff corner | 10 ns SDC, setup and hold closed at every required corner, real SRAM models, clean post-route signoff. Phase 18 must demonstrate feasibility early. A 50 MHz result is an intermediate milestone, not a silent substitute for the 100 MHz goal. |
| NPU peak | 4×4 INT8 array: 3.2 GOPS at 100 MHz, counting one MAC as two operations | Report peak separately from sustained throughput. Publish MAC/s as well as GOPS and state the MAC counting convention in every report. |
| NPU utilization | At least **50% of peak** on predeclared dense GEMM cases with M, N, K ≥ 64 | Independently checked outputs, cumulative active cycles over every job, active-PE utilization, memory bytes/cycle, and end-to-end latency. |
| Accelerator speedup | At least **5×** over the optimized scalar implementation for large dense GEMM; at least **2×** end-to-end on the selected batch-one MLP | All methods run on the same SoC configuration and input. Include setup, transfer, and completion in end-to-end results; publish kernel-only results separately. Small workloads may be slower and remain in the report. |
| CPU baseline | At least **2× fewer cycles** than the v1 PicoRV32 baseline on the predeclared CPU-bound kernel set at the same clock and memory configuration | Same firmware semantics, compiler settings, inputs, cache policy, and measurement window. Select and freeze the replacement CPU only after a measured candidate comparison. |
| FPGA resource margin | ≤80% of each PYNQ-Z1 LUT, BRAM, and DSP resource | Post-route utilization report for the exact tested bitstream. Timing closure takes priority over fitting one more feature. |
| ASIC physical signoff | Zero setup/hold violations, zero routing DRC, zero foundry DRC, zero LVS mismatch, zero antenna violations | Hash-bound post-route reports, real macro models, and a fresh read-only audit that fails on any unmet release gate. |
| Energy | Measured or vector-based energy per workload, not one global power number divided among workloads | Workload-specific post-route activity including SRAM macros, or a clearly labeled board measurement. Separate FPGA PL from PS and ASIC from FPGA. |

These are proposed v2 targets. Phase 17 freezes exact workload sizes, compiler
flags, memory capacity, area budget, power method, and signoff corners before
performance RTL work begins. Any target change must be recorded with the evidence
and trade-off that motivated it.

### 100 MHz feasibility

100 MHz is **not inherently too fast for 130 nm**; chips at that node can run
much faster. The process node alone does not set the SoC clock: logic depth, SRAM
timing, fanout, routing, pipeline boundaries, voltage, and signoff corner do.
A 100 MHz FPGA pass would be a strong milestone, but it would not establish
100 MHz SKY130 closure. The two implementations use different cells, SRAMs,
clock trees, and routing. For this design, 100 MHz is a plausible FPGA target
and a demanding ASIC target:

- The v1.3 FPGA implementation closes at 50 MHz. The earlier 100 MHz frequency
  sweep was a functional experiment on a limited workload; its 10 ns timing
  report still had widespread failing endpoints. It is not 100 MHz timing signoff.
- The Phase 16 SKY130 run used a 47 ns signoff clock. Its worst-corner setup
  slack was −1.154 ns, implying a required period of about 48 ns for that path.
  A 10 ns target therefore requires a major microarchitectural timing change:
  split long logic and memory/control paths across pipeline stages, then
  re-evaluate SRAM and routing. The approximate 4.8× ratio describes this v1
  path, not a 130 nm technology limit. Placement alone cannot establish closure.
- Phase 18 includes an early synthesis/place/STA feasibility experiment for
  100 MHz on both targets. If FPGA timing closes, that is a strong milestone,
  not evidence that SKY130 will close. The ASIC must be mapped and timed with
  its actual standard-cell and SRAM libraries, then iterated on pipeline
  boundaries and memory interfaces before full-chip place-and-route. If it
  fails, record the limiting path, pipeline/area cost, and an explicit decision.
  A functional overclock is never labelled as a closed operating point.

### Memory capacity and area are one decision

The Phase 16 physical result used 16 KiB ROM plus 16 KiB RAM (16 two-kilobyte
macros), not the default 64 KiB ROM plus 64 KiB RAM map. Its 16 macros occupy
4.55 mm². Scaling that same macro choice to the full map requires about 64 macros,
or roughly 18.2 mm² of macro area before standard cells, power distribution,
routing, and whitespace. A 20 mm² die cannot simply be assumed to fit the full
map. Phase 17 must choose one of these explicit product points:

1. a larger die with the full memory map;
2. a deliberately smaller on-chip SRAM budget with workloads/models tiled or
   streamed into it; or
3. a documented external-memory interface and a benchmark contract that includes
   its latency and bandwidth.

Do not shrink memory silently to make place-and-route complete, then describe the
result as the full frozen design.

## 2. Measurement contract

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
- Save raw UART/host records, ELF/ROM hashes, full configuration, tool versions,
  timing constraints, per-workload activity files, and all audit inputs in a
  versioned evidence bundle. Generated `build/` files alone are not retained
  evidence.

### CoreMark and other benchmark rules

CoreMark's standard duration rule requires a real timer and an actual run of at
least ten seconds. A cycle counter may be used to compute the score, but it must
be converted using the measured clock; relabeling CPU cycles as milliseconds is
not valid timing. Short CRC tests remain useful as correctness tests and must be
identified as such, not as an official CoreMark score.

Dhrystone may remain as an adapted functional workload; if its floating-point
port is altered, report raw cycles and the adaptation rather than claiming a
standard Dhrystone/sec score. Every workload's reference model must be independent
of the implementation being measured.

## 3. Workload plan: retain the whole catalog

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
- asynchronous simulation memory, true synchronous SRAM, and additional wait
  cycles as distinct configurations;
- 2×2, 4×4, and 8×8 NPU geometry where resource budgets allow;
- scalar, multicore, DOT8, DMA, and NPU methods where the operation is supported;
- aligned/unaligned placement, small/large working sets, and multiple seeds.

Every planned combination is either captured or marked unsupported with a reason.
Slowdowns, failed timing points, and failed configurations remain in the report.

## 4. Verification method and release gates

### Block-level gates

Each block has an executable interface contract, independent reference model,
directed boundary tests, randomized seeded tests, reset/stall/backpressure tests,
and assertions for held requests and ownership. Add synthesis and timing reports
for the same representative configurations. A block does not advance merely
because its output checksum is correct if it already exceeds its area or timing
budget.

### SoC gates

1. Run all supported workloads on the exact configuration used for the claim.
2. Verify every output independently, including full buffers, guards, model
   outputs, and final shared-memory state.
3. Prove measured workers actually execute and overlap when parallelism is
   claimed; prove accelerator activity and cumulative bytes/cycles when offload
   is claimed.
4. Capture at least three fresh RTL repetitions and two physical warm boots for
   each release-critical board workload. Investigate variance rather than
   selecting a favorable run.
5. Freeze the precise image, source revision, toolchain, clock, timing constraints,
   memory model, cache policy, and record schema in the artifact bundle.

### FPGA and ASIC gates

- **FPGA:** positive routed setup/hold slack at 100 MHz, no failing endpoints,
  no unrouted nets/DRC errors, resource limits met, then physical execution at
  100 MHz with independent result checking. The 50 MHz v1.3 image remains a
  comparison point.
- **ASIC:** 10 ns target SDC; setup and hold close at every declared corner;
  zero routing DRC, foundry DRC, antenna, and LVS errors; correct SRAM macros and
  capacity; post-route SDF on representative CPU, multicore, DOT8, and NPU paths;
  vector-based power for the selected workloads.
- **Closeout:** an audit must reject negative slack, nonzero physical errors,
  missing required workloads, source drift, missing raw records, incorrect
  counter attribution, or unbound ASIC source/configuration. `PASS` means every
  contractual gate passed; documented failures are status **incomplete**, not
  “complete with residuals.”

`make check` remains the fast developer regression. It is not a substitute for
the fixed full-study matrices, routed implementation, physical board capture, or
ASIC closeout.

## 5. Phase 17+ sequence

### Phase 17 — Re-baseline and freeze the v2 performance contract

**Purpose:** make the existing evidence comparable and decide the end design's
memory, CPU, frequency, area, and workload budgets before performance RTL work.

**Work:**

- repair v10 measurement semantics: separate DMA/NPU attribution, accumulate
  per-window engine cycles, and test counter totals against an independent
  transaction scoreboard;
- establish one all-engine RTL baseline for scalar/multicore/DOT8/NPU comparisons
  with fixed top, timing, SRAM, and input data;
- preserve the existing minimal-core numbers as a separate design point;
- implement valid CoreMark timing or label the current short run correctness-only;
- re-derive Phase 16 area, Fmax, and power from mutually consistent source reports;
- decide explicitly between full 64 KiB ROM + 64 KiB RAM, a smaller on-chip map,
  or external memory and include the macro area in the die budget;
- reconcile current architecture, phase-status, and subsystem README pages while
  leaving immutable historical bundles unchanged;
- create a fixed, hashed workload plan and correct closeout/audit acceptance logic.

**Exit:** independent auditors reproduce every baseline record and reject a
deliberately mismatched top, clock, memory mode, counter, source hash, or report.
The v2 numeric targets and selected memory/area point are approved before RTL
architecture is frozen.

### Phase 18 — CPU, memory, and 100 MHz feasibility

**Purpose:** choose a CPU and memory/fabric organization that can support the
target clock and provide a credible scalar baseline.

**Work:** prototype at least two CPU options, including the v1 PicoRV32 reference
and a pipelined in-order RV32 candidate. Measure area, IPC/CPI, memory traffic,
compiler behavior, and timing in the same minimal and all-engine shells. Compare
one strong hart before adding a second. Prototype banked SRAM/local scratchpad,
address decode, and accelerator-facing ports using the selected actual macro
model. Pipeline long register-to-register paths and test high-fanout/control
paths in STA before integrating every feature.

**Exit:** a measured CPU choice, a memory bandwidth/latency contract, an early
100 MHz feasibility report for FPGA and SKY130, and a verified RTL configuration
that meets its stage area/timing budgets. If a target is infeasible, the report
names the path and quantifies the proposed pipeline/area trade-off before the
target is revised.

### Phase 19 — High-utilization NPU and data movement

**Purpose:** make the array useful on real shapes rather than merely functional.

**Work:** add packed operand reads, tile reuse, banked local buffers or equivalent
bandwidth, full-word accumulator/result transfers, and a decoupled/pipelined
address/descriptor path. Evaluate direct Conv2D and GEMM lowering separately.
Double-buffer input tiles only when the memory arbitration and measured overlap
prove the benefit. Retain 4×4 as a baseline and evaluate 8×8 only with throughput,
resource, timing, and area-per-throughput results.

**Exit:** independent random/edge oracle tests, abort/reset/stall tests,
accumulated counters, and at least 50% PE utilization on the selected dense GEMM
cases; end-to-end speedup gates from Section 1 pass without changing the input
or excluding required movement/setup costs.

### Phase 20 — Whole-SoC workload placement and concurrency

**Purpose:** answer the compute-placement and system-level research questions on
one coherent, timed design.

**Work:** integrate the selected CPU, caches/coherence, memory banks, DMA, DOT8,
NPU, timer/interrupts, and software runtime. Run the complete workload matrix.
Tune DMA thresholds, cache geometry, multicore partitioning, and task placement
from captured bottleneck data. Demonstrate actual stage overlap for ECG or label
the system as sequential per-chunk processing. Keep functional and performance
acceptance separate.

**Exit:** all required workload outputs match independent oracles; comparable
records exist for every supported method; multicore overlap and NPU/DMA byte and
cycle totals are proven; all design-space results preserve both wins and losses.

### Phase 21 — FPGA 100 MHz closure

**Purpose:** establish the 100 MHz PYNQ-Z1 operating point as a timed and measured
configuration, not just a functional experiment.

**Exit:** all-engine routed timing passes setup/hold at 10 ns with no failing
endpoints, routing/methodology checks pass, resource limits are respected, and
the workload acceptance subset executes physically at that clock on repeated
boots. A lower-frequency functional image is not reported as 100 MHz success.

### Phase 22 — SKY130 implementation and PPA closure

**Purpose:** carry the selected performance architecture—not an unmeasured copy
of the v1 feature set—through a reproducible SKY130 flow.

**Exit:** chosen memory capacity fits the declared die budget; all-corner timing
closes at the v2 target (100 MHz goal); routing, foundry DRC, antenna and LVS are
clean; representative post-route SDF tests pass with real SRAM behavior; and
workload-specific power/energy is supported by activity data. If 100 MHz fails,
present the measured limit and trade-offs for an explicit project decision.

### Phase 23 — Fabrication (optional stretch)

Tapeout remains optional. It begins only after Phase 22's source, constraints,
GDS, DRC/LVS, timing, SRAM, and software artifacts are reproducible and audited.

## 6. Decision rule

The v2 design is successful when it answers the workload-placement questions with
repeatable data **and** meets its declared CPU, NPU, frequency, memory, area, and
power budgets. Correctness, performance, and physical signoff are independent
gates. Do not trade away one silently to make another look better.
