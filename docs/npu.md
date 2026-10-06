# The Aster NPU, v2: specification

Status: **approved by the owner, 5 October 2026.** This is the
Phase 19 contract, as [`cpu.md`](cpu.md) is Phase 18's: the design, its
programming interface, how it is verified and the gates it must pass. The
phase plan is [`phase19.md`](phase19.md); the targets are those frozen in
[`phase17-plus.md`](phase17-plus.md) §2. The owner's decisions of
5 October 2026 are recorded in §9.

## 1. Goals

v1's NPU computes correctly but is starved: each operand byte is a separate
32-bit read through a fabric that serves one transaction at a time, each
32-bit result is four byte stores, nothing is reused, and the operand
addresses come from combinational multiplies. Its useful-MAC utilization
(MACs ÷ 16 × job cycles) is 0.33% on Conv2D, 0.39% on MNIST and 0.80% on
CIFAR — the array steps in 1.34%, 1.54% and 0.87% of its job cycles (the
Phase 17 baseline, synchronous memory). v2 keeps v1's arithmetic and
replaces everything that feeds it:

- **Peak:** a 4×4 array of signed INT8 multiply-accumulates, one step per
  cycle — 16 MACs per cycle, 1.6 GMAC/s or 3.2 GOPS at 100 MHz (two
  operations per MAC).
- **Utilization:** at least **50% of peak** over the whole job on the
  declared dense GEMM cases (§7), with every transfer inside the job; a
  declared mapping for N = 1 with its own measured utilization.
- **Speedup:** at least **5×** over the best CPU code on the Aster core
  (DOT8 included) end to end on the declared GEMM cases, and at least **2×**
  end to end on the batch-one MNIST MLP (§7).
- **Timing and area:** 10 ns on the PYNQ-Z1, out of context per milestone
  and in context in the Phase 19 SoC; the whole image within 80% of each of
  the device's LUTs, block RAMs and DSPs.
- **Verifiable:** an independent oracle checks every job's whole memory; a
  cycle model predicts every job's length on a memory that answers on time;
  random and edge descriptors, abort, reset and back-pressure tests; the
  hardware's cumulative counters equal the sums of its per-job counters.

Non-goals: floating point; requantization, activation or pooling in the NPU
(owner decision, §9: the result stays raw int32 and the CPU post-processes,
as in v1); sparsity; a job queue (one job at a time, as v1). Double buffering
the operand buffers, a 64-bit memory port and the 8×8 array are evaluated in
Phase 19 and adopted only on measured benefit (§4.6, §8).

## 2. Arithmetic and job semantics

A job computes, for every 0 ≤ i < M and 0 ≤ j < N,

    C(i,j) = Σ_{k<K} sext(A(i,k)) × sext(B(k,j))      (mod 2^32)

with A and B signed bytes and C a little-endian 32-bit word, wrapping as v1
does (each product is 16 bits; the sum wraps modulo 2^32, never saturates).
K = 0 writes zeros. M = 0 or N = 0 is a job that writes nothing and
completes.

The operands' addresses (byte addresses, any alignment for A and B):

    A(i,k) = A_BASE + i × A_STRIDE + k
    B(k,j) = B_BASE + k × B_STRIDE + j
    C(i,j) = C_BASE + i × C_STRIDE + 4j

C_BASE and C_STRIDE must be multiples of 4 (results are written as whole
words). C_STRIDE ≥ 4N (C's rows do not overlap). A's and B's rows may
overlap (A_STRIDE < K, B_STRIDE < N, both may be 0): they are only read,
and overlapping A rows express a one-dimensional convolution directly (v1's
ECG FIR, which v1's descriptor could not express). C must not overlap A or
B. Every byte of A, B and C must lie in the NPU's memory window (the SoC's
main memory). Limits: M, N ≤ 4096; K ≤ 4096 (the A strip buffer, §4.4).

Memory outside C is never written. Each C word is written exactly once, as
a whole word, by a completed job; a job that is aborted or ends with error 6
leaves each C word either unchanged or holding its final value.

Milestone 19.3 adds a second level of A addressing for direct convolution
(§4.5): its four registers are 0 out of reset, which selects the plain form
above.

## 3. Programming interface (ABI 2)

A 4 KiB register page; 32-bit registers, word accesses only. v1's ABI 1
driver does not run on ABI 2 (a new driver, `aster_npu2`, comes with the
SoC in 19.4).

| Offset | Name | Access | Meaning |
| --- | --- | --- | --- |
| 0x000 | CONTROL | W | bit 0 START, 1 ABORT, 2 ACK, 3 CLEAR_TOTALS |
| 0x004 | STATUS | R | bit 0 BUSY, 1 DONE, 2 ERROR, 3 ABORTED |
| 0x008 | ABI | R | 2 |
| 0x00C | GEOMETRY | R | [7:0] ROWS, [15:8] COLS, [23:16] A strip KiB, [31:24] B buffer KiB |
| 0x010–0x018 | A_BASE, B_BASE, C_BASE | RW | byte addresses |
| 0x01C–0x024 | A_STRIDE, B_STRIDE, C_STRIDE | RW | byte strides |
| 0x028–0x030 | M, N, K | RW | dimensions |
| 0x034 | MODE | RW | [1:0] mapping: 0 automatic (K-split when N = 1, else tiles), 1 tiles, 2 K-split (N must be 1) |
| 0x038 | ERROR_CODE | R | §3.2 |
| 0x03C–0x048 | A_M0, A_STRIDE_M1, A_K0, A_STRIDE_K1 | RW | A's second level (§4.5, 19.3); 0 turns a level off |
| 0x080 | JOB_CYCLES | R | 64-bit (low word first): START accepted to DONE |
| 0x088 | JOB_ACTIVE | R | 64-bit: cycles in which the array stepped |
| 0x090 | JOB_MACS | R | 64-bit: useful MACs of the tiles (strips) completed — M × N × K for a completed job |
| 0x098 | JOB_BYTES_READ | R | 64-bit: 4 × words the NPU read |
| 0x0A0 | JOB_BYTES_WRITTEN | R | 64-bit: 4 × words the NPU wrote |
| 0x0A8 | JOB_TILES | R | 32-bit: output tiles (or K-split strips) completed |
| 0x100 | TOTAL_JOBS | R | 32-bit: jobs ended (completed, errored or aborted) |
| 0x108–0x128 | TOTAL_CYCLES, TOTAL_ACTIVE, TOTAL_MACS, TOTAL_BYTES_READ, TOTAL_BYTES_WRITTEN | R | 64-bit sums of the per-job counters |

- **START** with BUSY clear latches the descriptor, clears DONE, ERROR,
  ABORTED, ERROR_CODE and the per-job counters, sets BUSY and begins the
  checks (§3.2). START while BUSY is ignored; so are descriptor writes while
  BUSY.
- **ABORT** while BUSY stops issuing new memory requests (a request already
  presented and not accepted stays presented until accepted, as the port
  requires); once every request in flight is answered the job ends with
  ABORTED.
- **ACK** clears DONE, ERROR and ABORTED (not the counters).
- **CLEAR_TOTALS** with BUSY clear zeroes the cumulative counters.
- The per-job counters hold until the next START. The cumulative counters
  add each job's per-job values when it ends (done, error or abort) and are
  zeroed only by reset or CLEAR_TOTALS, so the totals over a window equal the
  sum of the jobs' counters — the audit §6 requires.
- **Interrupt:** `irq` is high while DONE, ERROR or ABORTED is set.
- **Register accesses:** word accesses only; a sub-word access is answered
  with an error (the core takes an access fault). Unmapped offsets and
  CONTROL read 0; writes to read-only or unmapped offsets are ignored. A
  64-bit counter is read low word first: reading the low word latches the
  high word, which the next read of the high word returns. In one CONTROL
  write, START takes effect only with BUSY clear (and ABORT is then
  ignored); ABORT with BUSY clear — including a job that ended in the same
  cycle — is ignored.
- **Utilization** of a job is JOB_MACS / (ROWS × COLS × JOB_CYCLES); the
  active-cycle form JOB_MACS / (ROWS × COLS × JOB_ACTIVE) is reported beside
  it, never instead of it.

### 3.1 Ordering with the CPU

The CPU writes A and B with ordinary stores and starts the job with a store
to CONTROL; the NPU's register page is an I/O window, so that store is
answered only by the page. The memory side applies one port's accesses in
acceptance order (cpu.md §5), and the SoC orders the NPU after it (§5.2:
an NPU read accepted after START's acceptance sees every CPU store accepted
before it), so every store the CPU issued before START is in memory before
the NPU's first read. DONE is set only after every C write has been
answered (so its error, if any, is known, and the memory side has presented
its snoop, §5.2), so a CPU that reads DONE and then C reads the results. The
driver brackets a job with `fence iorw` as v1's does; the specification does
not depend on it.

### 3.2 Errors

Checked after START and before any memory access; an erroneous job writes
nothing, sets ERROR and ERROR_CODE (the lowest code that applies). The
checks use arithmetic wide enough that nothing wraps (64 bits). A region is
the interval from its lowest to one past its highest byte — for A,
[A_BASE, A_BASE + (M−1) × A_STRIDE + K) in the plain form (with A in two
levels, §9's 19.3 bound) — and a region with no bytes (M or
K = 0 for A, K or N = 0 for B, M or N = 0 for C) is neither checked against
the window nor for overlap. Overlap is of these intervals (so interleaved
regions count as overlapping):

| Code | Error |
| --- | --- |
| 1 | M, N or K above its limit, or MODE 2 with N ≠ 1, or MODE 3 |
| 2 | C_BASE or C_STRIDE not a multiple of 4 |
| 3 | C_STRIDE < 4N |
| 4 | A, B or C not wholly in the memory window |
| 5 | C overlaps A or B |
| 6 | a memory access answered with an error (during the job: the job stops issuing, waits for its requests in flight, and ends with ERROR) |

A region's extent needs products (for example (M−1) × A_STRIDE + K); the
checks compute them over several cycles with registered multiplies, never in
the path to a memory request.

## 4. Microarchitecture

### 4.1 Overview

Descriptor checks → mapping → **loader** (address generators, memory read
requests, realignment) → **operand buffers** (an A strip and a B panel) →
**array** (pipelined MACs, an output register bank) → **C writer** (whole-word
writes). One memory port (§5.1), shared by the loader and the writer inside
the NPU. Every address the port presents comes from a register; address
generators step by additions only (no multiply in the request path).

### 4.2 Tile mapping (N ≥ 2, or MODE 1)

Output-stationary 4×4 tiles. The loader reads B's panel — all K rows of
the Nb columns that fit the B buffer (Nb = N when K × N fits, otherwise
the largest multiple of COLS that does) — once. Then, for each strip of
ROWS rows of A, it loads the strip (ROWS × K bytes) into the A strip buffer
and the array computes the strip's tiles one after another, K steps each:
at step k the array takes A(i0..i3, k) along its rows and B(k, j0..j3) along
its columns. At a tile's end its sixteen sums move to the output bank and
the next tile starts on the following cycle; the C writer drains the bank
while the array computes. When the B panel does not hold all of N, the
strips repeat for each panel (A is reread once per panel). Partial tiles at
the edges mask rows and columns: masked PEs never write C.

Traffic for one panel: A once, B once, C once — v1 reread A for every
column tile and B for every row tile, a byte at a time. Estimated
utilization on the memory that answers on time (one word per cycle, no
overlap of loading with computing): 89% at 64×64×64, 92% at 96×96×96, 91%
at 128×64×128. A panel fits when K × ⌈Nb/4⌉ × 4 bytes ≤ 16 KiB (partial
column tiles occupy whole words). When K < 16 a tile's sixteen results take
longer to write than the next tile takes to compute, and the array waits for
the output bank.

### 4.3 K-split mapping (N = 1)

For a matrix–vector product the 4×4 array splits K across its columns: PE
(r, c) accumulates A(i_r, 4t+c) × B(4t+c) over t, so each step consumes one
word of each of four A rows and one word of B, and a strip of four rows
takes ⌈K/4⌉ steps; an adder tree then sums each row's four partials into
C(i_r, 0). B (the vector, K bytes) is loaded once; A is streamed a strip at
a time. Every weight is used once, so the mapping is bound by the memory
port: at four bytes per cycle the array can be at most a quarter busy
(16 bytes of A per step). Estimated utilization on MNIST's 32×1×784, 19.5%
without overlap and 23.5% with the A strip double-buffered (§4.6), against
that 25% bound. B(k) = B_BASE + k × B_STRIDE is a word per read only when
B_STRIDE = 1; otherwise the loader gathers it a byte at a time (K bytes,
once per job). MODE 0 chooses K-split when N = 1; MODE 1 runs N = 1 as
tiles (one column in four busy).

### 4.4 Buffers, loader and writer

- **A strip buffer:** ROWS banks (one per row), 32 bits wide, K/4 words deep
  (4 KiB each for K = 4096: 16 KiB). Tile mode reads each bank once every
  four steps and selects the byte; K-split reads the four banks every step.
- **B panel buffer:** 16 KiB, one 32-bit word per read: B(k, j..j+3) in tile
  mode, B(4t..4t+3) in K-split.
- Both are block RAM on the FPGA (8 RAMB36 in all for the 4×4 array).
- **Loader:** the NPU's own DMA (the plan's "local operand buffers fed by
  DMA"): it reads the aligned words covering each row segment of an operand
  (consecutive word addresses), realigns them with a byte funnel (any A or B
  alignment and stride) and writes the buffers in the array's order. It
  keeps up to the port's limit of requests in flight (§5.1), so it streams a
  word per cycle from a memory that answers on time.
- **Array:** each PE a registered 8×8 multiply and 32-bit accumulate (LUTs or
  DSPs, as synthesis chooses; the multiplies are the only ones on the step
  path). Accumulators clear at a tile's first step; results move to the
  output bank at its last.
- **C writer:** writes whole words from the output bank; it takes the port
  ahead of the loader when the bank must drain before the next tile ends.

### 4.5 Direct convolution (19.3)

Conv2D's NPU path in v1 is im2col: the CPU builds a 784 × 25 matrix (19.6 KB)
inside the measured window. 19.3 adds a second level of A addressing:

    A(i,k) = A_BASE + (i div A_M0) × A_STRIDE_M1 + (i mod A_M0) × A_STRIDE
                    + (k div A_K0) × A_STRIDE_K1 + (k mod A_K0)

(A_M0 = A_K0 = 0 selecting §2's form). With A_M0 = the output width,
A_STRIDE_M1 = the input width, A_K0 = the kernel width and A_STRIDE_K1 = the
input width, a 5×5 convolution reads its input image directly, no im2col.
The address generators step these with additions. The phase evaluates both
lowerings on Conv2D and CIFAR's convolutions and publishes both (the plan:
"evaluate direct Conv2D and GEMM lowering separately").

### 4.6 Evaluated options

Each is built, measured and adopted only if it pays (the plan: "double-buffer
input tiles only when the memory arbitration and measured overlap prove the
benefit"):

- a second A strip buffer, so the next strip loads while the array computes
  (worth most in K-split, where loading dominates);
- a 64-bit memory port (twice the loader's bandwidth; block RAM supports it);
- the 8×8 array (64 MACs per cycle), judged by throughput, resources, timing
  and area per throughput against the 4×4 baseline, which is retained.

## 5. Interfaces

### 5.1 Memory port

A master port with the rules of the Aster core's data port (cpu.md §5) —
the handshake, a request presented and not accepted held unchanged, answers
in acceptance order with the error in the cycle after acceptance, writes
answered too — so the CPU shell's memory model and protocol checks apply
with its signals adapted: requests `m_req_valid/ready`, `m_req_addr[31:2]`
(word addresses), `m_req_we` (a write flag in place of `d_req_op`),
`m_req_wdata`; whole words only, no byte enables. Answers: `m_rsp_valid`,
`m_rsp_rdata`, `m_rsp_error`. At most OUTSTANDING requests in flight, a
parameter at least the memory's latency so the loader streams a word per
cycle (2 for the two-cycle memory; the SoC sets it to its own latency, which
a registered fabric lengthens).

### 5.2 In the SoC (19.4)

The NPU's port reaches main memory through the SoC's memory side, which
arbitrates it with the data cache's memory side and must keep cpu.md's
contracts and one more:

- a port's accesses take effect in acceptance order (cpu.md §5);
- each NPU write to cacheable memory is presented to the data cache as a
  snoop in write order, no later than the first cycle any other master can
  observe it (or its writer treat it as performed) and no later than the
  cycle the memory side accepts any request of the data cache that it
  orders after the write (cpu.md §9, 4 October 2026); the NPU treats a write
  as performed when it is answered, so its answer comes no earlier than its
  snoop;
- across ports: an NPU read accepted after the memory side accepted the
  CPU's store to CONTROL (START) returns every CPU store accepted before
  that one (cpu.md orders only one port's accesses).

The NPU's reads need no snoops: the data cache is write-through and its
stores are in memory once accepted.

### 5.3 Register port and interrupt

The register page answers the CPU's I/O-window accesses (a load answered
with the register, a store with nothing) through the same port protocol;
`irq` goes to the SoC's interrupt input (v1: source bit 2).

## 6. Verification

The Phase 18 method, applied to a block whose reference is a mathematical
oracle instead of an instruction-set model:

1. **NPU shell** (Verilator, `verification/npu/`): the NPU on the CPU shell's
   memory model — synchronous memory answering in one or two cycles, random
   back-pressure, extra and long latencies, room for more requests in flight
   — and a register driver. The memory port is checked as the core's is
   (held requests, well-formed words, requests in flight, answers in order).
2. **Independent oracle:** a C++ model of §2 written from this
   specification, not from the RTL. After every job the shell compares the
   **whole** memory: C as the oracle computes it, every other byte unchanged
   (guards everywhere, not only around C); after an abort or error 6, each C
   word old or final. Counters: for a completed job JOB_MACS = M × N × K and
   JOB_BYTES_WRITTEN = 4 × M × N; the cumulative counters equal the sums of
   the per-job counters the shell read.
3. **Cycle model:** a model of §4's mapping predicts each job's JOB_CYCLES and
   JOB_BYTES_READ exactly on the memories that answer on time, as the CPI
   model did for the core, so a utilization claim is a property of the
   design, not of one run.
4. **Descriptors:** a seeded random generator with coverage bins that fail a
   run when missed — M, N, K at 0, 1, every residue modulo the tile, and up
   to the limits; N = 1 in each mode; unaligned A and B; overlapping A and B
   rows; strides at their minimum; every error code — plus the declared gate
   cases and v1's workload shapes as directed tests.
5. **Abort, reset and stall:** abort and reset at random cycles of random
   jobs (the NPU must return idle, the memory consistent as in item 2), in
   every memory mode.
6. **Harness self-tests:** the shell must reject a corrupted C word, a stray
   write outside C, a wrong counter and DONE before the last write is
   accepted — each planted on purpose.
7. **v1 first:** as PicoRV32 proved the CPU shell, v1's engine runs in the
   NPU shell first through an adapter (inside its 32 KiB window), proving the
   harness on a known design and giving the same-shell baseline.
8. **Mutation campaign:** planted faults in the NPU RTL, each caught by the
   tests or recorded as equivalent, as 18.3's campaign did for the core.
9. **SoC (19.4):** the Aster core, its caches and the NPU in one design, the
   CPU in lockstep with Spike where no NPU result is read and self-checking
   programs where one is; the shell's oracle checks every NPU job's memory
   in the SoC too, and the CPU baselines' results are compared with the
   oracle's (a second copy of 128×64×128's 32 KiB result does not fit beside
   it in 96 KiB, so not in the program); directed coherence tests (the CPU
   holding a C line in its cache while the NPU writes it); a checker that
   every NPU write to cacheable memory is snooped as §5.2 requires, and that
   the cross-port order holds.

## 7. Timing, area and performance gates

- **Timing:** each milestone's NPU closes 10 ns out of context (Vivado,
  register to register, the PYNQ-Z1 part); the Phase 19 SoC closes 10 ns in
  context; the board runs the gate workloads at 100 MHz, cycle for cycle as
  simulated (as in 18.7).
- **Area:** the Phase 19 SoC image within 80% of the device's LUTs, block
  RAMs and DSPs; the NPU's own use reported per milestone.
- **Utilization (declared 5 October 2026, before any measurement):** at least
  50% (JOB_MACS / (16 × JOB_CYCLES)) on each of the dense GEMM cases
  64×64×64, 96×96×96 and 128×64×128 (M×N×K), in the SoC on its main memory.
  The N = 1 mapping's utilization is measured and published on MNIST's first
  layer (32×1×784) and Conv2D (784×1×25), without a threshold.
- **Speedup (declared 5 October 2026):** end to end — from A and B in memory
  in §2's layout to C in memory and visible to the CPU, setup, any packing,
  the job and completion included — at least **5×** over the best CPU code
  on the Aster core in the same SoC on each of the three GEMM cases; at least
  **2×** per image over the best CPU code on the batch-one MNIST MLP
  (784 → 32 → 10, the catalog's only MLP), its window as the workload
  defines it. The best CPU code may use DOT8 (owner decision, §9): a tuned,
  register-blocked DOT8 GEMM and matrix–vector product, each verified
  against the oracle. Kernel-only results are published separately, never in
  their place.
- Estimates, not gates: a register-blocked DOT8 GEMM on the Aster core has
  an instruction-count ceiling of about 1.6 MACs per cycle (8 loads, 16
  `dot8` and 16 adds per 64 MACs) and should reach about 1.2–1.5 with its
  loop, its stores, repacking B (`dot8` needs four consecutive k in a word)
  and the 4 KiB cache's misses; so 5× asks the NPU for about 6–8 sustained
  MACs per cycle end to end, and §4.2's tile mapping estimates about 14. The
  MLP's 2× is the tight gate (about 2–3× by estimate): its first layer is
  memory-bound on the NPU (§4.3), and the CPU's work between layers, which
  both methods share, dilutes the ratio; the options of §4.6 are its
  levers.

## 8. Milestones

| Milestone | Content | Exit gate |
| --- | --- | --- |
| 19.0 | NPU shell, oracle, descriptor generator, harness self-tests; v1's engine through an adapter | v1 passes the oracle in its 32 KiB window in every memory mode; every self-test rejected; v1's same-shell utilization recorded on the cases that fit its window (64×64×64, MNIST's 32×1×784, Conv2D's 784×1×25) |
| 19.1 | ABI 2; tile mapping: checks, address generators, loader, buffers, pipelined array, C writer; cycle model | random and edge descriptors pass the oracle in every memory mode; the cycle model exact on the on-time memories; ≥50% utilization on the three GEMM cases in the shell; 10 ns out of context |
| 19.2 | K-split mapping; abort and reset; cumulative counters; error codes; interrupt | the 19.1 gates on the new paths; abort/reset tests; counters audited; N = 1 utilization published; 10 ns |
| 19.3 | Direct convolution (§4.5) | the two-level addressing passes the oracle; im2col and direct lowerings measured on Conv2D and CIFAR's convolutions and published; 10 ns |
| 19.4 | The Phase 19 SoC (core, caches, NPU, 96 KiB main memory, snoops); `aster_npu2` driver; the DOT8 baselines; the gate workloads | all outputs verified independently; coherence tests; the utilization and speedup gates of §7 in the SoC; 10 ns in context |
| 19.5 | Evaluation: §4.6's options; the board run | each option adopted or rejected on measurements; the gate workloads on the PYNQ-Z1 at 100 MHz, cycle for cycle as simulated; the Phase 19 report |

## 9. Approval

**Decided by the owner, 5 October 2026** (before any design work):

- **Platform:** the NPU is verified first in its own shell, then joined to
  the Aster core, its caches and on-chip memory in a Phase 19 SoC grown from
  the 18.7 board design, where the speedup gates are measured (in simulation
  and on the PYNQ-Z1). Phase 20 still adds the second core, the shared fabric
  and the full workload matrix.
- **Gate cases:** dense GEMM 64×64×64, 96×96×96 and 128×64×128; the batch-one
  MNIST MLP (784 → 32 → 10); the N = 1 mapping measured on MNIST's
  32×1×784 layer and Conv2D's 784×1×25.
- **Baseline:** the best CPU code on the Aster core, DOT8 included — the
  strictest of the options offered — for the MLP's 2× as well as the GEMM's
  5× (the question named the GEMM gate; the owner confirmed the MLP with the
  specification).
- **Output:** raw int32 as in v1; scaling, activation and pooling stay on the
  CPU.

**Approved by the owner, 5 October 2026:** this specification, as reviewed
(cpu.md was approved the same way before Phase 18's RTL). Changes found
necessary during the phase are recorded here as clarifications or owner
decisions, as cpu.md §9 records Phase 18's.

### Clarifications in 19.1 (the tile mapping)

Found necessary while building and verifying 19.1; accepted by the owner
with the milestone (5 October 2026):

- §4.2: there are **two** output banks, used in turn, so a tile's results are
  written while the next tile computes; a tile starts only when its bank
  (the one two tiles back used) has been handed to the memory port. The array
  still waits when K is small (§4.2's "When K < 16").
- §1, §4.3: 19.1 runs every job as tiles — MODE 2, and N = 1 under MODE 0,
  included (one column in four busy). The K-split mapping is 19.2's.
- §3: CHECK takes 16 cycles, so a descriptor error (or M or N = 0) ends the
  job 17 cycles after START.
- §3: JOB_MACS and JOB_TILES count the tiles whose results reached an output
  bank (their useful MACs, and the tiles), including, after an abort or a
  memory error, tiles whose results were then not written.
- §3: one 64-bit latch serves the counters, tagged with the counter whose low
  word was read: a high-word read returns the latch, once, only when its
  counter's low word was the last one read, and its live high word
  otherwise. No
  simulation reaches 2^32 counts, so the latch is checked by inspection only.
- §3.2 (error 6): STOP is registered, so the request presented in the cycle
  the error arrives may still be accepted, then or later while held — at
  most one late request; such a write still writes its final value, so §2's
  rule (each C word unchanged or final) holds.
- §3: a job ends only once the array's pipeline is empty too (after an abort
  or an error, the steps in flight finish first), so a job's counters hold
  from the cycle its end shows.

### Clarifications in 19.2 (the K-split mapping)

Found necessary while building and verifying 19.2; accepted by the owner
with the milestone (5 October 2026):

- §4.3: each row's four partial sums are added when the writer takes that
  row's result (the "adder tree" is in the writer's path), so the array and
  its output banks are the tile mapping's.
- §4.3: B is packed once per job, word t holding B(4t .. 4t+3): read as the
  words covering its K bytes when B_STRIDE is 1, otherwise a word per byte
  (K reads). The loader now places each byte at an exact buffer position
  with exact byte enables, which this gathering needs; the tile mapping's
  loads are unchanged in words and cycles.
- §4.3: a K-split strip takes ceil(K/4) steps (one, writing zeros, when
  K = 0); on its last step, the products of the lanes beyond K are zero. It
  counts as one tile in JOB_TILES, and its steps in JOB_ACTIVE (none when
  K = 0).

### Clarifications in 19.3 (direct convolution)

Found necessary while building and verifying 19.3; accepted by the owner
with the milestone (5 October 2026), CIFAR channels last included:

- §4.5: each level is off on its own: A_M0 = 0 makes a row's offset
  i × A_STRIDE, A_K0 = 0 makes a k's offset k (both 0: §2's form). The four
  registers (0x03C–0x048) are written, like the descriptor, only while not
  BUSY.
- §3.2: with A in two levels, A's region (for error 4's window and error 5's
  overlap) runs from A_BASE to one past the bound ((M−1) div A_M0) ×
  A_STRIDE_M1 + min(M−1, A_M0−1) × A_STRIDE + ((K−1) div A_K0) ×
  A_STRIDE_K1 + min(K−1, A_K0−1). Each term is its largest; that is exact for
  §2's form and for a convolution, whose last output pixel and last kernel
  byte reach every largest term at once, and safe otherwise. CHECK computes
  it with two 12-step dividers beside the panel's, so it keeps its 16 cycles.
- §4.5: the loader reads A's row i as one segment of K bytes, or with A_K0 as
  segments of A_K0 bytes (the last of K mod A_K0), each at its byte of the
  row in its bank, so a kernel row is one segment.
- §4.5, the workloads: two levels address a convolution whose kernel window
  is two runs deep: one channel (Conv2D), or channels last (HWC), where a
  kernel row is kw × C contiguous bytes. CIFAR keeps its activations as
  channel planes (CHW), whose window is three deep (channel, row, column).
  19.3 measures CIFAR's direct convolutions channels last; Phase 20's CIFAR
  stores its images and weights reordered at build time and writes its
  pooled activations channels last, at no run-time cost. A third level was
  the alternative.

### Clarifications in 19.4 (the Phase 19 SoC)

Found necessary while building and verifying 19.4:

- §5.2: the SoC's port B serves the data cache first and the NPU in any
  other cycle (not while an AMO holds it), so the data cache's readiness
  never depends on the NPU. Each NPU write is snooped in the cycle after its
  acceptance (the first cycle the port can accept the data cache's next
  request) and answered in the cycle after that.
- §5.2 (cpu.md §5's reservation): an NPU write to the word the core's `lr`
  reserved ends the reservation, so the `sc` after it fails, as the A
  extension requires of another device's write.
- §3, §5.3: the NPU's register page is the data cache's second I/O window,
  0x4000_0000–0x4000_0FFF, marked word-only. The data cache faults any
  access but a word load or a word store there (sub-word, `lr`, `sc`, AMOs)
  in its first stage, as it does an unmapped address (a new aster_l1d
  parameter, `IO_WORD_ONLY`, off by default). The register port's own error
  answer stays for the shell. A late error is one the cache does not take
  (cpu.md §4). 18.7's `traps/m1_traps`, which uses 0x4000_0000 as unmapped,
  is not run on the SoC.
- §6, item 9 ("the CPU in lockstep with Spike where no NPU result is
  read"): the CPU programs (18.7's board programs) run on the SoC retiring
  exactly the CPU shell's RVFI records, record for record. Phase 18's suites
  compare the shell's runs of them with Spike. Against those runs' traces,
  97 of the 99 are byte for byte the same. `rv32ua/lrsc` differs from one
  `sc` on: Phase 18's run answers each `sc` as Spike did, where Spike's
  reservation ended differently. `selfcheck/dot8_arith` is self-checking,
  never compared with Spike. The SoC has no Spike run of its own.
- §7, the speedup windows: the NPU's runs from before the descriptor's first
  store to the CPU seeing DONE (ACK and the counters are read after). The
  CPU's runs from A and B in memory to C in memory, B's packing included; A
  needs none, since a row-major row already holds four consecutive k a word.
  The CPU's GEMM is built for each K (the gate cases' 64, 96 and 128). In the
  MLP, both paths use weights stored in their own layout before the windows:
  the CPU's 16 neurons interleaved a word, the NPU's the model's row-major
  ones. The workload's window is otherwise the same for both.
