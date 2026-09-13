# Phase 7: coherent memory-to-memory DMA

Status: **complete**. All seven requirements pass against the
[2,003-artifact immutable closeout](results/phase7/closeout-888c24b/README.md):
144-capture simulation and physical studies, eight separate physical functional
boots, both routed/reset/HWH images, all 22 legacy and 14 DMA regression targets,
and a fresh rebuild with 149 host tests. Clock remains 31.25 MHz; Phase 8 is
not part of this implementation.
Baseline: clean/pushed Phase 6 closeout
`70a1b55b303786144aaa052b6cd8b9e8a4d75bf1`. Before any Phase 7 edits,
`audit_phase6.py ... --current` passed all seven baseline acceptance gates.

## README contract and scope

[README Phase 7](../README.md#phase-7--dma) requires source, destination,
length, start/status and completion signaling, then CPU `memcpy` versus DMA
over increasing transfer sizes to identify the crossover. A copy performed by
a CPU loop and labeled DMA does not satisfy this requirement. No measured
crossover in the supported range is an acceptable experimental finding, not
a reason to omit slow cases or change the CPU baseline.

Keep the verified **31.25 MHz**, two pinned PicoRV32 cores and RV32IMA/ilp32
execution environment. Preserve Phase 1–6 targets/maps/ABIs and AsterBench
v2/v3/v4. Add DMA as an explicit elaboration choice, disabled in legacy
configurations. No shared L2, descriptor/scatter-gather engine, AXI DDR DMA,
interrupt controller, OS/MMU, custom instruction, accelerator or clock-speed
optimization is part of this phase. Completion is sticky and pollable; an
interrupt-capable CPU is not a prerequisite.

Use the README layout: owned engine/control logic in `rtl/dma/`, arbitration
in `rtl/interconnect/`, coherent-service changes in `rtl/cache/`, integration
in `rtl/soc/`, driver in `software/drivers/`, functional firmware in
`software/tests/`, measurements in `software/benchmarks/`, independent tests
in `verification/unit/` and `verification/soc/`, and provenance/audits in
`scripts/`. Do not modify the pinned PicoRV32 implementation.

## Milestones and exit evidence

- [x] Audit the Phase 6 baseline and specify this contract before RTL changes.
- [x] Independent DMA engine/register and fair-arbiter tests.
- [x] Coherent device accesses, reservations, permissions and safe lifecycle.
- [x] Compiled RISC-V driver/runtime plus directed/seeded integrated matrices.
- [x] Versioned AsterBench CPU/DMA sweep, independent oracles and provenance.
- [x] Full applicable Phase 1–7 regression run and clean routed FPGA signoff.
- [x] Real PYNQ Linux/PCAP DMA and CPU/DMA sweeps, repeated jobs/warm boots.
- [x] Immutable evidence, mutation-tested requirement audit, fresh-checkout
  verification and clean/pushed closeout.

Commit and push each verified milestone. A changed design must update this
contract with its evidence before subsequent stages depend on it.

The following milestone notes retain the evidence available at each step;
their intermediate test totals are not the final suite totals. The
[regression closeout](#regression-closeout-tooling), [physical report](phase7-physical.md)
and [measured crossover](phase7-bench.md#measured-physical-crossover) track the
combined result. The [current runtime guide](runtime.md) distinguishes legacy
RV32IM startup from the coherent RV32IMA/DMA execution and ownership contract.

The first implementation milestone is reproducible with `make dma-engine
dma-arbiter`. Three seeds cross three engine response-latency policies (zero,
seven, randomized), with **67,401 test scenarios / 27,882 accepted descriptors**,
complete byte-addressed RAM/guard oracles, all register lanes/unsupported
offsets, every short-transfer abort/pause edge, 1,025-cycle stalled drains and
testbench-deposited 64-bit carry/rollover. Arbiter tests cross three seeds and
four latencies, including zero-/one-/two-operation CPU groups, 129-cycle AMO
gaps, delayed settlement, random arrivals and a completed-group fairness bound.
Assertions are enabled and warnings are fatal for both owned RTL units. These
results prove units, **not** cache coherence, a real-core DMA copy or board DMA.

The next unit milestone adds `make dma-cache-matrix` (216 seed/cache/geometry/
latency scenarios) and `make dma-counters`. The device-cache oracle checks
complete RAM, every cache state/tag/data word, exact dirty-drain/destination
backing history, both M owners, two S copies, all store strobes, no allocation,
RAM instruction visibility and overlapping flush requests. DMA-disabled
`make coherent-cache-matrix` still passes 54 seeded configurations (four
latencies each). Counter tests cover independent multi-increment accounting,
common-window command priority/exclusion, all offsets and 32-/64-bit carry.
Whole-SoC reservation/lifecycle and actual-core DMA proofs remain separate gates.
`make dma-cache-boundaries` also passes 24 scenarios at 1x1024 and 1024x1,
covering the largest supported index and line-word widths independently.
DMA-disabled real-core `atomic-runtime ATOMIC_CACHE=1` and `coherent-soc` pass
their compiled atomic and RAM-preserving lifecycle checks with the new cache.

The real-core integration milestone adds `make dma-runtime-matrix`: one/two
harts, cache off/on and asynchronous waits 0/7 or synchronous waits 1/7.
All **16 configurations** pass **48 complete runtime boots**, each with 320
directed size/alignment copies, full source/destination/guard checks, driver
range/overlap/zero/timeout/abort cases, six directed LR/SC interactions and three
published jobs. Two-hart boots also perform eight selective resets during DMA
(192 total), retain the primary and deny raw secondary DMA-register writes.
Five in-flight global-stop triggers per configuration add **80 stop trials**;
every one checks all 64 KiB of retained RAM against observed CPU stores and
actual DMA destination acceptance. Raw firmware snapshots match all 42 counters.
`dma-atomic-fabric` adds all 32 A encodings/alignment faults on the DMA MMIO
page to the independent serialized-memory oracle. These are simulation results,
not a Phase 7 FPGA/timing/physical closeout.

The paired-measurement tooling milestone adds `make dma-bench` and the
[AsterBench v5 experiment](phase7-bench.md). Actual-core default and 8 KiB
different-offset development captures each pass two warm boots/four balanced
jobs, all 42 observed counters, all 108 RAM-published words per method, complete
source/destination/guard byte checks and whole-RAM architectural-store equality.
The default 64-byte aligned/cache-on/synchronous case measures CPU 1,362 cycles
versus DMA 1,672; the 8 KiB different-offset counterpart measures 398,918 versus
245,795. These are **development simulation observations**, not a complete
crossover study or physical claim. Do not use them to check off the study gate.
The 107-test host suite includes independent Python/C++ v5 record parity,
counter/ownership/ELF/ROM/RAM mutations and fixed-study/repeat/crossover audit
mutations. A fixed 144-capture plan is specified before collection; source
must be clean and stable for accepted captures. Historical Phase 6 acceptance
still passes without the inapplicable `--current` flag.

The Linux/lifecycle milestone adds `make linux-dma-matrix`. All four one-/two-
hart, cache-off/on configurations pass after the latched-global-stop change:
**16 complete runtime/publication boots and 68 retained-RAM snapshots**. These
include three in-flight global DMA stop points per configuration, MMIO fetch/
atomic denial, split/held AXI transactions and read-only host diagnostic gates.
Publication firmware performs eight epochs of dirty-source DMA copying of RAM
code, executing old and new instructions on hart 0 and new instructions on
hart 1 only after a release/acquire handoff. PC-qualified retirement checks
observe 32 primary and 16 secondary RAM instructions per two-hart boot.

Transient global STOP during a selective stop is latched even if RUN reasserts.
The new unit regression failed before the controller fix and now passes all
**58 DMA-enabled scenarios**; all **34 legacy scenarios** still pass. Actual
two-hart/cache-on and cache-off synchronous runs additionally pass four
selective/global escalation arrival points, preserving complete RAM through
separate selective and global flushes (`dma-runtime`, 12 stop acknowledgments).
The final post-change 16-configuration matrix separately passes all 48 runtime
boots, 80 in-flight global stops and 32 selective/global escalation trials;
that result is not inferred from those two initial runs.

`make linux-dma-bench` audits the actual ELF/ROM and checks v5 windows through
AXI and bit-serial UART. It saves independent CPU/DMA event and payload
observations at FREEZE, then compares each delayed serial record with its own
saved window, all 108 RAM words, frozen host diagnostics and complete stopped
RAM. Initial 64-byte aligned/cache-on, zero-byte/cache-off, 8 KiB different-
offset/cache-on and 127-byte same-offset/cache-on at **115,200 baud** all pass
two boots/four balanced jobs. The repeatable final targets
`linux-dma-bench-cases` and `linux-dma-bench-baud` cover ten configurations plus
both cache modes at physical baud. Both complete targets now pass: **24 warm
boots / 192 methods**, also independently reparsed by the Python v5 validator.
The identical-config 127-byte same-offset/cache-on direct-SoC and 115,200-baud
captures match all 16 complete records exactly. Simulation of serial circuitry is not a
claim that the FPGA has been programmed.

`scripts/dma_overlay.py` adds a distinct DMA overlay envelope and strict
six-argument build/HWH checks, retaining the existing actual routed/reset
signoff gate. Its mutation tests reject disabled/missing DMA identity,
hash-consistent failed reports, incomplete source/artifact inventories and
symlinks. No Phase 7 bitstream/physical results are claimed by those fixtures.
The host suite now passes **117 tests**, including actual compiled publication/
stop/benchmark ELF checks and the DMA host/overlay rejection gates.

Clean cache-off/on FPGA builds at revision `888c24b` now pass actual generated
reset, HWH, routing, setup/hold/pulse-width, DRC and methodology gates at
31.25 MHz. See the [physical evidence contract](phase7-physical.md) for routed
resources, safe Linux/PCAP deployment and independent physical audit rules.
At that milestone physical execution and the combined closeout were still
pending; FPGA builds alone do not complete the phase.
The tested physical collector adds seven mock-only safety/audit tests
(**124 host tests total**), including failure cleanup, read-only prior-overlay
guards, exact no-download reuse, changed-input rejection and raw evidence
mutations. These fixtures are not board results.

The first actual PYNQ cache-off/64-byte aligned pilot now passes two warm boots,
eight paired jobs, 16 raw serial records, full published RAM/buffer checks,
all 42 reference counters and the host Git-provenance audit. CPU 1,204 versus
DMA 1,443 cycles is a measured small-transfer slowdown, not a crossover claim.
The board was independently rechecked safely STOPPED at 31.25 MHz. The full
144-case physical study and functional runtime/code-publication acceptance
were separate gates and are now complete; the pilot alone did not prove them.

`scripts/run_phase7_regressions.py` fixes a **14-target DMA supplement**:
host validators, engine/arbiter/counters/stop units, cache matrices/boundaries,
atomic permissions, the complete post-escalation 16-configuration actual-core
runtime matrix, direct benchmark size/alignment/sensitivity cases, Linux
runtime/publication matrices and paired serial/physical-baud cases. It records
clean/stable source and toolchain, exact commands, complete logs and retained
failure state. It **does not replace** the existing 22-target Phase 1–6 run;
both complete audited runs are required for final acceptance.

## Architecture and coherent serialization

DMA is an uncached hardware requester on the **same coherent RAM service** as
the CPU atomic fabric. It does not masquerade as a third cached hart and must
not read/write the backing RAM behind dirty CPU lines.

The existing atomic fabric retains its complete CPU-transaction lock, including
both read and write halves of an AMO. An outer CPU-group/DMA arbiter grants
either that whole transaction or one DMA read/write transaction. CPU-group
admission is gated before the atomic fabric captures a request; DMA cannot
slip between an AMO's halves. The existing inner hart round robin remains.
Under simultaneous eligible requests, alternate CPU group and DMA at completed
transaction boundaries. This bounds starvation in completed transactions,
assuming backing memory eventually responds; it does not promise a finite
cycle bound against an indefinitely stalled external responder.

Once offered, request address/data/strobes and ownership stay fixed through
completion, including abort/stop requests. The DMA engine has explicit issue
and waiting states so pause can prevent the *next* offer without withdrawing
an existing one. Capture one result, acknowledge once, then arbitrate again.

The coherent cache service gains an explicitly tagged device path:

1. **DMA read:** inspect both matching CPU cache lines. Return the current
   word directly from a valid matching line (including M); retain its state.
   With no hit, read backing RAM. Do not allocate a DMA line or unnecessarily
   downgrade an M owner just because an uncached device consumed its data.
2. **DMA write:** if a matching M line exists, write its entire dirty line
   back first, preserving untouched bytes/words. Invalidate matching copies
   in both banks, then perform the destination byte-enabled RAM store. Do
   not allocate a CPU line for the device. S copies need no writeback.
3. Hold the coherent transaction lock throughout snoop, dirty drain and final
   read/write. No CPU access may observe a partial ownership transition.
   Unrelated dirty victims/lines are not discarded.

A successful DMA destination store is an architectural write. On its actual
RAM acceptance edge, invalidate every matching CPU word reservation, including
same-value and single-byte writes. Device reads, cache snoops and maintenance
writebacks must not clear reservations. Forward matching invalidations through
the atomic fabric's existing reservation-clear interface and observed reserved
addresses; do not simulate an unrelated CPU store. CPU instruction fetches
from RAM must see completed DMA writes through the existing uncached/coherent
RAM-fetch path. Code publication requires a software handoff, not a claim
that racing instruction modification is safe.

Keep an explicit DMA-origin tag in store/backing observations. In Phase 7,
device-initiated data traffic and snoop maintenance are recorded in DMA
counters, not silently charged as CPU instructions or CPU-issued accesses.
Legacy DMA-disabled per-hart event definitions stay unchanged.

## Address, transfer and ownership contract

Use the architecture's reserved DMA page **`0x3000_0000–0x3000_1000`**.
The page is uncached, data-only and unavailable when DMA is disabled. Native
register reads are available to either present hart; only hart 0 may write
control/configuration. Hart 1 writes acknowledge with no effect. Atomics to
this page fault, as do atomics to other MMIO. MMIO instruction fetch is denied.

Payload sources and destinations must both lie in shared RAM
**`0x1000_0000–0x1000_8000`**, end-exclusive. DMA cannot access ROM, either
private stack/runtime region, MMIO or unmapped memory. Check both *entire*
byte ranges with widened arithmetic before the first memory request. A
descriptor crossing a range boundary or wrapping 32-bit arithmetic fails
without reading or writing payload memory; checking only the first address
is insufficient.

Copy forward, supporting all 16 source/destination byte-alignment pairs:

- If both pointers are word aligned and at least four bytes remain, copy a
  word using one read and one full-strobe write.
- Otherwise copy one byte via an aligned source-word read and one destination
  byte-lane write. Matching alignments reach the word path after a short
  prefix; different relative alignments use the documented byte path.
- Tail writes must preserve every unselected byte. Reads of the containing
  aligned word remain inside the word-aligned shared-RAM region.
- Length zero is an immediate successful no-op, even for otherwise invalid
  pointers: no memory request and zero payload bytes.
- For nonzero length, reject any overlap, including identical source and
  destination. This is a `memcpy`-style engine, not `memmove`.

The driver/benchmark owns source and destination until completion. A whole
buffer copy is not atomic, and a source modified concurrently is not a snapshot.
Directed hardware tests may deliberately interleave CPU/DMA accesses and must
check their actual serialized history, not invoke undefined racing C behavior.
Use distinct memory for independent peer work and explicit publication/join
handshakes when either hart prepares or consumes DMA buffers.

## Register and completion ABI (planned DMA ABI 1)

Registers are 32-bit and word aligned, relative to `0x3000_0000`.

| Offset | Register | Access and meaning |
| --- | --- | --- |
| `0x00` | SOURCE | RW, byte address; lane writes merge only while idle |
| `0x04` | DESTINATION | RW, byte address; same rule |
| `0x08` | LENGTH | RW, byte count; same rule |
| `0x0c` | COMMAND | W: exactly `1` START, `2` ABORT or `4` ACK in low byte |
| `0x10` | STATUS | R: bit 0 BUSY, 1 DONE, 2 ERROR, 3 ABORTED, 4 REJECTED |
| `0x14` | BYTES_DONE | R: completed destination bytes for the accepted job |
| `0x18` | ERROR_CODE | R: 0 none, 1 source range/wrap, 2 destination range/wrap, 3 overlap |
| `0x1c` | ABI | R: 1 |
| `0x20/0x24` | JOB_CYCLES | R: low/high 64-bit cycles for the current/last accepted job |
| `0x28` | LIMIT_LO | R: `0x1000_0000` |
| `0x2c` | LIMIT_HI | R: `0x1000_8000`, exclusive |
| `0x100–0x16c` | DMA window counters | R: fourteen 64-bit counters, low word then high word |
| `0x180–0x19c` | Counter metadata | R: common-window running state, ABI 5, clock, flags, geometry, wait, counter count |

Only COMMAND's low byte lane acts. Mixed/unknown commands set REJECTED and
do not start/abort a job. Unsupported offsets read zero and ignore writes.
START while busy, configuration writes while busy, or ACK while busy set
REJECTED without changing the captured descriptor or canceling the active job.
START while quiescing is rejected. A new accepted START clears old terminal
flags, error and per-job accounting, captures the descriptor and validates it.
Invalid nonzero descriptors finish with DONE+ERROR and zero bytes. ACK while
idle clears sticky flags/error but does not invent a completion or copy.
Validation priority is zero-length success, source range, destination range,
then overlap. JOB_CYCLES counts rising edges entered BUSY, including its
terminal response/abort edge but excluding the accepted START edge. Immediate
zero-length and descriptor-error completions therefore report zero job cycles.
Per-job BYTES_DONE advances on completed coherent destination responses; the
window's payload-byte event is the actual destination RAM acceptance. Those
edges may differ, but agree after completion and must not double count.

Normal completion sets DONE only after every destination effect is globally
visible and the last owned response has completed. ABORT is cooperative, not
destructive: finish any already offered request, issue no subsequent request,
and report DONE+ABORTED with the exact completed prefix count. A read drained
after abort need not produce a destination write. An abort observed while busy
wins over simultaneous normal termination, even if the completed count equals
the full length. Abort while idle does nothing. The held completion level is
available for observation; no CPU interrupt delivery is claimed.

The production native RAM path has no late bus-error response. Range/overlap
errors are descriptor errors; do not claim rollback or pretend an arbitrary
backing-memory deadlock can be repaired by clearing BUSY.

Driver API: submit, poll/status, bounded wait, abort-and-wait, acknowledge and
blocking copy. Submission and successful completion include compiler barriers
and `fence iorw, iorw` ordering. The driver must not call `memcpy` to implement
DMA. Return explicit submission/range/abort/timeout results and verify ABI.

## Global stop, selective reset and POR

Global host STOP rejects new STARTs and requests a cooperative DMA abort. The
lifecycle controller waits for CPU/AMO and already offered DMA requests, DMA
quiescence, completion settlement and coherent-cache flush before STOPPED.
Keep admitted transactions stable; preserve every acknowledged CPU/DMA store.
Reasserting RUN mid-stop cannot cancel this sequence. Only after STOPPED may
device registers/per-job state reset and host ROM loading or RAM snapshots
proceed. Initial POR/FPGA programming is destructive and remains distinct.

A selective hart-1 reset **pauses**, rather than aborts, DMA. Block the next
DMA offer, drain any current one and CPU fabric ownership, then perform the
existing selective cache flush/reset. A transfer with a captured source word
may retain that pending write across the pause; resume it exactly once after
the lifecycle controller reopens admissions. Do not wait for full-job BUSY
to clear while simultaneously preventing its remaining requests. Preserve
primary state, DMA progress and unaffected reservations.

DMA configurations enable `LATCH_GLOBAL_STOP` in the lifecycle controller.
A sampled global request during **any** selective-stop stage remains mandatory
even if a direct-SoC host reasserts RUN. Never widen an already offered selective
flush mask: complete that transaction, keep admissions closed, then issue a
separate all-bank flush and reset the remaining primary. The Linux bridge also
rejects a cancelling RUN write, but that shell policy is not a substitute for
the underlying DMA lifecycle guarantee. The option defaults to zero so legacy
DMA-disabled timing/behavior stays unchanged. A new regression reproduced the
previous transient-escalation failure before this fix was implemented.

## Measurement contract: AsterBench v5

The [paired-study design](phase7-bench.md) expands this contract: four jobs with
balanced method order, two warm boots, full destination/guard serial output,
same-image CPU/DMA kernels and explicit prepared-cache/placement semantics.

Retain both fourteen-counter CPU banks at their existing addresses. Add the
separate DMA bank, with START/FREEZE/RESUME driven by the same primary-only
common-window command at `0x2000_3080`. All counters are 64-bit, freeze together
and exclude command edges, as in Phase 6. The DMA bank has this fixed ABI 5
order (each offset is its low word; the following word is its high half):

| Index | Offset | Field |
| --- | --- | --- |
| 0 | `0x100` | busy cycles |
| 1 | `0x108` | offered-request wait cycles |
| 2 | `0x110` | completed coherent read transactions |
| 3 | `0x118` | completed coherent write transactions |
| 4 | `0x120` | committed payload bytes |
| 5 | `0x128` | actual backing read transactions |
| 6 | `0x130` | actual backing write transactions (including dirty drains) |
| 7 | `0x138` | cache-read forwards |
| 8 | `0x140` | dirty writeback words |
| 9 | `0x148` | invalidated CPU cache lines |
| 10 | `0x150` | successful completions |
| 11 | `0x158` | cooperative abort completions |
| 12 | `0x160` | rejected-descriptor error completions |
| 13 | `0x168` | rejected control/configuration commands |

Metadata flags at `0x18c` have cache bit 0, synchronous-memory bit 1 and
DMA-present bit 2. Byte increments are the actual destination strobe popcount;
dirty writeback bytes are not payload bytes. Cross-check every reported field
against independently observed RTL events, including carry and reset edges.

Run CPU and DMA on the same DMA-capable hardware, with the same source,
destination, data/length/alignment, cache configuration and preparation policy.
Use a real optimized word-copy CPU kernel with byte prefix/tail (not volatile
byte copying chosen to make DMA look good). Retain compiler flags and actual
disassembly to prove that neither path was optimized away or substituted.

Primary end-to-end window: START common counters **before** CPU copy or DMA
descriptor programming; include setup, submission, polling, completion and
ordering; FREEZE afterward. Initialization, result checking, UART and global
stop are outside. `JOB_CYCLES` is a distinct engine-only diagnostic, not the
CPU/DMA end-to-end comparison. Polling is CPU work: autonomous transfer does
not mean the polling CPU was free. A separate useful-work overlap experiment
may report actual independent kernel retirement; it cannot replace this
latency experiment or credit idle loops as useful work.

Fixed primary size sweep in bytes: **0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 63,
64, 127, 128, 255, 256, 511, 512, 1024, 2048, 4096, 8192**. Test aligned and
representative same-offset/different-offset unaligned copies; functional tests
cover all alignment pairs. Cross cache off/on. Use at least three jobs and two
warm boots per accepted capture, independently rebuilt repeats and seeded data.
Retain both method orders or demonstrate an equivalent fixed preparation
state; do not cherry-pick warmed CPU or DMA instruction paths. State buffer
placement/cache-index effects and prepared-cache state explicitly. Do not
claim cold caches without a verified mechanism that makes them cold.

Compare common-window cycles; convert latency/bandwidth using the actual clock.
Report the first measured DMA win, any later reversals and alignment/cache
dependence, rather than claiming a universal crossover. Zero-byte throughput
is undefined: report its setup latency, not division by zero. Retain complete
raw output arrays and guards, not only a checksum or a PASS string. Versioned
records identify method, configuration, size, alignment, seed, job/boot and
all CPU/DMA observations. Old v2/v3/v4 parsers/captures remain unchanged.

## FPGA and physical acceptance

Add explicit DMA-capable build/serial simulation targets. The Linux bridge uses
new ABI **`0x00070001`**, with DMA feature bit 2 at `0x44` alongside existing A
and cache bits, while old configurations retain their exact identities.
Add read-only host DMA diagnostics; ARM still controls only RUN and stopped
ROM loading, not the RISC-V DMA descriptor registers. Preserve split AXI
channels, held replies, bounded UART credits and stopped-only RAM gates.

The explicit `ENABLE_DMA=1` Linux-shell/shim parameter requires coherent
one-/two-hart hardware; its default is zero. `LINUX_DMA=1` selects this
configuration in the build. HWH must contain the matching parameter, and the
handoff audit must reject a DMA image when a legacy/non-DMA image was expected.
Missing `ENABLE_DMA` is accepted only as zero for historical handoffs. Existing
Phase 2/5/6 ABI values and handoff result fields remain unchanged when disabled.

Host offsets below are relative to ARM AXI base `0x40000000`, **not** the
RISC-V DMA page. Every write to these offsets returns SLVERR, including zero
strobes; unknown/unaligned offsets also fail closed. Reads capture the current
value once and retain it under AXI backpressure even if DMA subsequently moves
or stops. No descriptor can be submitted through this diagnostic view.

| Host offset | Read-only value |
| --- | --- |
| `0x80` | DMA register ABI, 1 |
| `0x84` | Current five-bit DMA STATUS |
| `0x88` | BYTES_DONE |
| `0x8c` | ERROR_CODE |
| `0x90/0x94` | JOB_CYCLES low/high |
| `0x98` | DMA common-window counting flag |
| `0x9c` | DMA counter ABI, 5 |
| `0xa0–0x10c` | Fourteen DMA counters, consecutive low/high pairs |

Live 64-bit reads need high/low/high retry; reading the whole live bank is not
an atomic snapshot. Accepted benchmark captures read it only after the last
serial record, with counting frozen and the engine idle, and compare it with
the last method's RAM/UART record. STOPPED additionally requires idle/cleared
DMA job status and zero reset counters. Clock remains verified independently
through HWH, the bridge identity and the PYNQ clock readback.

Build clean cache-off/on overlays at 31.25 MHz. Retain generated reset-netlist
simulation, full HWH/clock/reset/ABI preflight, setup/hold/pulse-width signoff,
routing/DRC/methodology/resources and actual bitstream hashes. Before physical
PCAP, verify board identity/current image and inspect active use. Use a new
Phase 7 directory through SSH/PYNQ Linux, preserving other projects. No JTAG,
ARM halt/system reset or SD/QSPI writes without new permission.

Run actual RISC-V DMA runtime and CPU/DMA size sweeps, not Python/ARM copies.
Capture actual PL UART TX-to-RX via AXI/SSH (not external Pmod validation),
per-hart/DMA diagnostics and complete stopped RAM over repeated jobs/boots.
Require independent firmware/ELF/output/counter comparisons, retain every
failed/partial attempt and finish in independently verified STOPPED state
with Linux available. A host sleep or simulated result is not physical proof.

## Required verification and final audit

1. Engine/register model: full-byte oracle and guard regions; all alignments,
   word/tail boundaries, zero, overlap, permissions/wrap; lane strobes, busy
   mutations, sticky flags, held responses, abort at every transaction stage,
   simultaneous completion/abort and repeated starts.
2. Arbiter/cache model: CPU/AMO indivisibility, fair completed transactions,
   latest dirty data from either hart, both S copies, dirty destination neighbor
   preservation, no DMA allocation, cache-off path, no duplicated backing
   stores, exact reservation invalidation and continuously checked invariants.
3. Real one-/two-hart firmware: driver results, private-memory denial,
   publication, CPU/DMA/atomic contention, LR/SC success/failure and unrelated
   same-line traffic, selective pause/resume and global stop/retention, ROM
   reload, RAM code visibility and supported synchronous/asynchronous waits.
4. Preserve every applicable Phase 1–6 matrix and its historical audits; test
   DMA-disabled behavior as well as Phase 7. Use isolated build trees and do
   not concurrently rebuild different configurations into the same directory.
5. Strict v5 measurement/capture/ELF/provenance plus mutation tests. Execute the
   fixed CPU/DMA size study, both cache modes, alignment cases, repeated jobs/
   boots and fresh rebuilds; retain and explain the actual crossover findings.
6. Clean routed/reset/HWH FPGA gates and real repeated physical DMA/CPU sweeps
   with complete raw UART/RAM/counters and safe final state.
7. Commit a self-contained immutable evidence bundle, requirement-to-evidence
   audit and mutation tests. Rebuild and audit from fresh checkouts, inspect the
   exact committed bundle, and finish with clean main synchronized to origin.

## Regression closeout tooling

Regression closeout uses both `scripts/audit_phase6_regressions.py` and
`scripts/audit_phase7_regressions.py` on the separate clean-source manifests.
The latter checks all 14 DMA targets: host mutation tests, engine, arbiter,
counters, warm-stop, cache geometries and extreme boundaries, DMA-enabled full-A
fabric, the 16-configuration real-core runtime matrix, 42 regular and nine
sensitivity benchmark captures, four Linux runtime/code configurations, ten
serial benchmark cases and two physical-baud cases. It verifies raw records and
independent event windows where emitted, actual serial/retained-RAM gates,
ordered reset/escalation coverage, complete source/tool provenance and fixed
commands. A PASS count alone, or a partial target manifest, cannot close the
phase. Saved commands are never executed by either audit. Scenario and manifest
mutations test omitted cases, changed seeds/geometry, broken atomic and reset
coverage, reordered warm boots, damaged UART/counter evidence and false hashes.

The new clean Phase 1–6 run on DMA-capable source `888c24b` has passed its
independent 22-target / 2,397-scenario audit. The complete DMA supplement from
`694ae0e` passes all 14 targets / 569 emitted scenarios, with 130 host tests at
that revision. The final clean `b592cdc` rebuild passes 157 emitted scenarios
and 149 host tests. All actual logs pass their semantic audits; 66 damaged
legacy versions and 42 damaged DMA versions (missing, duplicate and late-failure
gates) are independently rejected without modifying the original captures.

`scripts/audit_phase7.py` is the outer seven-requirement audit. It binds the
complete nested artifact inventory, both regression manifests, routed overlays,
the full physical/simulation study, functional references/board runs, raw board
logs, the independent final stopped-state probe and a fresh `make check`.
Its manifest command refuses incomplete evidence or an existing manifest.
The audit resolves actual recorded Git blobs; no board or recorded executable
is invoked. After `888c24b`, the only allowed hardware-test change is the
optional UART/RAM/event exporter in `tb_pynq_linux_coherent.cpp`, explicitly
bound to both clean functional reference builds and the fresh checkout. Every
other RTL, firmware, vendor, FPGA, Makefile and hardware-test input must agree.
Later host validators do not imply a later FPGA build. The
[self-contained bundle](results/phase7/closeout-888c24b/README.md) passes all
seven combined gates. Reproduce the read-only acceptance audit from the root:

```sh
python3 scripts/audit_phase7.py audit docs/results/phase7/closeout-888c24b
```

Only Python 3 and complete repository Git history are required to audit saved
evidence; no board or recorded tool executable is invoked. `--current` also
requires present build/audit inputs to match the fresh verification. Omit that
flag when auditing this historical bundle after later source changes.

## References and basis

- [RISC-V A v2.1, LR/SC device-store requirement](https://docs.riscv.org/reference/isa/v20240411/unpriv/a-st-ext.html):
  DMA writes to LR-accessed bytes must invalidate a successful SC pairing.
- [RISC-V memory/I/O ordering](https://docs.riscv.org/reference/isa/v20240411/unpriv/rv32.html):
  the driver's FENCE contract includes both memory and MMIO.
- [AMD coherency discussion](https://docs.amd.com/r/2024.2-English/Vitis-Tutorials/Vitis-Hardware-Acceleration/Memory-Allocation-Concepts?contentId=r2gxJSwrLaBllFp9EJAQdA):
  background on dirty-source/stale-destination hazards. Aster implements its
  own PL coherent path; it is not using ARM ACP/CCI or a Vitis runtime.
- [Phase 6 architecture](phase6.md) and
  [audited physical baseline](results/phase6/closeout-215b2d0/README.md).
