# The Aster SoC, v2: specification

Status: **approved by the owner, 7 October 2026.** This is the
Phase 20 contract, as [`cpu.md`](cpu.md) is Phase 18's and [`npu.md`](npu.md)
Phase 19's: the design, its programming interface, how it is verified and the
gates it must pass. The phase plan is [`phase20.md`](phase20.md); the targets
are those frozen in [`phase17-plus.md`](phase17-plus.md) §2. The owner's
decisions of 7 October 2026 are recorded in §13.

## 1. Goals

Answer the plan's compute-placement and system-level questions on one
coherent, timed design: two Aster cores, each with its own caches that hit in
parallel, on a banked main memory with coherence, DMA, DOT8, the Phase 19 NPU,
a timer and interrupts, and the software runtime; then run the complete
workload matrix and tune the design from the bottlenecks it shows.

Gates (§11, the owner's): the plan's exit; two workers at least **1.8×** over
one on a reduction that fills and sums in parallel and on the three GEMM
cases with DOT8; and, for every workload v1 retained, a v2 method faster than
v1's best. The frozen targets still apply: 10 ns on the PYNQ-Z1 for the image
with every engine, and at most 80% of the device's LUTs, block RAM and DSPs.

## 2. The SoC

| Block | What it is | From |
| --- | --- | --- |
| Harts 0 and 1 | Aster cores (RV32IMA, Zicsr, Zifencei, Xasterdot8; `HART_ID` 0 and 1) | Phase 18, unchanged |
| Caches, per hart | 4 KiB instruction cache; 4 KiB write-through data cache, which gains a snoop port for each other writer (§4.6) | Phase 18 (18.6) |
| Main memory | 96 KiB in four banks of block RAM (§4.2) | The frozen memory point, now banked |
| Fabric | Arbitration, atomics, reservations and snoops between seven requesters and the banks; the I/O bus (§4) | New |
| NPU | The Phase 19 v2 NPU: 8×8, two A strip buffers, 64-bit port | Phase 19, unchanged |
| DMA | A new 64-bit engine behind v1's DMA ABI 1 (§6) | New |
| Devices | UART, timer, hart control, performance counters, interrupt controller, at v1's addresses (§7, §8) | v1, ported |
| ARM side | The AXI4-Lite window of the 18.7 and Phase 19 board designs | Phase 19 |

The clock is FCLK0 at 100 MHz. One reset (the CONTROL register's run bit, as
in Phase 19) releases hart 0, the NPU, the DMA and the devices; hart 1 also
waits for the hart-control page's SECONDARY_RUN bit (§7.3).

**Estimated area.** The 19.5 SoC's routed checkpoint, broken down by block
(`docs/results/phase20/area-basis/`), gives the measured parts. The rest are
estimates:

| Block | LUTs | Basis |
| --- | ---: | --- |
| The 19.5 SoC | 21,520 | measured: core 3,167, data cache 2,373, instruction cache 620, NPU 14,131 |
| Hart 1 with its caches | 6,160 | measured (hart 0's) |
| Snoop ports (two more per data cache) | ~1,200 | a tag copy (the cache's two tag read ports take 224 LUTs as RAM), a compare and a valid-bit decode each |
| Fabric | ~3,000 | arbitration and 64-bit multiplexers for seven requesters and four banks; atomics |
| DMA | ~2,300 | engine, byte funnel, registers and 14 counters |
| Devices | ~550 | timer, interrupt controller, hart control, UART |
| Counters | ~4,600 | about 66 more 64-bit counters (per hart, DOT8, fabric) and their read multiplexer |
| **Total** | **~39,300 (74%)** | |

That is about 81 block-RAM tiles (58%) and 27 DSPs (12%). Hart 1's caches
add two tiles. The banks are expected to need no more than main memory now
uses: the 19.5 SoC's top level holds 37 tiles, for main memory, the register
page and the console. The total is under
the 80% limit, but with less room than the 19.5 SoC. The counters are the
largest new block: if area or timing needs it, they can count in 48 bits and
read as 64, as 2⁴⁸ cycles is 32 days at 100 MHz.

## 3. Memory map

| Region | Start | End | Notes |
| --- | ---: | ---: | --- |
| Main memory | `0x8000_0000` | `0x8001_8000` | 96 KiB, code and data, cacheable; four banks (§4.2) |
| UART | `0x2000_0000` | `0x2000_1000` | v1's TX word; the console (§7.4) |
| Timer | `0x2000_1000` | `0x2000_2000` | v1, ABI 1; its count is the cores' `mtime` (§7.1) |
| Hart control | `0x2000_2000` | `0x2000_3000` | v1's layout (§7.3) |
| Performance, hart 0 / hart 1 | `0x2000_3000` | `0x2000_3200` | v1's ABI 4 (§8) |
| DOT8 counters | `0x2000_3200` | `0x2000_3300` | v1's ABI 6 layout |
| Fabric counters | `0x2000_3300` | `0x2000_3400` | New (§8) |
| Interrupt controller | `0x2000_4000` | `0x2000_5000` | v1, ABI 1 (§7.2) |
| DMA | `0x3000_0000` | `0x3000_0200` | v1's DMA ABI 1 and its counters, counter ABI 5 (§6) |
| NPU | `0x4000_0000` | `0x4000_1000` | NPU ABI 2 (npu.md §3), word accesses only |

Instructions are fetched from main memory only. A data access outside these
regions is an error: the data cache faults it before it leaves the cache, as
cpu.md §4 requires (v1 returned zero without a fault).

Every device page takes word loads and word stores only, as the NPU's has
since 19.4. The data cache faults any other access to them before it leaves
the cache, a byte or halfword access or an atomic, using its existing
word-only windows. v1 faulted atomics to devices too, but took byte lanes on
some registers. v2 drops that: v1's runtime and drivers access devices by
words only, which 20.2 checks.

There is no hardware partition of main memory: v1's shared and private
regions become a software layout (18.6's: 32 KiB of code, 32 KiB shared, two
private 16 KiB regions with the stacks), and a program may use its own layout
(Phase 19's gate programs use the memory whole).

## 4. The memory fabric

### 4.1 Requesters

| Requester | Port | Requests |
| --- | --- | --- |
| I0, I1 | the instruction caches' memory sides (cpu.md §5) | word reads (refills), at most two in flight |
| D0, D1 | the data caches' memory sides (cpu.md §5) | word reads (refills), stores, lr, sc, AMOs; I/O accesses; at most two in flight |
| N | the NPU's memory port (npu.md §5.1) | 8-byte reads, writes with byte enables; at most two in flight |
| R, W | the DMA's read and write ports (§6) | 8-byte reads; 8-byte writes with byte enables |

While the harts are held in reset, the ARM side reads and writes main memory
and the devices through the fabric, as in Phase 19.

### 4.2 Banks

Main memory is four banks of 24 KiB. Each is 64 bits wide and interleaved by
cache line: the bank is address bits [5:4], so a 16-byte line lies in one bank
and a sequential stream of 8-byte units moves to the next bank every two
units. Each bank is a true dual-port block RAM with its output register: a
read accepted at an edge is answered two cycles later, as Phase 19's main
memory was.

**The bank rule.** In one cycle a bank performs at most two accesses, at most
one of them a write, and never a read and a write of the same 8-byte unit.
Nothing therefore depends on how the block RAM behaves when its two ports
meet on one address.

### 4.3 Acceptance, order and answers

- A request takes effect at the edge that accepts it. A write's bytes are in
  the bank from that edge; a read samples the bank at that edge. **Acceptance
  is the access's place in the memory order**, the rule cpu.md §5 and §9
  build on (posted stores, and `fence` needing nothing). The one exception is
  an AMO, whose place is its write (§4.5).
- Each requester's accesses take effect in the order they are accepted, and
  its answers return in that order. Every answer comes two cycles after
  acceptance — main memory's, and an I/O device's (devices answer in the next
  cycle and the fabric returns the answer on the two-cycle schedule).
- **A hart alone is never slowed.** With no request from the other hart, the
  NPU or the DMA, each of its caches is accepted in the cycle it asks and
  answered two cycles later. Its instruction refill and its data access are
  accepted together even in one bank. The one exception is a write (a store,
  sc or AMO) to an 8-byte unit that an instruction refill reads in the same
  cycle: data sharing a unit with code. A single hart therefore runs cycle for
  cycle as in the CPU shell's two-cycle memory, outside that exception, and
  §10.4 checks that it does.
- The caches, the NPU and the DMA each check their own ranges, so none sends
  an access outside main memory. The fabric answers one, should it come, with
  an error and no effect, and its shell asserts that none comes.

### 4.4 Arbitration

Each bank grants, among the requests for it in a cycle, as many as the bank
rule allows, in round-robin order. A write whose turn has come takes the
bank's write slot even while reads keep arriving, so no requester waits
forever. The longest wait is counted (§8) and published.

A cache's readiness may depend on the other requesters' requests in the same
cycle. Every requester drives its request from its own
registers, so arbitration is a register-to-register path through the fabric,
timed from 20.1.

The I/O bus takes one access a cycle from D0 or D1 (rotating priority).

### 4.5 Atomics and reservations

The fabric performs the A extension's memory side (cpu.md §5, operations
1–12, v1's `funct5` semantics):

- **lr** reads its word and sets its hart's reservation on that word. Each
  hart has one reservation.
- **sc** writes, and answers 0, if its hart holds a reservation on its word;
  otherwise it has no effect and answers 1. Every sc ends the reservation.
- **An AMO** reads its word at acceptance and writes the new value two cycles
  later. Until that write, its bank accepts no other write and no other AMO,
  and its data cache has nothing else accepted (as in Phase 19). So no update
  is lost, not even to the other hart's AMO on the same word a cycle later.
  Its place in the memory order is its write. A read of its word by another
  requester in between returns the old value and is ordered before it. The
  AMO answers with the old value.
- **A reservation ends** when another requester (the other hart, the NPU or
  the DMA) writes any byte of its word. It also ends at every sc of its hart,
  and when its hart takes an exception or is reset. The hart's own stores
  and its interrupts do not end it. This is Spike's rule, which the CPU
  shell and the Phase 19 SoC keep and `directed/atomics` tests (an sc
  succeeds after the hart's own store to the word). v1 also ended a
  reservation on the hart's own store; cpu.md §9 lets the memory side keep
  that rule or document it, and this documents it, as 18.6 did. An sc and a
  write to its word are never accepted in the same cycle, because a bank
  takes one write a cycle.

### 4.6 Snoops

For every write to main memory (a store, a successful sc, an AMO's write, an
NPU or DMA write), the fabric presents the written line to every data cache
except the writer's own. The snoop comes **in the cycle after the write takes
effect**: after acceptance for most writes, and after its write (acceptance
+ 3) for an AMO.

Each data cache has three snoop ports, one for each other writer: the other
data cache, the NPU and the DMA's write port. Each writer makes at most one
write a cycle. Each port compares the line's tag, so only real copies are
invalidated.

cpu.md §9's contract, for each port:
- **No later than anyone else can observe the write.** The written unit
  cannot be read in the write's cycle, by the bank rule; a read accepted in
  the next cycle is answered later still.
- **A lookup in the snoop's cycle already misses,** and a refill of that line
  in progress installs nothing. (A lookup is a request entering the cache's
  head, stage 2.)
- **No later than the cycle the fabric accepts any request of this cache that
  it orders after the write.** One case needs an argument, not the letter of
  the rule. Writes by two caches accepted at the same edge, in different
  banks, are ordered either way. Whichever cache's write is ordered second
  receives the first one's snoop a cycle after its own write's acceptance.
  That is safe: its write reads nothing, and its next lookup comes no earlier
  than the next cycle, when the snoop has arrived, because the accepted
  request holds the cache's head through that edge. This refines cpu.md §9's
  wording for same-edge writes (approved, recorded there, 7 October 2026).

The data cache's RTL gains a parameter for its number of snoop ports: 1 in
the Phase 19 SoC, 3 here.

The instruction caches do not snoop. A hart that runs code written by the
other hart, the NPU or the DMA executes `fence.i` first, as RISC-V requires
(cpu.md §4).

### 4.7 The memory model

With §4.3–4.6, every load returns its word's value at a single point in a
global order of writes:
- its cache lookup, for a hit;
- the acceptance of the refill read of its word, for a miss;
- its acceptance, for an I/O access, an lr or an sc;
- its write, for an AMO.

Writes are ordered by when they take effect; writes to different banks at
the same edge may be ordered either way (§4.6). This
makes the memory multi-copy atomic, and the cores' program-order rules (cpu.md
§5) give RVWMO with the A extension. §10.2's checker tests exactly this
statement.

## 5. The NPU

The Phase 19 NPU, unchanged (npu.md): 8×8, two A strip buffers, the 64-bit
port, ABI 2 at `0x4000_0000`. Its port is requester N. Its writes are snooped
(§4.6), and its interrupt is source 2 of the interrupt controller (§7.2). In
the Phase 19 SoC its interrupt drove hart 0's MEIP directly; programs that
used that now enable source 2. Its options (4×4, one strip, a 32-bit port)
remain RTL parameters and are axes of the matrix (§9).

## 6. The DMA engine (v1's DMA ABI 1)

**Registers** at `0x3000_0000` are v1's DMA ABI 1, unchanged, so v1's driver
(`software/drivers/aster_dma.c`, which checks ABI 1 and hart 0) runs as it is:
- SOURCE, DESTINATION, LENGTH;
- COMMAND, with START 1, ABORT 2 and ACK 4;
- STATUS, BYTES_DONE, ERROR_CODE, ABI (1), JOB_CYCLES;
- LIMIT_LO/LIMIT_HI, which give the platform's copyable range. Here that is
  main memory, `0x8000_0000`–`0x8001_8000`; in v1 it was its shared RAM;
- the counters at `0x100` (counter ABI 5) and the metadata at `0x180`.

Only hart 0 programs it, as in v1; hart 1's writes are ignored. So placing
DMA work on hart 1 is not a supported method.

The counters keep v1's order and meanings. "Invalidated lines" counts the
lines the DMA's writes invalidate in the data caches, which are snoop hits
on its write port. The two events that cannot occur with write-through
caches read 0: cache-read forwards and dirty write-back words.

**A job** copies LENGTH bytes from SOURCE to DESTINATION. The ranges may have
any alignment, and both must lie in main memory: the checks are 33-bit, as
v1's. Overlapping ranges are rejected (ERROR_CODE 3: a copy, not a move).
LENGTH 0 completes at once. DONE is set once the job's last write has been
answered, and stays set until the next START or an ACK.

**The engine** reads 8-byte units on port R and writes 8-byte units on port
W, with two reads in flight. A byte funnel realigns the source to the
destination, so every write is one destination unit, with partial byte
enables only at the ends. At most 8 bytes move per cycle, when the read and
write banks are free.

**ABORT** is cooperative, as in v1. The engine offers no new request; one
it has already offered is completed, since the port's protocol does not
withdraw a request. It waits for the answers it is owed, then reports in
BYTES_DONE the prefix of the destination whose writes completed. No byte
beyond that prefix is written.

**Completion** raises source 1 of the interrupt controller. The DMA's writes
are snooped (§4.6). Its reads see main memory directly: write-through caches
hold no data newer than memory, so no intervention is needed, unlike v1's
write-back caches.

## 7. Timer, interrupts and the second hart

### 7.1 Timer

v1's timer, ABI 1, at `0x2000_1000`: a free-running 64-bit count of clock
cycles, a 64-bit compare and a level interrupt (docs/timer.md). Its count
drives both cores' `mtime` input, so `time`/`timeh` read it (cpu.md §3, which
leaves the timer's source to Phase 20). Its interrupt is source 0 of the
interrupt controller.

### 7.2 Interrupt controller

v1's controller, ABI 1, at `0x2000_4000` (docs/interrupts.md). Its sources
are 0 the timer, 1 the DMA's completion, 2 the NPU's completion and 3
software (RAISE). ENABLE0 and ENABLE1 drive hart 0's and hart 1's MEIP.
MTIP and MSIP are tied low: there is no CLINT, and cpu.md §3 allows this
choice. A hart signals the other through source 3, or through memory.

### 7.3 Hart control

v1's page at `0x2000_2000`:
- ID: the requesting hart;
- SECONDARY_RUN: hart 0's word stores only, as in v1;
- HART_COUNT: 2, or 1 when the SoC is built with one hart;
- STATUS: bits 0 and 1, harts running. v1's trapped bits, 8 and 9, read 0:
  the Aster core takes its traps at `mtvec` and does not stop;
- the TO_HART1 and TO_HART0 mailboxes.

v1's warm-stop and fault-capture words read 0: there are no dirty lines to
flush.

Writing 0 to SECONDARY_RUN holds hart 1 and its caches in reset. Writing 1
releases it at `0x8000_0000`, and it reads `mhartid` = 1
(start_multicore_aster.S). When hart 1 is reset:
- requests it had accepted complete in the fabric, an AMO's write included;
- the answers owed to its caches are dropped, even when it is released again
  before they arrive, with added memory waits too;
- a request it had not had accepted is withdrawn;
- its reservation ends.

### 7.4 The console

A store to the UART's TX word appends its low byte to the console: a 4 KiB
buffer and a byte count, which the ARM side reads over AXI, as in Phase 19.
The UART answers in the next cycle and never makes a hart wait. The count
keeps counting past 4 KiB, so an overflow is visible. Both harts' bytes go
in the order the I/O bus accepts them. v1's serial transmitter, and the
stall it imposed while its FIFO was full, are not kept.

## 8. Performance counters and records

- **Per hart: v1's ABI 4** (`0x2000_3000`, `0x2000_3100`), with ABI 4's
  fourteen 64-bit counters, command word and metadata (docs/phase6.md), the
  ABI word reading 4, and each event as ABI 4 defines it:
  - invalidations count, for the requesting hart, the lines its writes
    invalidate in the other cache;
  - the events that cannot occur with write-through caches (dirty
    interventions, write-back words) read 0.

  The command word at `0x2000_3080` (START, FREEZE, RESUME; hart 0 only, as
  in v1) starts and freezes every counter page together: the harts', the
  DOT8, DMA and fabric counters.

  v1's CPU kernels (CoreMark, Dhrystone, sort/search, strided, FFT, Conv2D)
  read aster_minimal's older map at the same address (`aster.h`, control at
  `0x38`, ABI 2). They are ported to ABI 4's counters: the same code and
  inputs, with records written as v12. The 99 regression programs keep the
  CPU shell's page (§10.4).
- **DOT8 counters** (`0x2000_3200`): v1's layout. Each hart's dot8
  instructions are counted as accepted, completed and retired; the core never
  makes a dot8 wait, so its wait count is 0.
- **Fabric counters** (`0x2000_3300`, new):
  - per requester: accepted requests and cycles waited (requesting, not
    granted);
  - per bank: reads, writes and conflicts;
  - snoops delivered and lines invalidated, per data cache and per snoop
    port (that is, per writer): the victim's view, beside ABI 4's
    requester's view;
  - reservations ended by another requester;
  - AMOs;
  - the longest wait.

  These are the captured bottleneck data the plan's tuning is to use.
- **Records: AsterBench v12** — v11's engine-attributed fields (asterbench-v11.md),
  with the v2 SoC's configuration (harts, workers, cache geometry, cold or
  warm caches, memory waits, the NPU's options), the NPU v2's and the new
  DMA's totals, and the fabric counters. Each record is validated by a Python validator and a C++
  validator that share one mutation corpus, as v11's are.

## 9. The workload matrix

Every family of phase17-plus.md §4, with every method the SoC supports:
scalar, one and two workers, DOT8, DMA and the NPU. The axes:

| Axis | Values | How |
| --- | --- | --- |
| Harts and workers | 1 hart; 2 harts with 1 or 2 workers | RTL parameter (HART_COUNT); software |
| Data cache | on; off (main memory uncached through the cache's I/O path) | RTL parameter |
| Cache state | cold (the first pass after reset) and warm (a pass after a warm-up pass) | software |
| Cache geometry | 4 KiB direct-mapped (the default); 2 and 8 KiB | RTL parameter (20.5): the caches' and the CPU shell's cache model's line count, verified by the L1 tests at each size |
| Memory | the physical two-cycle memory; +1, +2 and +4 wait cycles on every answer | RTL parameter, in simulation only |
| NPU | 8×8 (64-bit port); 4×4 with a 32- or 64-bit port; one or two A strips | RTL parameters (npu.md §4.6) |
| Methods | scalar, multicore, DOT8, DMA, NPU | software |
| Placement and size | aligned and unaligned; small and large working sets; several seeds | software |

**Unsupported, with the reason:**
- an instruction cache that is off: the core fetches only through its cache;
- a 2×2 NPU: the v2 NPU is built 4×4 or 8×8;
- an 8×8 NPU on a 32-bit port: the 8×8 array requires the 64-bit port;
- an L2: the owner set it aside;
- v1's private-region permissions and warm stop, and the v1 tests of them:
  v2 has neither;
- zero-wait memory (v1's async0 idealization): the v2 memory is block RAM
  with a two-cycle read, and the caches and the NPU are built around it. The
  latency question is asked the other way, with added waits.

A manifest lists every planned combination as captured, unsupported (with
its reason) or failed. Failed and slower results stay in the report.

**Gate and comparison workloads.**
- **v1's retained workloads** (Conv2D 32×32 K=5, the reduction, the MNIST
  MLP, streaming ECG, CIFAR-10 and the CPU kernels), with v1's inputs,
  computation and measurement windows.
- **The parallel reduction** (owner, 7 October 2026): v1's reduction, but each
  worker fills and sums its own half, with the same sizes, seed, iterations
  and checksum. Hart 1 is released and has reported ready before the window
  opens. The window covers the four iterations' fills, sums, dispatches and
  joins; the one-worker run uses the same window.
- **The multicore GEMM:** npu.md §7's three cases with DOT8. The harts split
  the packing of B (half of its column blocks each), meet at a barrier, then
  each computes half of the rows of C. Packing, barrier, dispatch and join
  are all in the window, as the one-hart run's packing is.
- **ECG:** a pipeline whose stages overlap (on two harts with the DMA and the
  NPU), proven by the engines' and harts' busy intervals. If that cannot be
  shown, it is labelled sequential per chunk.

## 10. Verification

Phase 18's and 19's method, extended to two harts and a shared fabric.

### 10.1 The fabric shell (20.0–20.1)

The fabric is verified on its own: seven random requesters, each obeying its
port's protocol, with random readiness and request patterns. Checks:
- a reference memory, updated at each write's acceptance edge, against which
  every answer is compared;
- answers in order and on time;
- the bank rule; nothing lost or duplicated;
- each AMO's atomicity, and each sc's outcome against the reservation rules;
- every write's snoop delivered to every other data cache in the cycle
  after the write takes effect (an AMO's at acceptance + 3), and no spurious
  snoop;
- the longest wait.

Coverage bins:
- every pair of requesters conflicting;
- two accesses in a bank, a read beside a write, and a read held back from
  the unit being written;
- an AMO holding its bank, and an AMO held back by another's pending write;
- a reservation ended by each kind of writer, and sc success and failure;
- four banks written in one cycle;
- reset of hart 1's requesters with requests in flight.

As before: edge cases, self-tests the shell must reject, and a campaign of
planted bugs. The data cache's three snoop ports are tested in the CPU
shell's L1 tests: simultaneous snoops, and a snoop on each port of a line
being refilled.

### 10.2 The memory checker

In the SoC's simulation a checker keeps a reference memory updated at each
write's acceptance. It checks every load each hart retires against the value
its word held when the load was performed (§4.7):
- for a hit, at its cache's lookup;
- for a miss, at the acceptance of the refill read of its word;
- for an I/O access, an lr or an sc, at its acceptance;
- for an AMO, at its write.

It checks AMO results, sc outcomes and reservations in the same way. This
tests coherence and the memory model end to end, under any interleaving. The
cores' RTL is Phase 18's, verified instruction by instruction against Spike;
with two harts, each hart's RVFI trace is still checked for internal
consistency, as lockstep.py does.

### 10.3 Litmus and multicore tests

- **Litmus tests:** v1's eight modes, ported, and RVWMO's two-hart shapes:
  - MP, SB, LB, S, R and 2+2W, with and without fences;
  - the coherence shapes CoRR, CoWR, CoRW and CoWW;
  - lr/sc and AMO counters under contention.

  Each shape runs thousands of trials with random delays. No forbidden
  outcome may appear, and the counts of the allowed outcomes are published.
- **Hart 1 reset** at random points, with requests, AMOs and reservations in
  flight.
- **Self-checking workload programs**, whose outputs are compared with
  independent oracles (Python references, as v1's were).
- **Coverage, beyond §10.1's:** both harts' AMOs to one word in consecutive
  cycles.

### 10.4 Regression

- **18.7's 99 programs** run on hart 0, with hart 1 held: in lockstep with
  Spike, and cycle for cycle as in the CPU shell (§4.3's lone-hart rule).
  They run on a build of the SoC whose register page is the shell's plain
  memory, as the Phase 19 SoC's page was. Phase 19's exceptions carry over:
  - `rv32ua/lrsc` differs from Spike at an sc Spike failed and the shell's
    reservation let succeed;
  - `selfcheck/dot8_arith` is self-checking, never compared with Spike;
  - `traps/m1_traps` is not run, because its "outside memory"
    (`0x4000_0000`) is the NPU's page.
- **Phase 19's gate programs** produce the same results and console as in
  the Phase 19 SoC, in the same cycles. Any cycle that differs is traced to
  a bank conflict between the hart and the NPU, which Phase 19's fixed
  priority resolved the other way, and is reported.
- **Determinism:** each release-critical simulation is captured once and
  repeated once. Each board capture is made on at least two boots
  (phase17-plus.md §5, gate 4).

### 10.5 The DMA shell (20.3)

The engine is verified alone, as the NPU was:
- random jobs of every length from 0 up and every alignment pair;
- range and overlap errors;
- random back-pressure, abort at random points, and reset;
- an oracle that compares the whole memory after every job: the destination
  equals the source, nothing else changed, and an aborted job's BYTES_DONE
  prefix is complete;
- an exact cycle model on memories that answer on time;
- counters audited against per-job sums, and planted bugs.

### 10.6 Timing

- The fabric meets 10 ns out of context (20.1).
- Each SoC milestone meets 10 ns in context, with Vivado 2025.1 and 18.7's
  flow and sign-off.
- The final image runs on the PYNQ-Z1 at 100 MHz, each program cycle for
  cycle as in the SoC's simulation (20.5). Phase 21 then makes the formal
  100 MHz closure, with its acceptance subset.
- On the board, with two harts:
  - the console is §7.4's buffer;
  - a program ends at hart 0's store to tohost, taken from its retired
    stores as in Phase 19;
  - the measurement window is the counters' command word.

## 11. Gates

| Gate | Requirement |
| --- | --- |
| Correctness | All required workload outputs match independent oracles; §10's checks pass. Functional acceptance is judged apart from performance: a correct run that misses a performance gate stays correct, and no performance result is claimed from a run that fails a check |
| Records | Comparable AsterBench v12 records for every supported method; the matrix manifest complete |
| Overlap and totals | Multicore overlap proven from per-hart counters; the NPU's and the DMA's byte and cycle totals reconciled with the records |
| Wins and losses | Every design-space result is published, slower ones included |
| Scaling (owner) | 2 workers ≥ **1.8×** over 1 on the parallel reduction and on each of the three GEMM cases with DOT8 |
| Against v1 (owner) | Every workload v1 retained has a v2 method faster than v1's best: in cycles (v1 ran at 31.25 MHz in simulation, v2 at 100 MHz, so wall time would favour v2 further); against v1's `sync1` numbers, its physical memory model; with the same inputs and window. The CPU kernels compare with v1's `aster_minimal` numbers, v1's only measurement of them |
| ECG | Stage overlap shown, or the pipeline labelled sequential per chunk |
| Timing and area | 10 ns in context for the image with every engine; at most 80% of LUTs, block RAM and DSPs |

## 12. Milestones

| Milestone | Scope | Exit |
| --- | --- | --- |
| 20.0 | The fabric shell, the memory checker, the litmus harness; a serial one-bank reference fabric as the shell's first DUT | Every self-test rejected; the reference fabric passes in every mode |
| 20.1 | The banked fabric (§4); the data cache's snoop ports | Random and edge tests pass in every mode with every bin; planted bugs caught; the L1 tests pass; 10 ns out of context |
| 20.2 | The two-hart SoC: the fabric, both cores, the NPU, the devices (§7, §8), the runtime's dispatch and join | §10.4's regression; litmus, the memory checker and reset tests on two harts; 10 ns in context |
| 20.3 | The DMA engine (§6) and its shell; v1's driver, unchanged | §10.5; the DMA against CPU copies, at every size and alignment, in the SoC; 10 ns in context |
| 20.4 | The workload matrix (§9): every family ported, AsterBench v12, oracles, the runner and manifest | The correctness, records and overlap gates; the scaling and v1 gates measured |
| 20.5 | Tuning from the captured data: DMA thresholds, cache geometry, partitioning and placement; ECG; the board run | Every §11 gate; the final image on the PYNQ-Z1 at 100 MHz, on two boots; the Phase 20 report and its closeout audit |

## 13. Approval

**Decided by the owner, 7 October 2026** (before any design work; recorded in
phase20.md):
- banked main memory with each core's private caches, which snoop;
- the full workload matrix;
- the plan's exit with 1.8× scaling and every v1 workload beaten;
- the scaling gate's reduction filled and summed in parallel;
- the multicore GEMM: npu.md §7's cases with DOT8;
- a new 64-bit DMA behind v1's registers.

**Approved by the owner, 7 October 2026:** this specification, as reviewed,
including two points that depart from the letter of earlier documents:
- §4.6's argument for writes accepted at the same edge, which refines cpu.md
  §9's snoop wording;
- §9's window for the parallel reduction. Hart 1 is released and ready
  before the window opens, whereas v1's reduction releases it inside its
  window. This favours the scaling gate, and it is set now, before
  measurement. v1's version keeps v1's window.

Changes found necessary during the phase are recorded here as clarifications
or owner decisions, as npu.md §9 records Phase 19's.

### Clarifications in 20.0 (the fabric shell)

Found necessary while building the shell (phase20.md, Milestone 20.0). Each
refines this specification without changing its contract:
- **§4.5, a reservation and an lr in one cycle:** a write ends only the
  reservation on the word it touches. An lr in that cycle reserves its own
  word, which the write cannot touch (the unit rule). An exception or a
  reset in an lr's cycle wins over the lr.
- **§4.3, a lone hart's exception:** it covers a store, any sc (whatever its
  outcome) and an AMO, at its acceptance as well as its write, on the unit an
  instruction refill reads in the same cycle.
- **§4.3, accesses outside main memory:** the fabric shell tests the error
  path (its errors mode). In the SoC the caches, the NPU and the DMA never
  send one, which the SoC's simulation asserts.
- **§4.3 and §4.5, timing:** an error bit comes only in the cycle after
  acceptance, with the answer at the usual time. WAIT delays every answer,
  the I/O bus's included, but not the error bit. A data cache whose AMO is
  accepted in cycle c has nothing else accepted in c+1 and c+2, as in
  Phase 19.
- **§4.1, the I/O bus:** one access a cycle, always taken, and answered in
  the next cycle without an error (the data caches filter what reaches it).
  It carries the hart's number.
- **§4.5, an AMO's hold:** "until that write" includes the write's cycle:
  neither its data cache's next request nor another AMO in its bank is
  accepted then.
- **§4.6, one write a writer:** each writer, its AMOs' writes included, makes
  at most one write a cycle, since each snoop port carries one line a cycle.
- **§4.4, fairness, as checked:** no requester is accepted ahead of a waiting
  one, in its bank, more than a limit set for each fabric. The limit is 16
  for the reference fabric, whose rotation moves one requester a cycle (its
  worst in 144 runs: 14); 20.1 sets the banked fabric's from its round robin.
- **§4.1, the ARM side and the counters:** the ARM side has no port of its
  own. While the harts are held, the SoC (20.2) presents its accesses on D0's
  port with the fabric out of reset and hart 0's `hart_rst_n` high; the cores
  and their caches are held by their own resets. The fabric counters of §8
  come out of 20.1's fabric as event outputs; the reference fabric has none.
- **§6, the DMA's error bit:** the engine reads its ports' error bit only
  in the cycle after an acceptance, as the NPU does (cpu.md §5 gives the bit
  no meaning in other cycles, and the fabric shell checks it only there).
- **§10.3, the litmus shapes:** 26. LRSC_PEER's forbidden outcome, an sc
  failing with no write to its word, is this design's contract (§4.5), not
  RVWMO's, which allows spurious failures.

### Clarifications in 20.1 (the banked fabric)

Found necessary while building and timing the fabric (phase20.md, Milestone
20.1):

**§4.4, arbitration — approved by the owner, 7 October 2026.** Each port of each bank has
its own round-robin arbiter over a fixed group of four requesters, the two
arbiters working in parallel:
- port A: the data caches (every operation), the DMA's writes (W) and the
  NPU's writes;
- port B: the instruction caches, the DMA's reads (R) and the NPU's reads.

Two rules keep the bank and the contract whole:
- **No starvation.** Port B's pick waits when port A's pick writes the same
  8-byte unit. In the next cycle port A takes no write to that unit, so the
  held-back read goes through and no requester waits forever. A first
  version without this let a read wait indefinitely while two writers kept
  writing its unit; the review found it.
- **A second chance.** A data cache's load (a refill's read; lr, sc and the
  AMOs stay on port A) that port A did not take may use port B, when no
  port-B requester presents one for that bank. So the two harts' refills of
  one bank proceed together.

What it costs: two accesses that the bank rule would allow together now take
turns when they belong to one group and no second chance applies. For
example: a data-cache read beside a DMA or NPU write, or beside the other
cache's write, in one bank while port B is busy; or an NPU or DMA read beside
an instruction refill.
- The lone-hart rule (§4.3) holds: a hart's data and instruction caches use
  different ports.
- Measured in the fabric shell's traffic, against the first design (any two
  compatible requests, the second chosen after the first):
  - random traffic: 4.0% fewer requests accepted (mix) to 9.4% fewer
    (dense);
  - both data caches refilling the same lines in step (the twin mode, two
    harts' refills of shared data, as in a two-hart GEMM): as many as the
    first design and the serial reference, where without the second chance
    16.4% fewer;
  - against the serial reference: 8.6% more in mix and 5.0% more in dense.
    With every requester on one bank (the hammer mode), about half, since a
    real bank serves two accesses a cycle and the reference any number.

The reason is timing. The first design missed 10 ns by 7.5 ns; this one meets
it with +0.384 ns out of context.

**§4.4, the fairness limit:** 12 overtakes for the banked fabric. Its worst in
312 saved runs, the hammer mode included, was 8; unfair arbiters (planted)
reach 24–212 in the hammer and edges modes.

**§2, area:** measured, the fabric is 3,622 LUTs, against ~3,000 estimated. A
data cache's two extra snoop ports cost 997 LUTs, about 2,000 for both caches
against ~1,200 estimated. So §2's total grows by about 1,400 LUTs, to about
40,700 (77%): under 80%, with about 1,850 LUTs of room.

**§10.6, timing in context — a prepared fallback, not adopted.** In the SoC
each request crosses 1–2 more LUTs of its requester's logic before the
arbiters, and readiness 2–3 more after (a harness with such front ends,
`timing_fabric_fe.sv`, times them: phase20.md, 20.1). If 20.2's in-context
build misses 10 ns on the request-to-bank path, the fallback is:
- register the banks' address, data and enables after the arbiters;
- read the block RAM without its output register.

What it keeps: answers still come two cycles after acceptance (the
bank-to-answer path has 3.7 ns of slack to absorb the slower read); every
access lands in its bank one edge after acceptance, for all requesters
alike, so the memory order is unchanged.

What it costs: an AMO's write would land one edge later and its hold grow by
a cycle. A lone hart's AMOs would then take a cycle more than in the CPU
shell (§4.3), so the fallback would need the owner's decision. It helps only
the request-to-bank path, not request to readiness or to the reservations;
for those the levers are D_ON_B off (the twin traffic's 16% back) and the
reservation-ended event registered a cycle later.

### Clarifications in 20.2 (the two-hart SoC)

Found necessary while building and verifying the SoC (phase20.md, Milestone
20.2). None changes a cycle of the approved design except where it says so.

**§4.4, the arbiters as synthesized — a defect in 20.1's fabric, fixed.**
Each round robin indexed its members by a size cast of an `int`
(`2'(int'(ptr) + s)`). IEEE 1800 keeps a cast's signedness, so members 2 and 3
were indexed as −2 and −1. Vivado synthesized them as never granted: in each
bank, port A served only the data caches, and port B only the instruction
caches. Verilator treats the index as unsigned, so every simulation (the
fabric shell, its mutants, 20.1's measurements) saw the intended fabric, and
no check failed. Synthesis then removed what that made unused: the NPU's whole
datapath, and the DMA's and NPU's write data. 20.2 found it when the
in-context build came out at half its expected area. Consequences:
- 20.1's timing (+0.384 ns alone, +0.327 ns with front ends) and area (3,622
  LUTs) were measured on that netlist. Corrected, with the index an unsigned
  2-bit sum: the fabric alone **−1.425 ns** (4,634 LUTs), with front ends
  **−1.906 ns**; in the SoC the fabric is 5,793 LUTs. So 20.1's "10 ns out of
  context" does not hold for the fabric as specified. 20.2's timing work
  (phase20.md) starts from these numbers.
- The SoC's build now checks the synthesized netlist. It fails if any of
  these is gone, since synthesis removes each when its port's data goes
  unused: the NPU's block RAMs, its writer's data registers, or the ARM
  side's write-data registers, which only port W reads. A first version of
  this check looked for port pins, which the synthesized hierarchy does not
  keep, and so checked nothing; the review found it.
- No other cast in the RTL is used as an index of a signed value.

**§4.1, each requester's target.** Every requester tells the fabric whether its
request is main memory's, from its own registers, and the fabric decodes no
address for that (20.2's timing):
- a data cache: a refill, or a cacheable access (`d_req_main`);
- an instruction cache: always, since it answers a fetch outside main memory
  with an error itself (`i_req_main` = 1);
- the NPU: decided as its buffer takes the request (`n_req_main`);
- the ARM side on R and W: always, and the DMA's engine from 20.3 (`r_req_main`,
  `w_req_main`).

An access whose flag is 0 is still answered with an error and has no effect,
as §4.3 requires. The fabric asserts in simulation that every flag agrees with
its address. The SoC's testbench also checks every data cache's flag
against its address (MAIN_FLAG); the fabric shell, which has no caches,
derives each flag from the address.

**§4.1 (20.0's clarification), the ARM side.** It uses ports R (reads) and W
(writes) while the harts are held, not D0's port: that keeps a multiplexer out
of every data-cache request. From 20.3, R and W are shared with the DMA, which
is held then too. Its writes are snooped on each cache's port 2. An ARM access
to main memory completes on the AXI side once the fabric has answered it, so
none is owed when the next begins, with any WAIT.

**§3, the device windows.** In the device build the data caches' word-only
I/O windows are exactly the device pages: `0x2000_0000`–`0x2000_3FFF` (UART,
timer, hart control, counters), `0x2000_4000`–`0x2000_4FFF` (interrupt
controller), `0x3000_0000`–`0x3000_0FFF` (DMA) and `0x4000_0000`–`0x4000_0FFF`
(NPU). Any other address outside main memory faults in the cache, as §3
requires. An unmapped offset inside a device page reads 0 and ignores writes,
as the NPU's page does. The regression build keeps the shell's 64 KiB register
page, as the Phase 19 SoC did.

**§7, the I/O bus's devices.** The devices register the bus's request and
answer from that register in the next cycle, as §4.1 requires. A device write
therefore takes effect at the end of the cycle after its acceptance. No
program can tell: each data cache's next I/O access is accepted only after
the answer. The NPU's register port takes the request from a register loaded
at acceptance (the owner's second-round decision, below) and answers from it
in the same cycle, so its answer keeps its cycle. Its writes, START among
them, take effect at the end of the cycle after acceptance, like the other
devices'. Its reset is registered beside it (it leaves reset a cycle after
hart 0).

**§7.3, the mailboxes.** As in v1: hart 0 alone writes TO_HART1 and hart 1
alone writes TO_HART0. Both clear when hart 0 holds hart 1 (v1 cleared them
when it stopped hart 1).

**§8, the counters.**
- **The command word:** hart 0's, its low byte exactly 1 (START), 2 (FREEZE)
  or 4 (RESUME), as in v1. Any other value is ignored.
- **ABI 4's metadata:**
  - 0x8C reads 3 (caches on, synchronous memory);
  - 0x90 reads 4 words a line and 0x94 256 lines;
  - 0x98, the memory's wait cycles, reads 1 + WAIT: v1's synchronous memory
    read 1, and the v2 memory is the same two-cycle block RAM.
  - Counter 0 (cycles) is the window's cycles, the same for both harts.
- **Width:** counters count in 48 bits and read as 64, as §2 allows.
- **Timing:** each event is counted from a registered copy of the signal it
  comes from, so no counter's logic lies on the fabric's or a cache's paths.
  The fabric's and snoop events count two cycles late, the others one
  (measurement only). A longest wait saturates at 65,535 cycles.
- **Invalidations:** a data cache now invalidates an sc's or AMO's line from
  the time the atomic heads it, not at its acceptance (20.2's timing; every
  cycle is the same). So a snoop of that line while the atomic waits finds it
  already invalid, and is not counted as an invalidation.
- **The fabric's counters** take 0x2000_3300–0x2000_34FF, not 0x3300–0x3400:
  48 counters of 8 bytes do not fit in 256. Their order:
  - 0–6 accepted requests and 7–13 cycles waited, per requester (I0, D0, I1,
    D1, N, R, W);
  - 14–17 each bank's reads (loads, lr, refills, AMOs), 18–21 its writes
    (stores, sc, AMOs), 22–25 its conflicts (cycles a request to it waited);
  - 26–31 snoops delivered and 32–37 lines invalidated, cache 0's ports 0–2
    then cache 1's;
  - 38–39 each hart's reservations ended by another requester;
  - 40 AMOs;
  - 41–47 each requester's longest wait, in cycles, in place of §8's single
    longest wait.

  Metadata at 0x2000_34F0: the ABI (1), the count (48), and whether the
  counters are running.
- **Reading the counters:** the ARM side reaches main memory (while the harts
  are held), the console and the AXI registers. It does not reach the devices,
  and every counter resets at each start. So a program prints its records
  before it stores to tohost, as v1's and Phase 19's do.

**§10.3, hart 1's resets.** The reset tests hold hart 1 at random points,
5,334 times over four seeds on each device build:

| What a reset caught | Without the added waits | With three added waits |
| --- | ---: | ---: |
| Answers owed | 520 | 1,041 |
| An AMO before its write | 69 | 55 |
| A refill | 37 | 36 |
| A reservation | 315 | 350 |

A release before the answers owed at the hold are due cannot happen from
software: each store to SECONDARY_RUN waits for its answer, so the release
comes at least three cycles after the last of them, with any WAIT. That case
of §7.3 is covered by the fabric shell's reset mode (20.1).

**§10.4, Phase 19's gate programs on the regression build** (`make
soc-gates`, in `soc-tests`). Their results and console records are the same
as in the Phase 19 SoC, apart from their cycles, and every NPU job checks
against the reference.

Phase 19 had one memory: its refills on a port of their own, its data cache
always ahead of the NPU on the other. In Phase 20's banks:
- the NPU waits behind refills in its bank;
- a hart can wait behind the NPU;
- the NPU no longer waits behind data accesses in other banks.

The testbench attributes every NPU wait, and counts the harts' waits behind
the NPU, from the fabric's own arbitration. Without the NPU's request buffer
and register stage:

| Program | Cycles against Phase 19 | NPU waits | Hart waits behind the NPU |
| --- | --- | --- | ---: |
| MNIST | the same | none | 0 |
| Faults | the same | none | 0 |
| GEMM gate | +4 (3,451,511) | 4, behind refills | 4 |
| Coherence (it races the hart against the NPU) | +206 (7,591,418) | 406: 403 behind data accesses, 3 behind refills | 348 |

As built, with the buffer and register stage:

| Program | Cycles against Phase 19 | NPU waits | Hart waits behind the NPU |
| --- | --- | --- | ---: |
| MNIST | +264 | none | 0 |
| Faults | the same | none | 0 |
| GEMM gate | +120 | none | 0 |
| Coherence | +565 | 418: 412 behind data accesses, 4 behind refills, 2 behind an AMO | 370 |

On the final RTL, against the build without them, the buffer alone
accounts for MNIST +264, the GEMM gate +116 and the coherence program +320,
and the register stage for the coherence program's +39; neither changes the
fault program. In every case:
- where a program measures the CPU's own code (the GEMM gate's CPU GEMMs,
  MNIST's CPU inference), those cycles are Phase 19's;
- no NPU wait is unexplained.

`scripts/soc_gates.py` checks all of this and pins each program's difference
and conflict counts to these traced values, so any change must be traced
again. Phase 19's own gates (utilization, speedup, MNIST) pass on the Phase
20 SoC as built.

**The runtime's dispatch and join** (`software/runtime/aster_smp.h`): a job
(a function and its argument) is published in coherent shared memory and a
sequence number. Hart 1 spins on it in its own cache, and a snoop wakes it.
A round trip with an empty job takes 69–95 cycles, 72 on average.

**Decided by the owner, 7 October 2026 (20.2's timing).**
- **§4.4, 20.1's second chance removed.** A data cache's load no longer uses
  an idle port B; it waits for port A. It cost about 1.9 ns at 10 ns, in the
  fabric and in context. In cycles it cost:
  - 0 when two harts sum a shared array from cold caches;
  - 5 of 18,549 (0.03%) when both read one word of every line in step;
  - in the fabric shell's synthetic twin traffic, 16.7% fewer requests
    accepted.

  The fabric shell's twin rule (TWIN_SERIAL) now applies only to a DUT that
  can take both loads at once: the reference, or a fabric run with
  `+second_chance=1`.
- **§2 and §5, the NPU's request buffer.** The NPU's requests reach port N
  through a two-entry buffer whose readiness and output are registers. The
  NPU keeps three accesses in flight. The buffer answers an access outside
  main memory with the error itself, in the cycle after taking it, as the
  NPU expects; the NPU never makes one, since it checks its descriptors
  first. Each access's answer comes a cycle later. Per job that is a few
  cycles on the dense GEMMs and up to +100 on a job of many short reads
  (N = 1, 784×1×25). On Phase 19's gate programs, on the final RTL and
  against the same build without it: the GEMM gate +116 cycles (5 jobs),
  MNIST +264 (65 jobs), the coherence program +320 (+0.003% to +0.015%);
  with the register stage too, +116, +264 and +359. It removed about 1.9 ns of
  NPU paths through the fabric's grant.
- **Timing margin:** the owner asked for good margin, not a thin pass. With
  these two decisions the SoC sat at about 0 ns in context, and 20.2
  continued with restructuring that keeps every cycle (each proven by the
  lockstep equivalence and regression suites), aiming at +0.3 ns or better
  (the outcome: the third round, below).
- **20.1's sign-off** is re-confirmed, with its timing correction recorded
  above. Its timing is now carried by 20.2.
- **Second round:** 20.2 pushes on for margin, the cores' logic included. The
  NPU's register port may be registered, at +1 cycle per NPU job: its
  register writes, START among them, would take effect a cycle after their
  acceptance.
- **Done:** the register port registered (`NPU_REG_Q`, the default; the NPU
  answers from the register in the same cycle, `aster_npu2`'s `RSP_COMB`).
  With the request buffer, against Phase 19 on its gate programs: +120
  cycles on the GEMM gate (+0.0035%), +264 on MNIST (+0.015%), +565 on the
  coherence program (+0.0074%), none on the fault program. The register's
  own share (the same build with `NPU_REG_Q` 0 and 1; the same on the final
  RTL): none on the GEMM gate
  (5 jobs) or MNIST (65 jobs), whose poll loops absorb the cycle, +39 cycles
  over the coherence program's 162 jobs, none on the fault program. It
  removed the 185 near-critical endpoints of a data cache's I/O write into
  the NPU's registers.
- **Third round (the owner, 7 October 2026): 20.2's timing is signed off on
  r19's build.** It has +0.309 ns at 10 ns in context, built with
  `make fpga-aster-soc` (the default directives), with no cycle cost and no
  contract change. The margin holds for that build, not across placements:
  the same RTL gave +0.05 to +0.10 ns on other strategies. Timing is to be
  re-checked when 20.3's DMA adds its paths. The cycle-costing fallback
  (§10.6: the banks' inputs registered, answers at 3 + WAIT) stays measured
  and unadopted: about +0.12 ns, at +0.73% on the CPU kernels.

### Clarifications in 20.3 (the DMA)

Found necessary while building and verifying the DMA (phase20.md,
Milestone 20.3):
- **§6 and §4.1, the ports R and W:** their registers are the DMA's. While
  the harts are held, they carry the ARM side's main-memory accesses (loaded
  as each access starts). While the harts run, they carry the engine's.
  Nothing is multiplexed after the registers, so the fabric still sees
  requests that leave from flip-flops. A start or a stop resets the fabric
  and drops the ports' requests with it, so no request of the engine's
  outlives its run into the ARM side's.
- **§6, the engine's reset:** the run's, a cycle after hart 0's, as the
  NPU's. The DMA is present in both builds. In the regression build, which
  has the shell's page in place of the devices, its counters never count
  (that build has no counter window) and its interrupt goes nowhere (no
  interrupt controller).
- **§6, the answers:** the engine relies on the fabric answering each read
  exactly 2 + WAIT cycles after its acceptance (§4.3), and asserts it. A read
  can then go out in the cycle an answer comes back, so two in flight
  sustain one read a cycle at WAIT 0. BYTES_DONE counts the writes answered,
  which in order are the destination's completed prefix.
- **§6, the descriptor's checks** are registered from the registers every
  cycle. A START is processed at least two cycles after a configuration
  write, because each data cache makes one I/O access at a time, the next
  after the answer; this is asserted.
- **§8, the DMA's counters (ABI 5):** v1's order and meanings, read for two
  ports:
  - the offered-request wait cycles count each port's (0–2 a cycle);
  - completed reads and writes are answers, backing reads and writes are
    acceptances;
  - payload bytes are each accepted write's byte enables (up to 8 a cycle);
  - invalidated lines are both data caches' snoop hits on W's snoop port.

  They count as the fabric counters do, whose events reach the devices
  through two registers. An event of cycle t counts if the counters add in
  cycle t + 2. They add while the window is open, but not in START's,
  FREEZE's or RESUME's cycle. That rule is v1's, its DMA counters' too
  (`aster_dma_perf.sv`), so a RESUME while already counting drops that
  cycle's events from every counter. The devices export the rule as one
  signal (`window_adds`), which the DMA counts on; START clears them.

  So over any window:
  - the DMA's reads and writes accepted equal the fabric counters' R and W;
  - its stalls equal their waits on R and W;
  - its invalidated lines equal their snoop hits on W's port.

  The SoC's DMA program checks all three exactly, under contention and with
  RESUMEs during a job.
- **§4.1, the NPU's request buffer (20.3's timing):** it is emptied with the
  fabric at a start or a stop and takes nothing while the harts are held. So
  its request to the fabric is a register's (the buffer not empty), with no
  gating by the run. While the board's reset is high, the fabric sees the
  same requests of N in every cycle. In the board reset's first cycle the
  fabric may take the buffer's head, as it may take any port's registered
  request.
- **The data caches' answer (20.3's timing):** the head's answer to the core
  is kept in a register, formed with the stage-1 ready's from the next
  state. It is asserted equal to its definition in every cycle. The core's
  cycles are unchanged: `make core-shell-equiv` runs 879 programs the same,
  cycle for cycle, as 20.2's.
