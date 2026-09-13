# Phase 5: dual-hart implementation contract

Status: implementation in progress, starting from `754db0c`. This is a design
contract and acceptance checklist, **not a completion claim**. The README exit
is two independently executing cores and a correct parallel workload. Phase 6
coherence, L2, atomics, DMA and accelerators are explicitly excluded.

## Integration and compatibility

`aster_hart` contains one pinned RV32IM core behind `aster_picorv32`, with its
own optional I/D L1 pair. The single-core `aster_minimal` remains supported with
its Phase 1–4 memory map and measurement ABI. The new dual-hart SoC reuses the
same front end, memories and peripherals. `HART_COUNT=1|2` in the new SoC allows
the same runtime/workload to be measured with one or two physical cores.
Changing the hart count must not change memory layout or workload inputs.

All harts use one clock, reset PC zero, RV32IM/ilp32 and no interrupts. Hart ID
is an Aster read-only MMIO register, not an added `mhartid` CSR or vendor patch.
The ARM Linux host remains the loader; neither RISC-V hart runs an OS.

`aster_picorv32` qualifies every new data request for one cycle before exposing
it to a cache or MMIO. The pinned vendor can otherwise emit an aligned request
one cycle before trapping on the original misaligned address. Unadmitted
faulting loads/stores have no external side effects. Once admitted, a stalled
request is never withdrawn on trap: it completes to preserve fabric ownership.
The pinned vendor is unchanged. Valid data accesses now cost one extra cycle;
earlier retained timings must not be represented as this implementation's.

## Dual-hart memory map and ownership

Ranges are start-inclusive/end-exclusive. ROM and RAM retain their physical
64 KiB sizes. There are no alternate cached/uncached aliases.

| Range | Hart 0 | Hart 1 | Cache policy / use |
| --- | --- | --- | --- |
| `00000000–00010000` | RX | RX | ROM instructions use each private I$; ROM data bypasses D$ |
| `10000000–10008000` | RWX | RWX | Uncached shared `.data`, `.bss`, job buffers and results |
| `10008000–1000c000` | RWX | denied | Hart 0 private D$, working storage and stack |
| `1000c000–10010000` | denied | RWX | Hart 1 private D$, working storage and stack |
| `20000000–20001000` | RW | read-only | UART: hart 0 is the only console writer |
| `20002000–20003000` | per-register | per-register | Hart control and polling mailboxes |
| `20003000–20004000` | read / common control | read-only | Per-hart performance banks |

Denied/unmapped data reads return zero; writes have no effect. Fetches outside
permitted ROM/RAM return zero and produce an illegal-instruction trap. Private
ownership is checked for **all** lower transactions, including instruction
fetches and cache refills. Only a hart's own private data region is D-cacheable;
denied access cannot hit a private cache. RAM code bypasses I$. Each private
region boundary is aligned to the largest supported cache line (4096 bytes).
ROM stores are ignored. Byte strobes remain meaningful everywhere.

Shared memory cannot be made coherent merely by `volatile` or write-through
stores. Here it always bypasses both D$ instances. There are no store buffers:
each native operation completes before its successor can publish a mailbox.
Software uses compiler memory barriers and `fence rw,rw` around publication and
consumption. The pinned core decodes ordinary FENCE; this in-order, single-
outstanding system already drains prior operations before completing it.
These rules do not license concurrent unsynchronized writes to C objects.

## Boot, reset and lifecycle

Global reset holds both cores, invalidates their caches, clears control,
mailboxes, counters and arbiter state; RAM contents persist. Deassertion starts
hart 0 only. The runtime reads its ID without a stack, selects its own aligned
4 KiB stack, and hart 0 initializes shared `.data`/`.bss` exactly once. Each
hart initializes its own explicitly placed private BSS. The linker asserts
shared/private limits, minimum/aligned stacks and no stack/data overlap.

Hart 0 publishes initialized state before releasing hart 1. Hart 1 starts at
the same reset vector but never copies or clears shared data. It acknowledges
startup, waits for job epochs, processes its assigned slice and publishes
completion. Repeated jobs use increasing nonzero epochs and a complete
start/acknowledge/done handshake; a ready message cannot satisfy job completion.
Return from either C entry parks that hart. Traps are independently observable;
one hart trapping does not reset the other or erase evidence.

Hart 0 may hold/reset/release hart 1 through its control register. A control
write shares the same arbiter as all memory traffic, so an already selected
hart-1 transaction finishes before the reset write can be accepted. Reset
cancels hart 1's unselected cache/core requests and invalidates its caches;
it clears the two mailboxes, but does not clear shared RAM or performance
banks. Global reset may abort an unaccepted transfer; accepted stores persist.
Hart 0 is reset only by global reset (host RUN=0), which resets both cores and
the serial path. Firmware must not reset a worker while relying on its job
results. A one-hart implementation ignores release and reports hart count 1.

### Hart-control registers (`0x20002000`)

| Offset | Name | Semantics |
| --- | --- | --- |
| `00` | ID | Read-only requesting hardware hart ID |
| `04` | SECONDARY_RUN | Hart 0, lane-0 write: bit 0 holds (0) or releases (1) hart 1; read effective run |
| `08` | HART_COUNT | Read-only elaborated number of real cores (1 or 2) |
| `0c` | STATUS | Read-only bits 0/1 running, 8/9 trapped |
| `10` | TO_HART1 | 32-bit mailbox, only hart 0 writes; both may read |
| `14` | TO_HART0 | 32-bit mailbox, only hart 1 writes; both may read |

Mailbox writes merge enabled byte lanes. No read side effects, interrupts or
implicit atomic read-modify-write operations. Unsupported offsets and writes
are ignored. Reset commands recognize only lane 0; upper-byte writes cannot
change lifecycle state. UART writes from hart 1 are ignored without stalling.

## Linux deployment boundary

`aster_pynq_linux` and its Verilog IP facade use `HART_COUNT=0` for the preserved
legacy map, or 1/2 for this map. `make fpga-linux-dual` selects 2 and writes to
`build/fpga/pynq_z1/linux-h2`; the exported HWH must contain the matching
parameter. The preflight rejects a mismatched map before importing PYNQ.
The dual ABI version is `0x00050001`. ARM-only registers expose real hardware
hart count/status and two 64-bit lifetime RVFI retirement counters. They reset
on global RUN=0, not on a secondary reset, allowing the host to verify both
harts executed even when firmware finishes with the worker held in reset.
These lifetime totals include setup/idle/UART time and are **not** job speedup
measurements. AsterBench v3's common frozen job interval supplies those.
See the [bridge map and deployment commands](../fpga/pynq_z1/README.md).

## Arbitration and memory timing

A two-input round-robin arbiter multiplexes native lower requests. At idle it
chooses the preferred valid input, falling back to the other. A stalled grant
locks until acceptance, keeping owner/address/data/strobes/instruction flag
stable. Completion advances preference to the other hart. Cache refill words
are independent transfers; a whole line does not monopolize the memory port.
Only the owner sees ready/read-data. Exactly one shared side effect can commit
on an edge. No bubble is required between accepted requests, even identical
ones. Zero-wait, synchronous BRAM and configured latency obey the same rules.

Fairness is bounded in **completed transactions**: if both remain valid, neither
waits behind more than one other completion. Wall-clock progress additionally
requires the selected slave to respond. UART backpressure can therefore stall
the shared port; the Linux serial path retains its bounded-capacity credit
scheme and the host must drain it. Global reset suppresses all valid/ready and
side effects and clears a locked grant. Sources must hold requests until ready
or a coordinated reset; arbitrary unilateral cancellation is not supported.

## Measurement and parallel workload

New AsterBench records use strict version 3, not silently changed v2 records.
Per-hart banks at `0x20003000` and `0x20003100` retain the v2 counter offsets,
with ABI=3. Hart 0's accepted bank-0 CONTROL command starts/freezes/resumes
**both** banks on the same edge. Other control writes are ignored. Both cycle
counts mean common elapsed interval, including waits and worker coordination;
retirement, native transactions, accesses/misses and backing transfers belong
to the indicated hardware hart. Global backing traffic is the sum of both
banks, not elapsed cycles. DMA/accelerator counts remain zero. A missing hart
has zero events but shares the elapsed cycle count. Resetting hart 1 alone does
not clear the interval or its accumulated counters.

The initial workload splits deterministic integer work across hardware harts,
using private cached working data and uncached published inputs/results. It
must validate every result against an independent scalar/host oracle, expose
both hart contributions and use a common start-to-done measurement window.
Compare one-core and two-core jobs with the same inputs, sizes, seeds and total
work. Report coordination/copy overhead and measured speedup or slowdown; no
assumed 2x gain. Retirement observations must prove that both real cores ran.
Records/captures include source cleanliness/revision, source manifest, compiler
version/hash/flags, firmware hash, RTL configuration and full workload identity.

## Acceptance tracking

### Foundation development checkpoint

Implemented: reusable core/cache front end; round-robin arbiter; protected
shared fabric/control; one/two-hart simulation top; separate-stack C runtime;
directed/seeded fabric tests and real-core runtime/retirement tests. Legacy
FPGA targets still use the single-core map. The first runtime test is a
producer/consumer handshake, not the required parallel benchmark.

Development verification commands completed successfully:

```sh
make check
make phase1-matrix
make arbiter
make fabric-matrix
make multicore-runtime-matrix
```

The fabric matrix has 24 runs (8 configurations × 3 seeds). The real-core
runtime matrix has 16 configurations, 32 complete warm boots and, in the
dual-hart legs, independent retirement from both cores. The arbiter's three
seeded runs completed 32,111 transfers and aborted 272 stalled transfers under
reset. These development results are not a clean-revision physical closeout;
the final Phase 5 evidence must be captured with provenance at a stable source
revision. See [verification scope](verification.md#phase-5-development-tests-not-closeout).

### Parallel workload development checkpoint

`software/benchmarks/parallel_mix.c` implements the measured parallel slice;
it is distinct from the startup/producer-consumer test. Jobs use an arm/ready
barrier, a common hardware measurement start, GO publication, disjoint private
working sets, shared result publication and a completion barrier before freeze.
One-worker mode leaves hart 1 reset, even in the same two-core hardware image.
Both cores execute the same non-cloned kernel function; ELF-bounded RVFI
observations and a per-cycle counter scoreboard verify actual hardware work.
The [benchmark contract](../software/benchmarks/README.md#phase-5-parallel-mix-v3)
defines every transformation, boundary and measurement inclusion.

Strict v3 C++/Python parsers coexist with the unchanged v2 record contract.
`scripts/parallel_results.py` captures every job and both warm boots, checks
independent result references, retains raw logs and full source/compiler/
firmware/model provenance, and compares the measured cycle counts. The core/
worker/cache/memory matrix and workload-boundary tests are separate Make
targets. This software/simulation checkpoint does not close FPGA validation.

[Retained clean-revision captures](results/phase5/README.md) from `4188064`
compare one/two active workers in otherwise identical two-core, cached,
synchronous-memory simulation. Three-job totals are 121,515 and 61,569 cycles
(1.974×), with two reproducible warm boots, exact counter-event checks and
overlapping kernel retirement on the two active harts. These are simulation
results, not measured FPGA performance or a general 2× speedup promise.

The full-phase boxes stay open until the final requirement audit. Evidence
paths and source revisions will be recorded as work lands; old Phase 1–4
evidence keeps its original meaning.

- [ ] Shared front end preserves the legacy single-core regressions.
- [ ] Arbiter directed/seeded scoreboard: fairness, stability, ownership,
  exactly-once stores/MMIO, lane masks, identical requests, reset in flight.
- [ ] Dual-hart decoder/control tests: IDs, ownership, permissions, byte lanes,
  lifecycle, mailboxes, traps, held slaves and denied operations.
- [ ] Runtime isolation, poisoned RAM, initialized odd bytes/BSS, private
  stacks, repeated jobs and warm boots, secondary stop/restart.
- [ ] Real parallel firmware, independently checked per-hart contributions,
  measured one-/two-core comparison and per-hart retirement observations.
- [ ] Strict v3 parsers, rejection/mutation tests and provenance capture.
- [ ] Single/dual × caches off/on × async/sync/extra-latency test matrix.
- [ ] Full Phase 1–4 regression suite, including cache/reference matrices.
- [ ] Linux host bridge simulation with dual-core boot, UART backpressure and
  reset; preserve legacy one-core tests.
- [ ] Stable-source Vivado build; generated reset-netlist/HWH gates; clean
  routing/timing/DRC; retained utilization/signoff reports.
- [ ] Physical dual-core execution through SSH/PYNQ Linux/PCAP with real serial
  capture, repeated boots/jobs, complete reference comparisons and provenance.
- [ ] README, architecture/runtime/toolchain/verification/deployment docs,
  raw results and requirement audit; completed Phase 5 commit pushed; clean
  worktree and origin/main synchronized.

Do not close Phase 5 on simulation or an untested bitstream. Preserve unrelated
board projects; no JTAG programming or ARM halt/reset without new recovery
authorization. Use a dedicated Phase 5 deployment directory.
