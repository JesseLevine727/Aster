# Phase 20: whole-SoC workload placement and concurrency

Status: **milestone 20.1 (the banked fabric) complete, awaiting the owner's
sign-off and approval of soc.md §13's 20.1 arbitration change.** 20.0 (the
fabric shell) was signed off by the owner on 7 October 2026. The owner's
decisions are recorded below; the SoC specification, [`soc.md`](soc.md), was
approved by the owner on 7 October 2026. The phase sits in the [v2 plan](phase17-plus.md#6-phase-17-sequence)
after [Phase 19](phase19.md) (complete, 6 October 2026). As in Phases 18 and
19, every milestone passes its verification layer and records its timing
before the next starts, and the owner signs off each milestone.

## Goal

From the v2 plan ([`phase17-plus.md`](phase17-plus.md) §6, Phase 20): answer
the compute-placement and system-level research questions on one coherent,
timed design. Integrate two Aster cores with per-core caches that can hit in
parallel, coherence, memory banks, DMA, DOT8, the NPU, timer and interrupts,
and the software runtime. Run the complete workload matrix. Tune DMA
thresholds, cache geometry, multicore partitioning and task placement from
captured bottleneck data. Demonstrate actual stage overlap for ECG or label
the system as sequential per-chunk processing. Keep functional and
performance acceptance separate.

The plan's exit: all required workload outputs match independent oracles;
comparable records exist for every supported method; multicore overlap and
NPU/DMA byte and cycle totals are proven; all design-space results preserve
both wins and losses.

## Decided by the owner, 7 October 2026

Asked before any design or measurement:

- **Memory system: banks and private L1 caches.** The 96 KiB main memory is
  split into independent banks behind a crossbar (the Phase 17 memory point
  already calls for independent banks). Each Aster core keeps its own
  instruction and data caches, so hits run in parallel. Each data cache
  snoops every other master's writes, the snoop contract of cpu.md §9. The
  options set aside: one shared main-memory port (Phase 19's, with a second
  core), and a shared L2 cache (it adds latency and area to on-chip block
  RAM without adding capacity).
- **Workload matrix: the full catalog.** Every workload family of
  phase17-plus.md §4, with every method the v2 SoC supports — scalar, one
  and two cores, DOT8, DMA, the NPU — and the survey's axes the design can
  be configured for: the NPU's geometry and options by parameter, caches on
  and off, added memory waits in simulation. A combination the design cannot
  run is listed with its reason. The option set aside: the v1 catalog on
  the final SoC only.
- **Exit gate: the plan's exit, with stricter scaling.** Besides the plan's
  exit (above):
  - two workers reach at least **1.8×** over one on the parallel reduction and
    on the multicore GEMM;
  - every workload v1 retained has a v2 method faster than v1's best method
    for it (the same window and inputs).

  The options set aside: 1.6× scaling, and the plan's exit alone.

Asked the same day, before measurement, once the v1 workloads were read:

- **The reduction the scaling gate measures: fill and sum in parallel.** v1's
  reduction (`workload_reduce.c`) refills its 1,024-word array on hart 0
  alone inside the timed window, and only the summing is split, so two
  workers reach about 1.3× on it whatever the hardware (v1 measured 1.29×).
  The gate is measured on a version in which each worker fills and sums its
  own half: the same sizes, seed, iterations and checksum. v1's version is
  still run and reported, so the comparison with v1 stays like for like. The
  options set aside: timing the summing alone, and leaving the reduction
  out of the gate.
- **The multicore GEMM the scaling gate measures: npu.md §7's cases with
  DOT8.** 64×64×64, 96×96×96 and 128×64×128, each hart running the best CPU
  code (DOT8) on half of the rows, against one hart running all of them —
  the cases and code of the NPU's gates. The options set aside: v1's
  cross-engine multicore GEMM (the scalar kernel at v1's sizes), and both.
- **DMA: a new 64-bit engine with v1's register interface.** v1's engine
  copies a 32-bit word about every six cycles (about 0.7 bytes a cycle),
  which the Aster core's own copy loop beats at every size. The new engine
  keeps v1's registers (its driver and workloads unchanged), moves 8-byte
  units with two requests in flight (about 4–8 bytes a cycle), and is
  verified in its own shell against a reference, as the NPU was. The option
  set aside: v1's engine on the new fabric unchanged.

## Verification architecture

soc.md §10:
- **20.0–20.1:** a fabric shell with seven random requesters, a reference
  memory, and order, atomicity and snoop checks. Its first DUT is a serial
  reference fabric.
- **From 20.2:** in the SoC's simulation, a memory checker tests every load
  each hart retires against the global order of writes. The SoC also runs:
  - litmus tests on two harts;
  - hart-1 reset tests;
  - self-checking workload programs against independent oracles;
  - 18.7's 99 programs in lockstep with Spike, cycle for cycle as in the
    CPU shell.
- **20.3:** a DMA shell with an oracle and a cycle model.
- **Each milestone:** timing at 10 ns.

## Milestone 20.0: the fabric shell (7 October 2026)

**Exit gate met** (soc.md §12: every self-test rejected; the reference fabric
passes in every mode). `make fabric-tests fabric-checker-tests litmus-tests`
(all in `make check`) and `make fabric-mutants`.

**The fabric's ports** (`verification/fabric/ref_fabric.sv`, which 20.1's
fabric keeps):
- I0, I1: word reads;
- D0, D1: op, address, data, byte enables, and three snoop ports each;
- N: 8-byte units, writes with byte enables;
- R and W: the DMA's 8-byte units;
- the I/O bus, with the hart's number;
- per hart, its reset and its exception.

The ARM side and the fabric counters are decided in soc.md §13 (20.0).

**The serial reference fabric** (`ref_fabric.sv`) is the contract built the
simplest legal way, as the shell's first DUT. 19.0 put v1's NPU in the NPU
shell the same way. It is behavioural and not meant for the FPGA:
- one memory array: any number of reads a cycle and one write in all;
- never a read and a write of one unit together;
- an AMO takes the write slot until its own write;
- priority rotating by one requester a cycle.

**The fabric shell** (`tb_fabric.cpp`, `fabric_ref.h`, `shell_fabric.sv`) has
seven random requesters, each obeying its port's protocol. A requester may
offer a new request in the cycle an answer returns, and a hart held in reset
keeps offering requests, which the DUT must ignore. Against an independent
reference, cycle by cycle, it checks:
- every answer's timing and data, and every sc's outcome under Spike's
  reservation rule;
- the error bit in the cycle after each acceptance (cpu.md gives it no
  meaning in other cycles);
- every snoop, and no other;
- the I/O bus;
- the unit rule; the bank rule for a banked DUT, whose banks must equal what
  the run declares (`+banks`);
- the AMO rule through the AMO's write cycle, and one write a writer a cycle;
- starvation (more than 256 cycles), and fairness: no requester accepted
  ahead of a waiting one, in its bank, more than a limit (16 for the
  reference);
- in the solo modes, the lone hart never slowed but for its AMO's hold and
  the unit exception;
- at the end, the whole memory read back.

Its modes are mix, hot (eight shared lines), stream, dense, sparse, errors,
reset, solo and solo1 (hart 0 or hart 1 alone, half their accesses on four
lines both caches share). Each runs at WAIT 0, 1, 2 and 4.
- **Results:** 36 configurations, 4 seeds × 500,000 cycles each, 140 million
  accepted requests, all passing.
  - The longest wait is 64 cycles; the most overtakes 14.
  - A lone hart waits at most 4 cycles: its AMO's hold, then the unit
    exception.
  - Every coverage bin that applies to an unbanked DUT is hit, 6 to 26 a
    mode, including:
    - reservations ended by each kind of writer (another hart's store, sc
      or AMO; the NPU; the DMA), by an exception and by a reset;
    - both harts' AMOs to one word a cycle apart;
    - hart 1 reset with answers owed, with an AMO in flight, and released
      before a dropped answer was due;
    - each case of the lone-hart unit exception;
    - every snoop port.
  - The bins only a banked fabric can hit (every pair of requesters
    conflicting, two accesses in a bank, a read beside a write, four banks
    written at once) are required from 20.1.
- **Self-tests:** all 12 reported:
  - 5 at the DUT's boundary: a starved requester, spurious snoops, a false
    error bit, a lone hart slowed, a DUT reporting four banks;
  - 7 in the shell's own view: misread data, a missed snoop, a late answer,
    a misread sc outcome, kept reservations, wrong AMOs, a corrupted
    reference byte.
- **Planted bugs** (`scripts/fabric_mutants.py`): 31 mutants of the reference
  fabric, all caught. They cover:
  - the AMO hold;
  - the unit rule and the AMO rules;
  - snoops late, on the wrong port, to the writer, or of the wrong line;
  - every reservation rule;
  - answers kept across a reset, and a held hart's requests accepted;
  - fixed priority;
  - the I/O bus;
  - the error bit;
  - AMO operations;
  - a lone hart's refill held behind its own read.

**The memory checker** (`mem_checker.h`): each hart's retired loads are
checked against the reference's word at each load's perform point
(soc.md §10.2); a device load is checked against its device's answer; a
reset drops a hart's loads in flight. 20.2 wires it to the data caches'
lookups and the harts' RVFI. Its self-tests accept 6 correct event streams
and reject 6 planted faults: a stale value, a value never written, loads out
of turn, a load never performed, a wrong byte under the mask, a wrong device
value.

**The litmus harness** (`software/tests/litmus_v2.c`, `scripts/litmus.py`):
- **Shapes:** 26 two-hart shapes:
  - MP, SB, LB, S, R and 2+2W, each with and without fences;
  - CoRR, CoWR, CoRW, CoWW;
  - lr/sc and AMO counters, and lr/sc under traffic to the reserved word's
    line;
  - v1's eight modes, ported: SB and LB under C11 seq_cst (in two lines and
    in one 8-byte unit), publication, a native fenced SB, and lr/sc;
  - MP and SB with fences in one unit.
- **How it runs:** each trial runs after a random delay on each hart. Every
  instruction is inline assembly but for the seq_cst shapes, whose point is
  the compiler's lowering: `fence rw,w` before the store, `fence rw,rw` and
  `fence r,rw` around the load. The counts are dumped as a signature, and the
  classifier fails on anything RVWMO forbids (or, for LRSC_PEER, this
  design's no-spurious-failure contract), on any read outside a shape's
  possible values, and on missing trials.
- **On Spike with two harts:** 26 × 2,000 trials, nothing forbidden. Spike
  interleaves every 5,000 instructions, so its outcomes are sequential; it
  checks the program and the harness. The RTL runs come with the two-hart
  SoC (20.2).
- **The classifier's self-tests:** 363 planted forbidden, impossible or
  missing outcomes rejected, and a clean run accepted.

**Reviews.** The milestone's watchdog review planted 25 bugs in the
reference fabric; the shell as first built caught 21. The four it missed:
- an AMO hold one cycle short;
- fixed priority;
- a lone hart's refill held behind its own read of the same unit;
- error bits in cycles with no acceptance.

The first three were holes, now closed, among them:
- the AMO rule checked through the write cycle, and one write a writer;
- a fairness check;
- solo modes on shared lines, for both harts.

The fourth is not a fault: cpu.md gives the error bit no meaning outside the
cycle after an acceptance. The review's other findings, all fixed:
- bins that could be hit for the wrong reason;
- the bank count taken from the DUT itself;
- litmus keys that could alias;
- the litmus shapes soc.md §10.3 asks for that were missing (unfenced S, R
  and 2+2W; v1's eight modes) and its thousands of trials;
- the memory checker's device loads and resets.

Three self-tests (2, 10 and 11) could be made blind by a change in stimulus;
they are now deterministic.

**Found while building it**, each fixed:
- the reference fabric's first version ended a hart's *new* reservation when
  another requester wrote the word of its old one in the lr's cycle (found by
  the hot mode);
- the shell's lone-hart check first excused only writes that took effect;
- solo mode blocked the final read-back port.

**Timing:** none in 20.0: nothing in it is for the FPGA. The fabric meets
10 ns from 20.1.

The clarifications of soc.md this milestone found are recorded in soc.md §13
("Clarifications in 20.0").

## Milestone 20.1: the banked fabric (7 October 2026)

**Exit gate met** (soc.md §12: random and edge tests pass in every mode with
every bin; planted bugs caught; the L1 tests pass; 10 ns out of context).
`make fabric-tests fabric-mutants core-aster-l1-unit core-aster-l1-tests
timing-fpga-fabric`. The arbitration below changes soc.md §4.4 and awaits the
owner's approval (soc.md §13, 20.1).

**The fabric** (`rtl/fabric/aster_fabric.sv`, `aster_fabric_bank.sv`) keeps
the reference fabric's ports and contract.
- **Banks:** four line-interleaved banks of 64-bit block RAM. Port A takes
  the bank's write (or a read); port B only reads.
- **Arbitration**, per port of each bank: a round robin over four
  requesters, the two in parallel, each grant one LUT:
  - port A: D0, D1, the DMA's W and the NPU's writes;
  - port B: I0, I1, the DMA's R and the NPU's reads.
- **Two rules:**
  - a write on port A that holds back port B's read of its unit blocks
    writes to that unit for a cycle (no starvation);
  - a data cache's load may use an idle port B (the second chance).
- **AMOs:** each bank computes its AMO's new value for both halves of the
  unit, from registers loaded the cycle before.
- **Reservations and snoops:** both come from each writer's grant and its
  own request.
- **The counters:** the event output `ev_resv_end`, a reservation ended by
  another requester, on both fabrics, checked every cycle.

**The data cache** (`aster_l1d`) gains `SNOOPS` snoop ports: 1, as before,
in the Phase 19 SoC; 3 here.

**Verification:**
- **The fabric shell, on both fabrics** (`make fabric-tests`): 12 modes × WAIT
  0, 1, 2, 4 × 4 seeds — 192 runs a fabric. The random modes run 500,000
  cycles each.
  - **The new random modes:**
    - hammer: every requester on one bank at a time, on four units;
    - twin: both data caches loading the same lines in step. It also checks
      a performance rule of this design: both caches' loads to one bank,
      with nothing else asking, are both taken (TWIN_SERIAL).
  - **The directed modes:**
    - edges, 15,000 cycles of scripted scenarios. Its seeds change only the
      ghost requests and the garbage data, so it is effectively one run a
      WAIT.
  - **Results:** every check passes, with every bin that applies. The banked
    fabric's 192 runs accept 173 million requests; its bins include every
    pair of requesters conflicting, two accesses in a bank, a read beside a
    write, and four banks written at once. The 12 self-tests are reported
    on both fabrics:
    - self-test 12 now reports a banked fabric with half its banks;
    - self-test 9 is now caught a cycle earlier, by the reservation event.
- **The edge scenarios**, 13 of them:
  - all seven requesters on one bank, after a lone request moves that bank's
    pointers;
  - four banks written at once;
  - one unit read and written together, by four pairs of kinds;
  - a read of a unit that two harts and the DMA keep writing;
  - both harts' AMOs on one word a cycle apart, with a write held and reads
    of the word;
  - a reservation ended by each kind of writer (another hart's store, sc and
    AMO, the NPU, the DMA) and kept by a write beside it;
  - lr with an exception, and lr before a reset;
  - sc without a reservation;
  - hart 1 reset with an AMO in flight;
  - both harts' I/O at once, streaming, and behind an AMO;
  - errors from four requesters at once;
  - the NPU streaming beside a hart's stores.
- **The banked fabric's waits:**
  - longest wait 25 cycles, in hammer mode; the reference's is 64;
  - at most 8 overtakes in 312 saved runs (the suite's 192, and 120 more
    hammer runs at WAIT 0 and 2 in `build/fabric/hammer-extra`), so its
    limit is 12;
  - a lone hart waits only for its AMO's hold (2 cycles).
- **Planted bugs** (`make fabric-mutants`; the battery adds the hammer and
  twin modes and a WAIT 2 build for reset): 42 of 42 in the banked fabric,
  each caught by a rule's report, and 31 of 31 in the reference. They
  include:
  - the starvation fix removed;
  - a sticky winner;
  - fixed priority on either port or on the I/O bus;
  - the answer-delay stages kept across a reset;
  - six bugs in the second chance: off, its candidate inverted, beside a
    write, for lr, at a wrong address, beside port B's own pick. One mutant is recorded in the script as undetected: port
  B's pointer moving past a pick port A held back. It costs that requester
  its turn but stays within the fairness bound.
- **The data cache with three snoop ports:**
  - its unit test (`make core-aster-l1-unit`): three other masters, each on
    its own port, 200 seeds × 1,000,000 cycles; in every seed, snoops on two
    or more ports in one cycle and a refill snooped on each port;
  - the core campaign (`scripts/mutation_campaign.py`): its anchor updated,
    and three snoop-port bugs added, caught by a new three-port unit stage
    (the rarest at seed 9).
- **The CPU shell's L1 suites** (`make core-aster-l1-tests`) pass with the
  modified cache at its single port.

**Throughput** (the shell's random traffic, WAIT 0, accepted requests):

| Traffic | Banked fabric against its first design | Against the serial reference |
| --- | ---: | ---: |
| mix | −4.0% | +8.6% |
| dense | −9.4% | +5.0% |
| twin (refills of shared lines) | equal; −16.4% without the second chance | equal |
| hammer (one bank) | — | about half: a bank serves two accesses, the reference any number |

**Timing at 10 ns, out of context** (`make timing-fpga-fabric`, Vivado
2025.1; every input and output registered):

| Block | Worst setup slack | LUTs | FFs | Block RAM tiles |
| --- | ---: | ---: | ---: | ---: |
| Fabric, with its banks | +0.384 ns | 3,622 | 1,578 | 32 |
| Core and caches, three snoop ports | +0.456 ns | 5,968 | 2,542 | 34 |
| Core and caches, one snoop port (for comparison) | +0.177 ns | 4,971 | 2,481 | 34 |

The fabric's named paths:

| Path | Slack |
| --- | ---: |
| request to readiness | +1.630 ns |
| request to the banks (worst) | +0.384 ns |
| an AMO through its bank | +0.565 ns |
| request to the reservations | +0.639 ns |
| request to the snoops | +1.623 ns |
| bank to answer | +3.738 ns |

**With the requesters' front ends** (`timing_fabric_fe.sv`: each request
formed by logic shaped like its requester's, and each readiness feeding its
requester's state). The data caches' front end is aster_l1d's valid, address
and op selection, with acceptance advancing the refill count, enabling the
posted-store register and clearing one of 256 valid bits; the NPU's is its
loader-or-writer choice. Worst setup slack **+0.327 ns** (3,856 LUTs):

| Path | Slack |
| --- | ---: |
| data cache to readiness | +1.135 ns |
| data cache to the banks | +0.483 ns |
| NPU to the banks | +0.779 ns |
| data cache to the reservations | +0.570 ns |
| readiness into the data cache's state (11 levels) | +0.674 ns |
| readiness into the NPU's state | +4.876 ns |

The SoC's placement, at about three quarters of the device, will take some of
this margin; 20.2 measures it in context. A fallback for the request-to-bank
path is prepared and recorded, not adopted, with the levers for the others
(soc.md §13, 20.1).

**How the design got here.**
- **The first version** (any two compatible requests, the second chosen
  after the first) passed the shell but missed 10 ns by **7.5 ns**: 18
  logic levels from the arbiters through the banks' multiplexers into the
  snoops and reservations.
- **What closed it:**
  - the per-port groups, with parallel one-LUT arbiters;
  - snoops and reservations from each writer's grant;
  - each bank's AMO arithmetic, from registers and for both halves at once.
- **Found by the shell:** the I/O bus at first ignored an AMO's hold.
- **Found by the review** of this milestone, and fixed:
  - port B's read could starve while two writers kept writing its unit;
  - the core campaign's anchor was stale, breaking `make check`;
  - the groups cost more than the random traffic showed when both data
    caches refill shared lines, which the second chance recovers;
  - the fairness limit was not sensitive enough without hammer mode;
  - mutants missing for the I/O bus, the answer-delay stages and the snoop
    ports.

## Milestones and gates

| Milestone | Scope | Exit |
| --- | --- | --- |
| 20.0 | The fabric shell, the memory checker, the litmus harness; a serial one-bank reference fabric as the shell's first DUT | Every self-test rejected; the reference fabric passes in every mode |
| 20.1 | The banked fabric (soc.md §4); the data cache's snoop ports | Random and edge tests pass in every mode with every bin; planted bugs caught; the L1 tests pass; 10 ns out of context |
| 20.2 | The two-hart SoC: the fabric, both cores, the NPU, the devices, the runtime's dispatch and join | soc.md §10.4's regression; litmus, the memory checker and reset tests on two harts; 10 ns in context |
| 20.3 | The DMA engine and its shell; v1's driver, unchanged | soc.md §10.5; the DMA against CPU copies, at every size and alignment, in the SoC; 10 ns in context |
| 20.4 | The workload matrix: every family ported, AsterBench v12, oracles, the runner and manifest | The correctness, records and overlap gates; the scaling and v1 gates measured |
| 20.5 | Tuning from the captured data (DMA thresholds, cache geometry, partitioning, placement); ECG; the board run | Every soc.md §11 gate; the final image on the PYNQ-Z1 at 100 MHz, on two boots; the Phase 20 report and its closeout audit |

## Checklist

- [x] The owner's decisions (7 October 2026)
- [x] soc.md approved by the owner (7 October 2026)
- [x] 20.0 as in the table above — signed off by the owner (7 October 2026)
- [x] 20.1 as in the table above — exit gate met (7 October 2026), awaiting the owner's sign-off
- [ ] 20.2 as in the table above
- [ ] 20.3 as in the table above
- [ ] 20.4 as in the table above
- [ ] 20.5 as in the table above

## Risks

- **Timing and area.** The image adds a second core, the fabric's
  arbitration, two more snoop ports per data cache, the DMA and about 66
  counters. The estimate is about 74% of the LUTs (soc.md §2): under 80%, but
  with less room for placement than the 19.5 SoC's 40%. The core's margin in
  context is thin (+0.140 ns in 18.7, +0.221 ns in 19.5's SoC). A registered
  stage in the fabric would cost a cycle on every access, and it would move
  the point where an access takes effect away from its acceptance, which
  cpu.md's ordering rests on. It is held in reserve, as an owner decision.
- **The 1.8× gates.** Everything that does not split eats into the margin:
  - the dispatch and join of each of the reduction's four iterations. One
    hart takes about 18,000 cycles an iteration (18.7's scalar reduction:
    73,811 cycles for the four), so each fixed cost of a few hundred cycles
    is a few percent of two workers' half;
  - the GEMM's barrier after the shared packing of B;
  - imbalance between the harts, and bank conflicts.

  Hart 1's start-up is kept out of the window (soc.md §9), and B's packing is
  split between the harts. If one hart packed all of B, two workers would
  reach only about 1.88–1.94×. The estimate is about 1.85–1.95× on both
  workloads, so the margin is small.
- **Memory.** Code, data and two stacks share 96 KiB, and the largest matrix
  points are tight.
- **Verification without a two-hart reference.** Spike cannot follow the
  RTL's interleaving. The cores keep Phase 18's lockstep evidence, and the
  memory checker, the litmus tests and the oracles cover what two harts add.
- **Scope.** The full matrix is hundreds of runs. The manifest and parallel
  simulation keep it tractable.

## Non-goals

More than two harts; an L2 (owner decision); write-back caches or directory
coherence; privilege modes other than machine mode; a CLINT or PLIC; changes
to the NPU, whose options are only matrix axes; floating point. Energy per
workload is not part of Phase 20's exit; the frozen target stands.

