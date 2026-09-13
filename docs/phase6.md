# Phase 6: coherent RAM and full RV32A

Status: **in progress; not an implementation or hardware completion claim**.
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
- [ ] Two private coherent D-cache banks, ownership/dirty-data/reset rules,
  coherent instruction reads from RAM and coherent atomic transactions.
- [ ] Dual-hart RAM-backed C runtime and compiled atomic workloads; complete
  directed, randomized, progress, latency, reset and compatibility matrices.
- [ ] Versioned AsterBench coherent/atomic experiments, validated per-hart
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
