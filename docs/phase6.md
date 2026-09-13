# Phase 6: coherent RAM and full RV32A

Status: **in progress; first coherent hardware checkpoint passed, final acceptance pending**.
Baseline: clean Phase 5 closeout `29fbe34`, implementation `71e2570`.
The Phase 5 evidence audit and directed retirement regression passed before
Phase 6 changes. This document is the implementation and acceptance contract.

## Scope and sequence

Keep the pinned PicoRV32 vendor unchanged. Add Aster-owned PCPI, memory and
coherence logic to support **RV32IMA/ilp32**, not just AMOs. Preserve existing
RV32IM single-/dual-hart builds, maps, tests and AsterBench v2/v3 captures.
Shared L2, DMA, custom compute, interrupts, an MMU and an OS are not this phase.
The Linux ARM host still loads bare-metal RISC-V programs through PYNQ/PCAP.

Complete and commit/push these tested milestones in order:

- [x] Architecture and acceptance contract written (this document).
- [x] Real PicoRV32 PCPI feasibility: operands, native memory/MUL/DIV overlap,
  exactly-once results/retirement, prolonged waits, faults and reset.
- [x] Full RV32A against a serialized uncached memory backend, including a
  reservation monitor and independent instruction-level reference checks.
- [x] Two private coherent D-cache banks, ownership/dirty-data/reset rules,
  coherent instruction reads from RAM and coherent atomic transactions.
- [x] Dual-hart RAM-backed C runtime and compiled atomic workloads; complete
  directed, randomized, progress, latency, reset and compatibility matrices.
- [x] Versioned AsterBench coherent/atomic experiments, validated per-hart
  measurements, independent results and clean-source reproducibility.
- [ ] Clean-source FPGA signoff and repeated real PYNQ jobs/boots over Linux.
- [ ] Evidence audit, fresh-checkout verification and pushed final closeout.

A milestone may reveal a necessary design change. Update this contract with
the evidence before building on it; never silently reduce full A to a subset.
Replacing the CPU or modifying its pinned vendor implementation requires a
separate decision with the user if the owned integration cannot meet the ISA.

## Memory and execution environment

Ranges are start-inclusive/end-exclusive. No cached/uncached address aliases
are introduced. Cache enable is an elaboration choice, not a live mode switch.

| Address range | Access | Phase 6 cache / atomic policy |
| --- | --- | --- |
| `00000000–00010000` | Both harts RX | ROM I$; data bypass; atomics fault |
| `10000000–10008000` | Both harts RWX | Coherent shared D$; word atomics allowed |
| `10008000–1000c000` | Hart 0 RWX only | Coherent private D$; owner word atomics allowed |
| `1000c000–10010000` | Hart 1 RWX only | Coherent private D$; owner word atomics allowed |
| `20000000–20001000` | UART, hart 0 writer | Uncached MMIO; atomics fault |
| `20002000–20003000` | Per-register hart control/mailboxes | Uncached MMIO; atomics fault |
| `20003000–20004000` | Performance/control banks | Uncached MMIO; atomics fault |

Check permissions before lookup, allocation, snooping or side effects. A denied
peer-private address must not succeed because a cache happens to hold a tag.
The existing non-atomic denied-read-zero / ignored-store bare-metal behavior
is retained unless explicitly versioned otherwise. Atomic accesses instead
have the fault behavior below. Naturally aligned bytes/halfwords/words and
the existing native misalignment side-effect guard remain supported.

RAM execution bypasses I$ but **must read the latest coherent D$ value**, not
stale backing RAM. ROM I$ contents remain read-only between stopped-loader
sessions. There is no general self-modifying-code/FENCE.I extension claim.
Compiler `fence` instructions and all legal A ordering encodings are accepted;
no store buffer allows them to pass an earlier unfinished memory operation.

## Full A semantics and faults

Implement `lr.w`, `sc.w`, `amoswap.w`, `amoadd.w`, `amoxor.w`, `amoand.w`,
`amoor.w`, `amomin.w`, `amomax.w`, `amominu.w`, `amomaxu.w`, with all four
`aq`/`rl` combinations. AMOs return the old word and commit the corresponding
new word; addition wraps modulo 2^32. Signed and unsigned extrema differ.
Register aliases and `rd=x0` must not suppress a memory effect. LR requires
encoded `rs2=x0`; unsupported widths/encodings remain illegal instructions.

Each hart has one reservation containing an aligned physical word address,
independent of cache residency. LR replaces it. SC consumes it regardless of
success, returns zero on success or one on reservation failure, and performs
no store on failure. The reservation must match the most recent LR address.
Every committed overlapping write invalidates matching reservations, including
byte/halfword writes, ordinary M-hit stores, AMOs and writes of the same value.
Conservatively invalidate the writer's matching reservation as well. Reads,
LR by another hart, and clean capacity evictions do not invalidate reservations.
Context/warm reset invalidates that hart's reservation. Future non-CPU writers
must join this monitor; no DMA coherency support is claimed yet.

SC checks alignment and permissions **even without a valid reservation**.
Misaligned LR raises load-address-misaligned; misaligned SC/AMO raises
store/AMO-address-misaligned. Aligned inaccessible LR raises load-access-fault;
aligned inaccessible SC/AMO raises store/AMO-access-fault. Check alignment
first when both fail. These are fatal traps in Aster's bare-metal execution
environment, not software-visible M-mode exception handlers: expose persistent
trap, typed cause and fault address; do not advertise privileged CSRs. Faults
must neither retire nor access memory/MMIO, nor masquerade as failed SC.

The first implementation is stronger than RVWMO: one global memory-operation
linearization order, preserving each hart's instruction memory-operation order.
AMO read/modify/write is indivisible within this order. Acquire/release are
therefore honored by completion ordering, including MMIO fences; this is not
evidence of testing every weak-memory implementation permitted by the ISA.
Do not acknowledge an atomic instruction before ownership, data and its
result/side effect are settled. Instruction prefetch is not a data operation.

Give competing requesters bounded service when backing memory responds.
Reservations survive unrelated traffic and eviction, enabling constrained
LR/SC-loop eventual progress. Exercise the ISA's <=16-instruction constrained
loops and distinguish system progress from per-hart starvation freedom under
continuous conflicting writes. Finite randomized tests supplement, not replace,
the argument that idle contenders and read-only traffic cannot force perpetual
SC failure. Backpressure forever from an external sink cannot promise progress.

## PCPI integration boundary and feasibility gate

PicoRV32 already exposes PCPI from `aster_picorv32`; internal MUL/DIV also use
its coprocessor machinery. An owned atomic unit claims only legal word A
instructions, captures operands, asserts `pcpi_wait` promptly, holds a stable
backend request through any stall, and produces one result with `pcpi_ready`.
Hold the completed result until the core drops `pcpi_valid` to prevent duplicate
transactions. Reset gates request/response outputs immediately.

PCPI provides neither a memory port nor an external exception input. Aster
must arbitrate atomic commands with native traffic, including outstanding
instruction prefetch, without patching the vendor. The fault candidate is:
record typed fault/address, perform no memory access, then release PCPI wait
without ready so the core's unsupported-instruction timeout enters its fatal
trap. Real-core tests must prove no retirement, no side effect, correct fault
instruction identity and stable halt. If this cannot meet the contract, stop
at the feasibility gate and resolve the integration, rather than returning a
fake atomic result. An adapter-only test cannot establish this feasibility.

Upstream RVFI observes PCPI register retirement but its native-memory trace
does not describe an external atomic memory transaction. Add owned atomic
commit observations and correlate them to retirement; do not mislabel native
RVFI memory fields as a complete A-extension formal trace. Native requests
already admitted to a stalled fabric must remain stable; preserve the wrapper's
one-cycle data fault qualification and its existing retirement regression.

## Coherence architecture

Use a snooping MSI-like protocol with one private D-cache bank per hart,
initially direct-mapped, 16 lines of four words. These are real write-back,
write-allocate caches of shared and permitted private RAM, not the Phase 5
write-through caches with shared accesses still bypassing them.

For the first implementation, a single protocol controller checks both banks'
tags and owns every state transition. It completes one admitted operation at a
time, including hits. There is no directory or shared L2. Serializing hits is
an intentional verification/performance tradeoff; independent parallel local-hit
paths are not required for this phase and must not be implied in measurements.
Round-robin selection changes at completed operations, not every stalled cycle.
Native versus PCPI arbitration must also finish outstanding prefetch safely.

| Stable state | Permission and authoritative data |
| --- | --- |
| I | No usable copy |
| S | Read-only clean copy; backing RAM and any peer S copy agree |
| M | Sole valid writable copy; its bank, not backing RAM, is authoritative |

Read miss: snoop the peer; a peer M owner supplies latest data and writes back
before becoming S, then requester installs S. Otherwise fill from backing RAM.
Write or successful SC/AMO: obtain exclusive ownership, transfer/write back any
dirty peer data, invalidate the peer, then commit into requester M. An own S
upgrade must invalidate the peer before acknowledgement. Dirty victim eviction
writes every word before replacing its tag. Clean eviction needs no writeback.
RAM instruction reads and cache-disabled reads use the same snoop authority;
they may obtain latest dirty data without allocating an I$ or another alias.
The cache-disabled configuration uses the same serialized atomic/reservation
rules with no valid D-cache lines.

In-flight states distinguish victim drain, peer intervention, fill, ownership
grant and commit. A line is not externally usable merely because a partial
fill wrote its tag. Hold request owner/address/data/masks and backing payloads
stable until accepted. One atomic command may require many lower transfers;
never release transaction ownership between its read and write. Backing writes
are cache-maintenance transfers, not fresh architectural stores and must not
spuriously invalidate LR reservations. Monitor architectural commit separately.

Invariant checks must cover transient as well as stable states: never two M
owners, never M plus peer S, never acknowledge stale data, never replace an
unwritten dirty victim, never commit a denied access, never acknowledge an
operation twice, and never lose an accepted transaction under backpressure.

### PCPI boundary checkpoint

`make pcpi-probe retirement` passes with the pinned vendor unchanged:

- Real-core probe: 4,744 programs, 4,072 backend responses, 3,544 successful
  exactly-once PCPI completions, 24 destructive component-reset probes and
  84,791 cycles of simultaneous native instruction fetch and atomic request
  (`0xa57e6` seed). Five latency policies include 19/65-cycle and random waits.
- Adapter unit: 32,768 opcode/funct3/funct5 combinations and 44 legal
  operation/order combinations stalled for 80 cycles and held completed for
  40 cycles without duplicate backend requests or unstable results.
- Native six-fault/four-latency retirement regression remains passing.

The fault candidate works with the actual pinned core: local alignment faults
and backend-supplied access faults preserve typed cause/address/instruction,
do not retire or return PCPI ready, and reach a persistent core trap. Register
writeback, `rd=x0`, aliases and native MUL/DIV continue to work. The test's mock
backend supplies results and access-fault classifications: it does **not**
prove actual permission decoding, atomic memory semantics, reservations,
shared-port arbitration, cache coherence or safe warm-stop flushing. Those
remain acceptance gates, not inferred from this checkpoint.

### Uncached atomic bring-up checkpoint

`aster_atomic_hart` now serializes native prefetch/data and PCPI requests onto
one stable port. `aster_atomic_fabric` fairly selects a hart and keeps ownership
through both AMO halves. It checks RAM ownership/alignment, implements all nine
word AMOs, tracks independent word reservations and emits separate architectural
store/atomic/SC outcome observations. It treats every ordering encoding with
the same stronger completion order. It can later sit above a coherent cache
service without mistaking that service's writebacks for new CPU stores.

The following initial checks pass; caches and physical validation remain open:

- `make atomic-fabric`: three seeds, four backing-latency policies, **171,415**
  independently modeled operations. Coverage includes signed extrema, overflow,
  every nonempty write mask, same-value ABA invalidation, nonoverlapping writes,
  LR replacement, failed SC consumption, read-only progress, hart reservation
  clear, memory-map boundaries/permissions, faulting SC without reservation,
  malformed backend operations, fairness and contended AMO increments.
- `make atomic-runtime-matrix`: real one-/two-core models, four latency policies
  and two boots each (**16 boots / 48 jobs**). RAM starts nonzero and survives
  subsequent uncached resets. The existing protected-stack startup runs C built
  explicitly with `-march=rv32ima`; 32-bit C atomics are lock-free. All nine AMOs
  with all ordering bits, LR/SC, byte-write invalidation, constrained LR/SC
  increments, C11 compare-exchange and lock-protected counters/sums pass.
- Each real-core A retirement is matched to a prior completed atomic command
  and its read/write/SC outcome history. The host independently checks each
  job's RAM-published counter/sum/epoch results; UART success text alone is not
  acceptance. Every full-A operation appears in actual retirement observations.

This is deliberately an uncached bring-up test top with a C++ backing-memory
and minimal peripheral model, not a shipping Phase 6 SoC/bridge ABI. Full
cache-enabled tests, adversarial warm-stop draining, stronger progress/litmus
coverage, integrated negative programs, public reference ISA tests, benchmark
provenance and FPGA evidence remain required before final full-A closeout.
The expanded default `make -j2 check` passes with these additions, including
the existing single-core, multicore, fault, parallel and Linux-bridge regressions.
This is not yet a rerun of every historical configuration matrix.

### Coherent-controller and integrated-fault checkpoint

`aster_coherent_cache` implements the two-bank write-back/write-allocate MSI
service below the atomic fabric. A single controller snoops both banks; dirty
victims/interventions drain all words before replacement/downgrade, partial
fills remain invalid, and word/byte stores acquire sole M ownership. The
atomic fabric still owns architectural permissions/reservations and remains
locked across an AMO's two cache-service operations. Maintenance traffic is
observed separately from architectural atomic reads/writes.

`aster_atomic_hart` optionally uses the existing private ROM I$ implementation.
RAM fetches reach the coherent service and see its latest dirty data; they do
not allocate into I$. Cache-off build targets still bypass the new service.
This is a simulation integration, not yet the lifecycle-aware FPGA SoC.

Verified initial coverage:

- `make coherent-cache-matrix`: cache off/on, 1/4/8-word lines, 1/4/16 lines,
  three seeds and four latency policies: **54 test invocations / 671,112
  requests / 7,560 flushes**. Independent logical memory/backing memory and
  cache observations check latest-data authority, clean RAM agreement,
  transient ownership/shared-copy assertions, every nonempty byte mask,
  dirty victims/transfers, RAM instruction reads, and selective flush retaining
  the other bank. Flush requested during an admitted operation waits for it.
- `make coherent-runtime-matrix`: another **16 boots / 48 jobs**, on one/two
  real cores with private I$ and coherent D$ and four physical-port latency
  policies. All A operations retire and C11/LRSC/locked results match the host's
  independent checks. Actual SC retries occur under two-hart contention. The
  probe explicitly flushes the quiescent completed workload before its next
  reset and checks the resulting backing RAM; this does not establish a general
  host RUN/secondary-stop protocol for arbitrary in-flight programs.
- `make atomic-faults-matrix`: **6,864 actual-core negative programs** across
  cache off/on, each physical hart, all A operations/order bits, three latency
  policies, misalignment, inaccessible ROM/MMIO/peer-private/unmapped RAM and
  illegal `.D` encodings. SC faults with and without a preceding successful LR.
  Exact pre-fault retirement, persistent fault PC/opcode/cause/address, memory
  transfers and both dirty cached/backing data are checked; a subsequent poison
  store never executes. Fault fixtures use destructive component reset, not
  the future RAM-preserving warm-stop protocol.
- The uncached runtime matrix was rerun after adding the optional caches and
  continues to pass all 16 boots / 48 jobs.

Remaining gates still include a lifecycle-aware SoC and host bridge, adversarial
warm-stop/reset at arbitrary transaction phases, complete higher-level coherent
workloads/litmus/progress tests, measurement ABI/provenance, final compatibility
matrices and clean-source physical validation. Do not load the current Phase 5
overlay expecting this new ISA/cache/lifecycle contract.
The expanded `make -j2 check` passes again with the coherent-cache unit and
integrated uncached fault regression included alongside all default legacy tests.

## Runtime, reset and host contract

Keep the protected 16 KiB private regions and distinct 4 KiB stacks. Hart 0
initializes shared `.data`/`.bss` once; hart 1 does not erase published state.
Use compiled aligned 32-bit C/GNU atomics for shared synchronization, not
`volatile` as a lock. Verify generated LR/SC and AMOs with `-march=rv32ima`.
Base RV32A does not promise lock-free 8-, 16- or 64-bit C atomic operations;
document supported library/width behavior instead of silently claiming it.

Write-back changes the reset contract. Introduce a controlled warm-stop
handshake: stop admissions, drain already accepted transactions, write back
modified lines, invalidate cache state/reservations, then acknowledge stopped
and hold the selected core(s) in reset. Previously acknowledged stores survive
this operation. Secondary stop preserves the peer and shared data. An MMIO
stop request must not hold the sole backing port while waiting for its own
flush; lifecycle requests and completion status must be decoupled.

Host RUN=0 requests this safe stop; it is no longer an immediate cache reset.
The loader polls STOPPED before touching ROM. Reset in the middle of an atomic
transaction must resolve the accepted operation before clearing its owner.
Power/configuration reset is distinct and destructive: FPGA reconfiguration
cannot preserve dirty BRAM/register contents. Do not call such a destructive
reset a RAM-preserving warm boot in tests or evidence.

Allocate an explicit Phase 6 bridge/measurement ABI and separate build targets
and outputs. Old loaders must reject new ABI/map/reset semantics; new loaders
must not guess compatibility. Keep the legacy single-hart and Phase 5 overlays
available. Inspect board users/jobs before download, deploy in a new dedicated
directory over verified SSH/PYNQ Linux, preserve other projects, and end held
safely stopped. No JTAG, ARM halt/reset or SD/QSPI writes are authorized here.

### Warm-stop implementation details

`aster_warm_stop` gates **new fabric admissions**, never an already captured
transaction's backing port. A selective secondary stop temporarily stalls both
harts' memory admissions, but does not reset the primary. After the fabric
reports idle, two quiet edges allow its response to pass through the PCPI
completion handshake. Unadmitted requests may then be canceled by the selected
core reset. A stable flush request/mask remains asserted until completion;
only that completion clears the selected RUN bits/reservations/mailboxes.
Global stop arriving during selective flush finishes it and then drains/flushes
the remaining core. A new RUN request cannot cancel an in-progress flush.

An explicit global stop cancels remaining console output: the UART elastic
slot is allowed to drain to a discard sink while memory completes. This avoids
deadlock if the host requests STOP with its receive FIFO already full. The
serial path resets only when the cluster reaches STOPPED. This deliberate
console cancellation must not cancel an accepted RAM/atomic operation. Normal
run and secondary reset retain normal UART backpressure and primary state.

`aster_coherent_soc` integrates the lifecycle controller, real cores, snooping
cache, atomic fabric, ROM/RAM and peripherals. It separates destructive `resetn`
from RAM-preserving `host_run`. ROM programming is accepted only when STOPPED
and RUN is false. A read-only host RAM port likewise becomes available only
when stopped; its consumer must honor synchronous RAM read latency.

Hart-control offset `0x004` now reads requested secondary RUN, not an immediate
reset wire. Effective RUN/trap bits remain at `0x00c`; firmware polls bit 1 clear
before treating the worker as stopped. Offset `0x018` reports STOPPED bit 0,
stop-busy bit 1 and flush-active bit 2. Own-hart fatal-atomic diagnostics occupy
`0x020` (cause bits 3:0, valid bit 4), `0x024` (address), `0x028` (instruction).
Mailboxes clear at completed secondary/global stop, not at the initial request.

The lifecycle checkpoint passes `make coherent-soc-matrix`: 16 combinations of
one/two cores, caches off/on and async/0, async/7, sync/1, sync/7 memory timing.
Each runs the atomic C program through three complete warm boots plus targeted
early/fill/writeback/atomic-read/atomic-write/reservation/byte-store/blocked-UART
stops (and secondary-atomic or stalled-RAM triggers where applicable). Every
stop compares **all 64 KiB** of physical RAM against a history of acknowledged
architectural stores, without destructive reset after initial POR. Attempts to
program a poisoned reset vector during RUN/drain are rejected and subsequent
warm boots still execute the correct firmware.

The second firmware runs three boots per configuration. With two cores it
resets/restarts hart 1 eight times per boot, checking published payloads, private
BSS startup, primary stack/private state, mailbox clearing and preservation of
the primary's LR reservation; the secondary reservation is cleared. The host
also checks the primary enters its reset vector only once per boot. One-core
configurations check that secondary release remains ignored. The separate
34-case sequencer unit covers held flush responses, stop escalation and a
restart request arriving during an uncancelable flush.

These prove the SoC's direct host-run contract in simulation. AXI bridge/loader
integration, arbitrary-phase expanded adversarial coverage, benchmark-level
validation and physical deployment remain separate gates.
The expanded default `make -j2 check` passes with the warm-stop, coherent SoC
and ABI 4 counter tests included. Across the 16-configuration SoC matrix there
are 244 checked global stops and 192 completed secondary resets; every global
stop is followed by a full RAM snapshot comparison. ABI 4's independent unit
scoreboard also checks frozen reads, command edges, metadata and 32/64-bit carry.

### Phase 6 performance-register contract (ABI 4)

Keep 256-byte per-hart banks at `0x20003000` and `0x20003100`. ABI 4 is distinct
from old v2/v3 layouts. Fourteen little-endian 64-bit counters occupy offsets
`0x00` through `0x6c`, in this order: cycles, retired instructions, architectural
memory transactions, I$ accesses, I$ misses, D$ data accesses, D$ data misses,
backing transactions, completed A instructions, successful SC, failed SC,
dirty interventions, invalidations, writeback words. SC attempts are success
plus failure. Faulting A instructions do not count as completed. Cache service
writebacks do not increment architectural stores or invalidate reservations.

Control at `0x80` accepts START=1/FREEZE=2/RESUME=4 from hart 0 bank 0 only,
on byte lane 0, broadcasting a common edge to both banks. As before, START
clears without counting that edge, FREEZE excludes its edge, and reads after
FREEZE are stable. Metadata: `0x84` ABI=4, `0x88` clock Hz, `0x8c` flags
(bit 0 caches, bit 1 synchronous memory), `0x90` line words, `0x94` line count,
`0x98` backing wait cycles, `0x9c` counter count=14. Unknown reads return zero.

Cycles measure a common interval even for a held worker. Other events use
actual per-hart observations. Dirty interventions/invalidations belong to the
requesting hart; backing/writeback words belong to their physical port owner
(the dirty bank's hart for maintenance). D$ access/miss counts exclude RAM
instruction reads, which still consult coherent data. The benchmark schema
must spell out these boundaries and retain raw per-hart values.

### Linux bridge ABI `0x00060001`

`aster_pynq_linux` adds an explicit `ENABLE_COHERENCE=1` configuration, requiring
one/two harts. Default configurations retain legacy ABI `0x00020001` or Phase 5
ABI `0x00050001`. `COHERENT_L1` selects caches for the new configuration only.
Vivado's module-reference shim exports both parameters; HWH preflight requires
the requested hart/coherence/cache settings as well as the unchanged clock,
reset polarity/driver and AXI-window safety checks. Old host tools reject the
new coherence/ISA/warm-stop configuration before importing PYNQ or downloading.

Offsets within the unchanged `0x40000000` / 256 KiB AXI aperture:

| Offset | Phase 6 meaning |
| --- | --- |
| `0x00` | Requested RUN; zero requests drain/flush, not immediate core reset |
| `0x04–0x3c` | Existing serial, identity, clock, hart-status and lifetime counters |
| `0x40` | STOPPED bit 0, stop-busy bit 1, flush-active bit 2 |
| `0x44` | RV32A bit 0 (set), caches enabled bit 1 |
| `0x50–0x5c` | Hart 0 atomic cause/valid, fault address, opcode, PC |
| `0x60–0x6c` | Hart 1 corresponding diagnostics |
| `0x10000–0x1ffff` | Write-only boot ROM, aligned words, only RUN=0 and STOPPED |
| `0x20000–0x2ffff` | Read-only retained RAM snapshot, aligned words, only RUN=0 and STOPPED |

Fault cause uses bits 3:0 and valid bit 4; PC is meaningful as a fault PC only
after the corresponding hart TRAP bit is asserted. Diagnostics clear on the
selected warm reset. Host RAM reads have an additional synchronous read edge;
once captured, replies remain fixed under RREADY backpressure. A concurrent RUN
that invalidates stopped state before capture produces SLVERR, not a live/stale
RAM snapshot. Snapshot writes, unaligned requests and unknown offsets fail.
RUN=1 cannot cancel a stop still in progress. Completed global stop resets the
serial path and lifetime counters; collect execution evidence before stopping.

`scripts/coherent_bridge.py` verifies actual AXI identity/configuration before
any write, validates complete firmware before requesting STOP, polls STOPPED
before loading, and provides stopped-only full-RAM reads and fault/retirement
diagnostics. It neither imports PYNQ nor selects/downloads an overlay. Physical
deployment still requires paired bit/HWH validation, signoff and board-use
inspection. `make fpga-linux-coherent` selects a separate coherent output
directory and keeps the generated reset-netlist and routed signoff gates.

The four-configuration `make linux-coherent-matrix` (one/two cores, caches
off/on) passes both firmware kinds, with two full serial boots each: 16 boots
and 48 complete RAM snapshot comparisons including the negative/race fixtures.
Tests cover split AXI channels, early restart/boot rejection, full-UART stop,
fatal atomic diagnostics, a RUN write coincident with snapshot admission, and
a held valid RAM reply while resumed firmware changes that same RAM word.
The host suite passes 42 tests including mutated HWH hart/coherence/cache
fields and protocol helpers that cannot program before acknowledged STOPPED.
These are simulation/host-unit results, not physical FPGA acceptance or a
completed board-side benchmark capture application.
The expanded default `make -j2 check` passes with the coherent Linux bridge
regression included; the legacy Linux/parallel/serial tests retain their prior
results. No board was reconfigured to establish this checkpoint.

### AsterBench v4 implementation checkpoint

[AsterBench v4](asterbench-v4.md) adds nine compiled RV32IMA workloads: AMO,
constrained LR/SC and C11 CAS counters, an ordinary-RAM lock-protected sum,
adjacent/padded per-worker counters, coherent request/reply ping-pong, an
eight-slot wrapping SPSC queue and parallel ordinary shared-RAM compute.
Independent C++ and Python oracles check values, work partitioning, returned
ticket sums and full output arrays; release/acquire publication protects the
ordinary communication data. A real secondary cold start occurs inside each
two-worker measurement window and is counted as dispatch overhead. Primary
stays running; secondary is safely stopped before each UART record.

The direct SoC scoreboard independently accumulates all 14 actual event signals
per hart and matches every reported counter exactly, including real SC retries
and coherent maintenance ownership. ELF kernel-PC retirements establish actual
per-hart execution. Every job's RAM-published eight-word result and complete
output array match independent expectations. Every global stop compares all
64 KiB of retained backing RAM to architectural-store history; every run ends
STOPPED, without destructive reset after initial POR.

`make coherent-bench-matrix` passes **216 workload/configuration combinations,
432 warm boots and 1,296 measured jobs**: all nine workloads, (harts,workers)
of (1,1)/(2,1)/(2,2), caches off/on and async/0, async/7, sync/1, sync/7 timing.
`make coherent-bench-boundaries` passes another **40 configurations, 80 boots,
240 jobs**: five atomic/coherent kernels, one/two workers, seven-item odd splits,
maximum-value seed, seeded UART stalls and (words,lines) of (2,2)/(8,2)/
(1024,2)/(2,1024), all with synchronous seven-cycle memory. Verilator 5.020
requires increased unroll limits for the 1024-line nonblocking reset-array
loops; the RTL, synchronous reset and assertions remain unchanged.
`make coherent-bench-sizes` additionally passes **108 configurations, 216 warm
boots and 648 jobs**, crossing all nine workloads, one/two workers, caches
off/on and (items,rounds,seed) of (2,1,0)/(129,16,1)/(1024,64,0xc0ffee), with
synchronous seven-cycle memory and seeded UART stalls. The 1024-item mix checks
every output word after 64 rounds, not just the checksum.

The strict v4 collector retains raw serial/RTL observations, full RAM snapshots,
actual firmware/ELF/map/disassembly, complete relevant Git-tree provenance,
compiler components/headers and model/build identities. Audit binds actual ELF
symbols and load bytes to RAM/firmware evidence, rejects omitted source files,
and requires clean source by default. Host tests mutate schemas, numeric types,
observations, artifacts, rehashed corrupt RAM/firmware and source manifests.
Development capture/audit passes; complete clean-source studies, physical
serial/reference integration and final evidence packages remain open.
After the implementation commit `bfd9a97`, fresh isolated one-/two-worker
seven-item shared-mix captures (two boots/two jobs, sync/one-wait) pass the
default clean-source/artifact audit and controlled comparison. These local
packages in `build/phase6-bfd9a97/bench/` prove the capture path, not the full
immutable final study. They preserve the source and compiler used for each run.
The expanded default `make -j2 check` passes with v4 included and all 50 host
tests passing. This is not yet the final rerun of every historical matrix.

### Early clean-source FPGA build checkpoint

Detached clean source `aee9e913fd5ce561a06cb8ab27306408cdb0c564` builds the
two-hart coherent/cache-on overlay with the mandatory generated-reset-netlist
simulation (five scenarios), HWH ABI/hart/cache/clock/reset preflight and
routed timing/DRC/routing/unconstrained gates. No board download occurred.
The physical clock is 31.25 MHz; final routed setup slack is **9.207 ns**, hold
slack **0.035 ns**. Resources: **15,357 LUTs, 15,167 registers, 32 BRAM tiles,
0 DSPs**. Build output is `build/phase6-aee9e91/fpga/linux-h2-coherent-c1/`.

- Bitstream SHA-256: `305ea8fbcae1a27e1880c5256201958426107869a68562eacf19e080516b9c60`
- HWH SHA-256: `9bab3b5b238297a921ecc23f941c12b212cc72e8042658bc428e7150911e42e8`

This establishes a synthesizable, routed checkpoint, not the final FPGA
artifact/evidence audit or actual execution of Phase 6 on the PYNQ. Benchmark
software changes do not alter its RTL; any subsequent RTL/build-input change
must be reviewed and rebuilt before a final physical acceptance claim.

### Public RV32A reference checkpoint

`vendor/riscv-tests` pins upstream revision
`2ebecad997fa58cd9e5724340ba75aa4b59bd1d0`: ten RV32UA wrappers and their
unchanged word-operation bodies (all nine AMOs plus LR/SC), the original scalar
macros and license. An explicit 22-file hash manifest guards that subset.
Only the separate owned platform environment supplies Aster RAM startup,
physical-hart selection and private-RAM result transport. No upstream test
instruction, expected value or disabled-case decision is modified.

The actual coherent SoC executes each program independently on hart 0 and
hart 1, with the peer parked outside the test. ELF-derived original test-case
PCs must each retire exactly once on the selected core. Actual A retirements
are observed (including 1025+ LR and 1028+ SC in the upstream loop); the final
case/hart/result signature and all 64 KiB of RAM after warm stop are checked.
The first full cache-off/on × async/0, async/7, sync/1, sync/7 matrix passes
**320 warm boots / 1,408 original test-case retirements**. The original LR/SC
program deliberately restricts itself to one active core; this is not claimed
as an upstream two-core contention or weak-memory test. Aster's separate tests
cover contention and the word-granule behavior skipped by that upstream test.

An owned negative fixture changes only the generated ROM's expected-value
instruction for upstream `amoadd_w` case 2. Both real cores take the original
test's FAIL path, and the harness rejects it. The final reference matrix repeats
all 320 positive boots and **16 negative failure-path checks**, both harts in
all eight cache/timing configurations. Vendor sources remain unchanged.
The default regression includes these reference programs and negative checks;
this supplements Aster's broader directed coverage, not architectural
certification or privileged-ISA support. Explicit memory-ordering litmus tests,
final historical matrices and physical/evidence closeout remain open.
The expanded `make -j2 check` passes with all 52 host tests and both reference
success/failure paths included. No FPGA RTL or board state changed here.

### Ordering and reservation-progress checkpoint

`software/tests/coherent_litmus.c` runs eight kinds of two-hart trials. The
worker remains live between trials; release/acquire epoch handshakes prevent
reinitializing shared data before the preceding trial's worker has completed.
Seeded instruction delays vary relative arrival. There is no emulation of a
third/fourth core or claim to test IRIW on only two hardware harts.

| Mode | Trial / forbidden or required outcome |
| --- | --- |
| 0 / 1 | C11 seq_cst store buffering, same/separate line: forbid both loads returning zero |
| 2 / 3 | C11 seq_cst load buffering, same/separate line: forbid both loads returning one |
| 4 | Ordinary 16-word payload published with release, consumed with acquire: every word and checksum must match |
| 5 | Native `sw; fence rw,rw; lw` store buffering: forbid both loads returning zero |
| 6 | Constrained four-instruction LR/SC increment versus peer reads, nonoverlapping same-line writes and dirty conflicting evictions: correct tickets, zero manufactured SC failures |
| 7 | Both harts increment the same word with constrained LR/SC loops: all increments/tickets present despite actual SC retries |

A primary-owned mailbox publishes **each** trial's observations. The independent
RTL host checks order, epoch, values, forbidden outcomes and ticket sums before
accepting a phase histogram. It also matches all 28 physical counter registers
to its event scoreboard, checks real kernel-PC retirement on both harts and
verifies exact successful-SC counts. Mode 6 cannot hide spurious SC failure
behind a software retry loop: the independent failed-SC observation must be zero.
Mode 7 establishes completion for this finite contention workload; it does not
promise per-hart starvation freedom under infinitely conflicting writers.
The primary stays live for every phase, the worker is safely stopped at the
end, and each complete warm stop compares all 64 KiB to architectural history.

`make coherent-litmus-matrix` passes **24 configurations / 48 warm boots /
49,152 trials**, crossing cache off/on, async/0, async/7, sync/1, sync/7 timing
and three seeds (0, 1, 0xc0ffee). No forbidden outcome or unrelated-traffic SC
failure occurs; genuine contention produces observed retries and correct totals.
`make coherent-litmus-boundaries` adds **eight boots / 128 trials** at maximum
memory wait 1024 (caches off/on, 2×2 geometry), 1024-word lines and 1024 lines
(two-item trials, two LR/SC steps, maximum seed). All gates remain enabled.
The expanded default regression includes the litmus suite.

`scripts/run_phase6_regressions.py --output <new-directory>` provides the full
22-target Phase 1–6 rerun: all historical and new matrices execute sequentially
relative to each other in a fresh build tree. It requires a complete clean Git
source manifest, records tools/commands/log hashes/exit status, retains partial
failure evidence and rejects source/toolchain changes before completion. It
never deletes an existing build/output. Unit tests cover child failure, changed
source/toolchain, partial results and non-mutating refusal of dirty/existing
input. A complete final run and its evidence audit are still required.
The expanded `make -j2 check` passes, including all 55 host tests, the ordering
suite, public ISA cases and every existing default regression.

### Fixed AsterBench study checkpoint

`scripts/coherent_study.py` defines the complete versioned simulation study:
all nine workloads, worker/cache cross, small/odd/large mix jobs, longer
communication jobs, and one independent clean rebuild. Its fixed 57 captures
retain 114 boots/342 jobs, all 28 counters and 61 controlled comparisons. The
collector keeps failure evidence; the auditor rejects missing/changed cases,
mixed source/toolchains, altered aggregates, symlink/unlisted artifacts and a
repeat that reused the original build directory. Three batch-level test groups
add plan, negative-manifest, fresh-build and failure-preservation coverage to
the independently tested per-capture ELF/ROM/serial/RAM validators.
See [the study plan and measurement caveats](asterbench-v4.md#fixed-simulation-study).
This implementation checkpoint does not claim the full study or physical
acceptance has run; their final raw evidence and audits remain required.

### Clean dual FPGA build and archive checkpoint

Detached clean source `215b2d08333d060abe3afe5874cf104ada2abfaf` produces separate
two-hart cache-enabled and cache-disabled Phase 6 overlays using Vivado 2025.1.
Both builds pass the five actual generated-reset-netlist scenarios, strict
HWH/clock/reset/ISA preflight, full routing, zero DRC/methodology findings and
setup/hold/pulse-width signoff at 31.25 MHz:

| Variant | Setup slack | Hold slack | LUTs | Flip-flops | BRAM tiles | DSPs |
| --- | --- | --- | --- | --- | --- | --- |
| Coherent caches enabled | 9.207 ns | 0.035 ns | 15,357 | 15,167 | 32 | 0 |
| Caches disabled | 9.691 ns | 0.047 ns | 12,064 | 10,824 | 32 | 0 |

There are zero unconstrained internal endpoints. The four LEDs and asynchronous
UART TX are five external ports without synchronous output-delay constraints;
the archive/auditor retains this explicit exception rather than claiming zero
unconstrained external outputs.

`scripts/coherent_overlay.py capture` archives the generated bit/HWH pair,
full build log, routed timing/utilization/routing/DRC/methodology reports,
actual reset simulation netlist and all three compile/elaborate/run logs.
Its source manifest covers the complete relevant clean Git tree. The read-only
audit binds all artifact hashes, recomputes signoff/resource claims and rejects
wrong topology/cache metadata, changed build paths, omitted evidence, failed
gates (even after rehashing a modified report), symlinks and unlisted files.
The post-build collector verifies the still-clean source worktree; it is not
an attestation system or a replacement for running the build in that worktree.
Offline board validation checks package consistency without Git; final host
validation additionally compares every source file against the recorded Git
revision. Packages are currently under `build/phase6-215b2d0/overlay-c0/` and
`overlay-c1/`. The cache-enabled overlay has now passed its first real board
checkpoint below; cache-disabled deployment and broader physical cases remain.

### First physical coherent checkpoint and full regression rerun

The complete clean-source 22-target Phase 1–6 run at `215b2d0` finishes with
every child exit status zero, stable source/toolchain and full retained logs
under `build/phase6-215b2d0/regressions/`. Cumulative target time is 2240.7 s.
The fixed 57-capture/114-boot/342-job v4 study at clean `26e18cb` independently
audits successfully, including identical fresh-rebuild ROM, records, per-hart
observations and stopped RAM. Its small contended jobs' measured slowdowns are
retained; see [AsterBench v4](asterbench-v4.md#fixed-simulation-study).

PYNQ-Z1 `10.0.0.145` was reverified through its known SSH host key. Read-only
process checks found no executing notebook kernel/FPGA job. The previous
Phase 5 overlay had CONTROL/STATUS/HART_STATUS all zero before Phase 6 PCAP.
The new cache-enabled bitstream was loaded via PYNQ 3.1.1 under Linux, without
JTAG, ARM halt/reset or SD/QSPI changes. The collector at `f4e330c` then runs
the clean `26e18cb` two-worker atomic-add reference without another download:

- Two complete warm boots, three jobs each, 64 shared atomic updates per job.
- Each job takes 2600 measured cycles. All fourteen counters on both harts
  exactly match the independently scored simulation reference.
- Each boot captures 3750 real UART bytes with equal TX/RX totals, empty final
  FIFO and clear framing/overflow/trap flags.
- Independent lifetime retirements are 433929/3288 and 434142/3288 for h0/h1.
  Primary lifetime includes unmeasured UART waiting; it is not expected to be
  identical across host-controlled boots.
- Both full 64 KiB stopped snapshots pass independent ELF-addressed job/output
  checks. Final CONTROL/STATUS/HART_STATUS are zero and STOPPED is one.

The host's default `coherent_physical.py` audit checks downloaded artifacts
against actual reference, overlay and collector Git revisions and passes.
Evidence is under `build/phase6-f4e330c/board/`; the board directory is
`/home/xilinx/aster_phase6_7d25898/`, with immutable `collector_f4e330c/` code.
The exact loaded overlay is `overlay-c1/aster_linux.bit` in that directory.

Two unsuccessful host checkpoints are retained separately, not relabeled PASS:
the initial `1b8ec6d` launch omitted a transitive parser and stopped before PYNQ
import; `7d25898` completed three correct hardware jobs but incorrectly treated
retained successful-request address/opcode payloads as faults despite clear
valid flags. An isolated-shipping import regression and a red/green diagnostic
validity regression correct those host errors. Hardware/firmware were unchanged;
all 65 current host tests pass, including strict real-fault rejection.

This is **not final Phase 6 acceptance**. All-AMO/LRSC/C11 functional execution
on the board, broader cached/uncached and one-/two-worker physical studies,
immutable closeout evidence, full requirement-to-log mutation audit and final
fresh-checkout verification still remain. The first atomic-add checkpoint does
not stand in for those gates.

## Verification and closeout requirements

1. Real-core PCPI probe, adapter unit tests and independent full-A reference:
   all operations/order bits, signed extrema/wraparound, aliases/x0, malformed
   encodings, fault types/permissions/SC checks, long stalls and reset.
2. Coherence reference memory and per-operation history: cold/hot reads, dirty
   ownership transfers and victims, byte lanes, partial fills, instruction RAM
   reads, competing requests, false sharing, reservation invalidation and
   progress. Assertions inspect states even while transactions are unfinished.
3. Real one-/two-hart C programs: coherent ping-pong, producer/consumer,
   AMO counters, LR/SC lock/counter, queue publication, repeated epochs and
   resets. Include constrained LR/SC loops, deterministic and seeded contention,
   cache-off/on, memory latencies including synchronous FPGA memory, and
   phase-boundary adversarial resets. Use public ISA tests where practical;
   identify upstream revision and any untested requirements honestly.
4. Preserve `make check` and all Phase 1–5 geometry/latency/adversarial matrices.
   Run recursive configuration matrices separately or in isolated build roots.
5. Extend AsterBench with a new strict schema and actual observed per-hart
   counts (including atomic attempts/success/failure and coherent transfers).
   Keep window definitions explicit, independent output oracles, complete
   firmware/compiler/configuration/source/raw-log provenance. Compare one/two
   workers, cached/uncached, uncontended/contended and false-sharing cases;
   report overhead/slowdowns as well as speedups. Mutation-test the validator.
6. Clean-source FPGA build: reset-netlist simulation, HWH/reset/clock/ABI checks,
   routed timing, unconstrained-path/DRC/routing gates, utilization and artifact
   hashes. Physical serial TX-to-RX captured through AXI/SSH is valid but is not
   external Pmod validation. Retain complete UART, per-hart observations and
   independent simulation reference comparisons over repeated jobs/warm boots.
7. Final requirement-to-evidence audit, immutable raw records, fresh-checkout
   verification and clean branch synchronized with origin. A successful build,
   simulated AMO, or uncached lock demo alone does not complete this phase.

## References

- [RISC-V A extension, version 2.1](https://docs.riscv.org/reference/isa/v20260120/unpriv/a-st-ext.html):
  normative AMO, LR/SC, ordering, fault and eventuality requirements.
- [RISC-V RVWMO, version 2.0](https://docs.riscv.org/reference/isa/v20260120/unpriv/rvwmo.html):
  load-value, atomicity and progress constraints; stronger implementations allowed.
- [Upstream PicoRV32 PCPI interface](https://github.com/YosysHQ/picorv32#pico-co-processor-interface-pcpi)
  and [pinned provenance](../vendor/picorv32/UPSTREAM.md): owned integration boundary.
- [gem5 MSI introduction](https://www.gem5.org/documentation/learning_gem5/part3/cache-intro/):
  stable-state terminology only; its directory transport is not Aster's protocol.
- [Phase 5 contract/evidence](phase5.md): compatibility and baseline obligations.
