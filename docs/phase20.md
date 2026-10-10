# Phase 20: whole-SoC workload placement and concurrency

Status: **milestone 20.5 (the tuning and the board run) in progress;** its plan, tuning.md, was approved by the
owner on 10 October 2026. **Milestone 20.4 (the workload matrix) was signed off by the owner on 9 October 2026,** with matrix.md
§10.10–14 and its timing on the m4-o5asm-mgi build (+0.333 ns, reproducible). 20.5 began after 03:00 on
10 October 2026, at the owner's word. 20.3 (the DMA) was signed off by the
owner on 8 October 2026, its timing on the g2-o5end build (+0.375 ns, reproducible; one placement: the
median across its RTL's ten strategies is about +0.1 ns). 20.2 (the two-hart SoC) was signed off
by the owner on 8 October 2026. The owner decided 20.2's timing on 7 October 2026, in three rounds (below,
and soc.md §13):
- the second chance is removed and the NPU's request buffer adopted;
- 20.1's sign-off is re-confirmed with its timing correction;
- the NPU's register port is registered;
- the timing is signed off on r19's build (+0.309 ns), with its spread recorded.

20.0 (the fabric
shell) and 20.1 (the banked fabric, with its arbitration change to soc.md
§4.4) were signed off and approved by the owner on 7 October 2026. For 20.2
the owner asked for good timing margin, not a thin pass, without sacrificing
much performance: levers that keep every cycle come first (placement and
floorplanning, implementation strategies, restructuring logic with the same
cycle behaviour); any that costs cycles is measured and put to the owner.
Each lever tried is recorded with its result. The owner's
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

**Timing:** none in 20.0: nothing in it is for the FPGA. (20.1 recorded the
fabric meeting 10 ns; 20.2 found that measurement wrong: Milestone 20.1's
correction below.)

The clarifications of soc.md this milestone found are recorded in soc.md §13
("Clarifications in 20.0").

## Milestone 20.1: the banked fabric (7 October 2026)

**Exit gate met as recorded at sign-off** (soc.md §12: random and edge tests
pass in every mode with every bin; planted bugs caught; the L1 tests pass;
10 ns out of context). **Corrected in 20.2:** the 10 ns part does not hold.
The timing below was measured on a netlist in which Vivado never granted the
round robins' members 2 and 3 (a signed size cast in the arbiter; soc.md §13,
20.2). The corrected fabric misses 10 ns out of context: −1.425 ns alone,
−1.906 ns with its requesters' front ends. The owner signed off 20.1 on the
incorrect figure. 20.2 asks the owner to re-confirm that sign-off, with the
timing now carried by 20.2's levers.
`make fabric-tests fabric-mutants core-aster-l1-unit core-aster-l1-tests
timing-fpga-fabric`. The arbitration below changes soc.md §4.4; the owner
approved it with the milestone (7 October 2026).

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
| Fabric, with its banks | +0.384 ns (invalid: −1.425 ns corrected, 20.2) | 3,622 (4,634 corrected) | 1,578 | 32 |
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
loader-or-writer choice. Worst setup slack **+0.327 ns** (3,856 LUTs; invalid,
as above: −1.906 ns corrected, 20.2):

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

## Milestone 20.2: the two-hart SoC (7 October 2026)

**What is built.**
- **The SoC** (`rtl/soc/aster_soc.sv`): two Aster cores, each with its
  instruction cache and its data cache with three snoop ports; the banked
  fabric; the NPU on port N; the devices on the I/O bus
  (`aster_soc_devices.sv`); and the Phase 19 SoC's AXI4-Lite window. A
  regression build (`SHELL_PAGE = 1`) keeps hart 1 held and the CPU shell's
  register page, as the Phase 19 SoC did.
- **The devices** (soc.md §7, §8, with 20.2's clarifications in soc.md §13):
  the UART into the console; v1's timer, interrupt controller and hart
  control; ABI 4 counters for each hart; the DOT8 counters; 48 fabric
  counters.
- **The runtime's dispatch and join** (`software/runtime/aster_smp.h`): a job
  published in coherent shared memory, hart 1 spinning in its own cache. A
  round trip takes 72 cycles on average.
- **The SoC's testbench** (`verification/aster_soc/`):
  - the memory checker, at soc.md §10.2's perform points;
  - Spike's reservation rule, against every sc;
  - every snoop;
  - every NPU job against its reference;
  - each hart's RVFI trace;
  - hart 1's reset coverage;
  - the cause of every cycle the NPU waits.
- **Builds:** `make soc-sim` builds the simulations, `make soc-tests` runs
  them (`scripts/soc_tests.py`), `make fpga-aster-soc` builds the bitstream.
  The bitstream build now snapshots its sources and checks its synthesized
  netlist.

**Results in simulation** (all passing):
- **soc.md §10.4's regression:** 99 of 99 programs on the regression build,
  each as in the CPU shell, cycle for cycle and RVFI record for record.
- **Litmus:** 26 shapes × 2,000 trials on two harts, nothing forbidden. Run
  without and with three added memory waits:
  - 2.64 and 2.84 million loads checked;
  - 125,902 and 132,176 sc's, of which 29,902 and 36,176 failed;
  - 64,000 AMOs each.
- **Hart 1's reset stress:** four seeds × 1,000 holds, 5,334 resets each.
  Without the added waits they caught answers owed (520), an AMO before its
  write (69), a refill (37) and a reservation (315). With them: 1,041, 55,
  36 and 350.
- **The devices:** exact counts of AMOs, sc's and dot8s; the timer's and the
  software interrupt; word-only and unmapped pages faulting; the NPU's job and
  its interrupt.
- **Each hart's trace** is consistent, as lockstep.py checks one.
- **Phase 19's gate programs** on the regression build (`make soc-gates`,
  in `soc-tests`): the same results and records as in the Phase 19 SoC.
  - **Why cycles differ:** Phase 19's refills had a port of their own and
    its data cache always went ahead of the NPU. In Phase 20's banks the NPU
    waits behind refills in its bank, a hart can wait behind the NPU, and
    the NPU no longer waits behind data accesses in other banks. The
    testbench attributes every NPU wait, and counts the harts' waits behind
    the NPU, from the fabric's own arbitration.
  - **Without the NPU's buffer and register stage:**
    - MNIST and faults: Phase 19's cycles exactly, with no conflict.
    - GEMM gate: +4, with 4 NPU waits behind refills and 4 hart waits behind
      the NPU.
    - Coherence: +206, with 406 NPU waits (403 behind data accesses, 3
      behind refills) and 348 hart waits behind the NPU.
  - **As built,** with the two owner-approved levers: GEMM gate +120
    (no conflict), MNIST +264 (none), coherence +565 (418 NPU waits: 4 behind
    refills, 412 behind data accesses, 2 behind an AMO; 370 hart waits
    behind the NPU), faults 0. Of the levers' share on the final RTL
    (against the build without them), the buffer alone gives the GEMM gate
    +116, MNIST +264 and coherence +320, and the register stage coherence's
    +39; neither changes faults.
  - **Always:** where a program measures the CPU's own code (the GEMM gate,
    MNIST), those cycles are Phase 19's; no NPU wait is unexplained. `scripts/soc_gates.py` checks this and pins each traced
    difference. Phase 19's own gates pass on the Phase 20 SoC.

**A defect in 20.1's fabric, found here** (soc.md §13, 20.2). The round
robins indexed members 2 and 3 with a signed size cast. Vivado follows
IEEE 1800 and never granted them, so it removed the NPU's datapath. Verilator
hid the defect. 20.1's timing was measured on that netlist:

| | As recorded in 20.1 | Corrected |
| --- | ---: | ---: |
| The fabric alone | +0.384 ns | −1.425 ns |
| With its requesters' front ends | +0.327 ns | −1.906 ns |

The fix is an unsigned index. A golden copy of the fabric
(`verification/fabric/aster_fabric_golden.sv`) and a lockstep wrapper
(`equiv_fabric.sv`, in `make fabric-tests`) now require every later
restructuring to keep every cycle.

**Timing: the levers tried** (in context unless marked; worst setup slack at
10 ns after routing and physical optimization):

| Lever | Kind | Result |
| --- | --- | --- |
| Baseline (round-robin defect present) | — | −2.458 ns on a netlist without the NPU |
| Placement directives on it: ExtraTimingOpt, ExtraNetDelay_high, EarlyBlockPlacement | strategy | −2.976, −3.084, −2.399 ns: no help |
| The fabric counters counted from registered copies; the ARM side on R/W; the caches' registered main-memory flag; the NPU's page chosen per cache; the console read from a registered address | keeps every cycle | in r1, with the defect fixed |
| r1: the above, the real netlist (33,925 LUTs, 64%) | | −11.1 ns: the longest-wait maximum chain (27 levels), the NPU's reset fanout, routing |
| r2: longest wait per requester; the NPU's reset registered; an atomic's line invalidated from the head's registers | keeps every cycle | **−2.619 ns** |
| r2 + a floorplan (pblocks: NPU, fabric, each hart) | keeps every cycle | −2.743 ns; but its fabric pblock had 29 block-RAM sites for 32 (the review found it), so not a fair test |
| r4: r2 + the NPU's job copy loaded while idle (START enables only its state) | keeps every cycle | **−2.428 ns**: the best on the approved design |
| r4 + the corrected floorplan (36 sites for the fabric) | keeps every cycle | −3.005 ns: worse |
| r2 + D_ON_B off | costs cycles (below) | −1.076 ns |
| r2 + the NPU's request buffer | costs cycles (below) | −1.072 ns |
| r2 + the read-beside-write bypass | contract change, saves cycles | −0.982 ns |
| r2 + bypass + NPU buffer | | −0.185 ns |
| r2 + bypass + D_ON_B off | | −0.001 ns |
| r2 + NPU buffer + D_ON_B off ("the candidate") | | **+0.002 ns**: met |
| the candidate + bypass | | −0.006 ns: the bypass adds nothing here, so it is not proposed and was removed from the RTL (it was never verified) |
| the candidate, placement directives: ExtraTimingOpt, ExtraNetDelay_high, EarlyBlockPlacement, AltSpreadLogic_high | strategy | −0.009, −0.060, +0.006, +0.018 ns |
| the candidate, synthesis: PerformanceOptimized; register retiming | strategy | +0.007, +0.007 ns |
| the candidate on r4's RTL (cand2) | | −0.070 ns: 9 endpoints within 0.07 ns, in the fabric, the NPU and both cores' own paths |
| cand2 + the corrected floorplan | keeps every cycle | −0.159 ns: worse |
| r5: the owner's decisions as defaults (the second chance removed, the NPU buffer) | | −0.070 ns (the same netlist as cand2); AltSpreadLogic_medium +0.059, AltSpreadLogic_high +0.002, ExtraPostPlacementOpt −0.234, AlternateFlowWithRetiming −0.102 |
| r5 + harts-only floorplan | keeps every cycle | −0.086 ns: no help |
| r6: every requester's main-memory flag (the fabric decodes no address) | keeps every cycle | **+0.006 ns**; AltSpreadLogic_high +0.001, _medium +0.009: met on every strategy. Out of context the fabric with front ends +0.260 ns (from +0.020); request to reservation +1.616 ns (from +0.318) |
| Out of context, the fabric with front ends | | −1.906 ns; D_ON_B off +0.039; NPU from registers −1.155; bypass −0.832; bypass and D_ON_B off +0.302 |

**What the cycle-costing levers cost:**
- **D_ON_B off** (20.1's second chance, approved with its arbitration):
  - on two harts summing the same array from cold caches, 0 cycles;
  - refill-bound, both harts reading one word of every line in step, 5 of
    18,549 (0.03%) (`software/tests/soc_twin.c`);
  - in the fabric shell's twin traffic, up to 16% fewer accepted requests.
- **The NPU's request buffer** (two entries between the NPU and port N,
  readiness from a register; the NPU keeps three accesses in flight): each
  access's answer a cycle later. Per job that is a few cycles on the dense
  GEMMs and up to +100 on a job of many short reads (N = 1, 784×1×25),
  all of it the buffer's. On Phase 19's gate programs, as measured then
  (on 20.2's earlier fabric): +116 cycles on the GEMM gate (+0.003%), +264
  on MNIST (+0.015%), +335 on the coherence program (+0.004%). On the final
  RTL, against the same build without it: the GEMM gate +116, MNIST +264,
  coherence +320.
- **The read-beside-write bypass** (measured, then removed): a port-B read
  beside a port-A write of the same 8-byte unit would take the unit's old
  value from port A's read-first output instead of waiting. It would have
  changed soc.md §4.2's bank rule. With the other two levers it adds no
  timing, so it is not proposed.

**Where 20.2's timing stands.**
- **The approved design:** keeping every cycle, the best is −2.428 ns. Most
  of the gap is 20.1's second chance (about 1.9 ns) and the NPU's paths
  through the fabric's grant (about 1.9 ns).
- **The candidate** (D_ON_B off and the NPU's request buffer): at zero, from
  −0.07 to +0.02 ns with the strategy. The paths left at the limit include
  each core's own (forwarding into Execute, the dot8 sum), which met with
  +0.14 to +0.22 ns in 18.7's and 19.5's lighter designs. At 64% of the
  LUTs, no single block holds the margin any more.

So good margin needs more than these levers.

**Decided by the owner, 7 October 2026:**
- adopt both cycle-costing levers: the second chance is removed from the
  fabric, and the NPU's request buffer is the default;
- keep restructuring, keeping every cycle, for good margin (+0.3 ns or
  better);
- re-confirm 20.1's sign-off with its timing correction, its timing carried
  by 20.2.

These are recorded in soc.md §13.

**After the decisions** (r6: the flags, every strategy met, +0.001 to
+0.009 ns), the design is balanced at zero. About 350 endpoints lie within
+0.3 ns, in eight classes, every one within 0.05 ns of the worst:

| Class | Endpoints | Worst |
| --- | ---: | ---: |
| a data cache's I/O write into the NPU's registers (its CLEAR_TOTALS to 320 counter bits, its descriptor) | 185 | +0.044 ns |
| a data cache's answer into its core's forwarding selects | 52 | +0.024 ns |
| a data cache's request to a bank | 48 | +0.010 ns |
| a data cache's request back into its own state | 27 | +0.035 ns |
| the fabric's answer into a data cache | 11 | +0.100 ns |
| a core's own decode and hazard paths | 9 | +0.006 ns |
| inside the NPU (its tile's MAC count, two multiplies) | 7 | +0.129 ns |
| a data cache's write holding an instruction refill (the bank rule) | 3 | +0.024 ns |

+0.3 ns needs every class to gain that much, the cores' own paths included,
which met with +0.14 to +0.22 ns in lighter designs. The next levers:
- **NPU's registers:** the NPU's register port registered, with the fabric
  sampling the I/O bus's answer a cycle later. Answers keep their cycle, but
  an NPU job starts a cycle later: **+1 cycle per job, which costs cycles**.
  (Measured once built: none on the GEMM gate or MNIST, whose poll loops
  absorb it, and +39 over the coherence program's 162 jobs.)
  (As built, below, the request is registered in front of the port and the
  NPU answers from it in the same cycle, so the fabric's sampling is
  unchanged.)
- **The cores:** restructured hazard and forwarding logic, proven cycle for
  cycle by the core's lockstep suites.
- **The bank rule's coupling:** the read-beside-write bypass (a contract
  change, verified properly this time).
- **Area:** less of it, for routing (the counters are about 3,400 FFs).

**Decided by the owner, 7 October 2026 (second round):** push on in 20.2
for +0.3 ns, the cores' logic included; registering the NPU's register port
is allowed (+1 cycle per NPU job).

**After the second round.** Each step builds on the one before. Results are
the worst setup slack in context, in ns. Every step but r7 keeps every
cycle, proven so:
- **the core and caches:** `make core-shell-equiv`. The CPU shell from
  8809f72 and the current one run 879 cases: 7 modes (plain, latency 1,
  stall seeds with latency 1, with three in flight and with long stalls,
  spliced interrupts) × the kernels and every suite, the interrupt,
  coherence and fetch-fault programs included, plus 4 random modes × 40
  programs. Both shells must pass and exit 0, with the same status line
  (cycles, instructions retired), every RVFI record, and the same console
  and signature.
- **the caches' registered fields:** checked against their definitions
  every cycle by assertions in every simulation, and by the L1 unit tests
  (200 seeds each);
- **the fabric:** `equiv_fabric` against the golden, every output every
  cycle, in every mode; mutants 36/36 and 31/31;
- **the devices:** `make soc-devices-equiv` (in `soc-tests`), against
  8809f72's devices, every output every cycle, 3 million cycles of random
  requests, commands and events. A planted off-by-one is caught.
- **the NPU:** the NPU suites (job and total MAC counts against the
  reference). The divider was also checked on every dividend against 10,360
  divisors.
- **the SoC:** `soc-tests` (the regression cycle for cycle against the CPU
  shell, litmus, resets, the devices).

| Step | Lever | Default | ASM | ASL | over 0.3 | over 0.5 | over 0.5, ASM | others |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| r7 | the NPU's register port registered (`NPU_REG_Q`; the NPU answers from the register, `RSP_COMB`): owner-approved, +1 cycle a job | −0.294 | +0.004 | | | | | |
| r8 | a data cache's target (cacheable, I/O) decided as stage 1 takes the request; Decode's decode registered as the instruction enters | −0.160 | +0.147 | | | | | |
| r9 | dot8's products registered as M1 loads (in the DSPs' multiplier registers) | +0.062 | +0.037 | +0.018 | | | | |
| r10 | Execute's trap, for the slot's fields, without a taken branch's misaligned target (a branch has none of those fields) | +0.005 | +0.004 | | | | | |
| r11 | the operation fields' trap from the causes that need no operand; the NPU's A-extent divider at 13 bits (the remainder never exceeds the 12-bit dividend); a data cache's memory-side request (address, op, byte enables) in registers loaded on its transitions | +0.026 | +0.023 | +0.172 | | | | |
| r12 | the fabric's unit compares on the bank's 12-bit index (both sides are always of one bank); Decode's registers kept off reset pins (`extract_reset`: R's setup is 0.45 ns longer than D's) | +0.092 | +0.121 | +0.032 | +0.201 | +0.277 | | over 0.3 ASM +0.126 |
| r13 | dot8 back to its committed form; a data cache's refill counters off reset pins; the devices' answer selects decoded as the request is registered | +0.058 | +0.103 | | +0.070 | +0.116 | | over 0.7 +0.039 |
| r14 | r13 with dot8's products registered again (r13 was worse on every strategy) | +0.062 | +0.102 | | +0.086 | +0.205 | +0.154 | |
| r15 | the instruction cache's refill request from a register (next cycle's value formed both ways, the fabric's acceptance choosing) | +0.154 | +0.038 | | +0.060 | +0.146 | +0.209 | |
| r16 | the data cache's request the same way; its main-memory flag is stage 2's (a refill's head is cacheable) | +0.056 | | | | +0.254 | +0.168 | |
| r17 | Execute's decoded instruction (`e_dec`) kept off reset pins; the NPU's tile MAC count in two steps (rv × cv at the tile's issue, × K a cycle later: read only three cycles after issue at the earliest) | +0.164 | | | | +0.158 | +0.026 | over 0.4 +0.091; 0.6 +0.268; 0.5 ASL +0.218 |
| r18 | the data cache's `d_req_ready` from a register (next value formed from stage 2's and the state's next values, the core's request and the acceptance last); one-hot cache states (effective in the instruction cache only: Vivado infers no FSM in the data cache, so its attribute was removed) | +0.107 | | | | +0.185 | +0.231 | over 0.4 +0.086; 0.6 +0.065; 0.5 ASL +0.159; 0.5 with route AggressiveExplore +0.185, ExtraTimingOpt +0.190, ExtraPostPlacementOpt +0.123, ExtraNetDelay_high +0.107, phys AggressiveFanoutOpt +0.074; 0.7 ASM +0.063 |
| r19 | a cache's stage-1 freshness terms without this cycle's array write, in both caches (never as the head moves: asserted), so the fabric's acceptance stays out of stage 2's and the state's next values; the watchdog's cosmetic fixes | **+0.309** | | | | +0.103 | +0.053 | over 0.6 +0.094 |

ASM and ASL are the placer's AltSpreadLogic_medium and _high. "over" is
placement over-constrained (`over=`): setup uncertainty is added while
placing and optimizing the placement, then removed before routing; every
result is signed off at the real 0.154 ns.

**What the table shows.** The design went from −2.4 ns (the approved
design) to a worst path of +0.03 to +0.28 ns, depending on the strategy.
From r11 on, each step changes which class is worst more than it moves the
spread: run-to-run placement varies by about ±0.1 ns.
- Over-constraining by 0.5 ns is the best strategy on most versions.
- r19's default build is the first at +0.3 ns: +0.309. Its other
  strategies gave +0.05 to +0.10, so the margin holds for that build, not
  across placements. Before r19 the best was r12's +0.277.
- dot8's registered products: r13 (reverted) was below r12 and r14 on every
  shared strategy, though the gap (about +0.03 to +0.05 ns on average) is
  within the noise.
- r18's register for `d_req_ready`: the stall chain's worst endpoint was
  +0.164 in r17 (over 0.5 with ASM) and +0.299 or better in every r18 build
  analysed, but other classes
  set the worst path, so the spread did not move. It is kept: an assertion
  checks its mirrored next-state logic in every simulation.
- After r18 (the watchdog's review): a data cache's stage-1 freshness terms
  no longer include this cycle's array write, which can never happen as the
  head moves (asserted). That takes the fabric's acceptance out of stage 2's
  and the state's next values. The instruction cache gets the same change.
  These are r19.

Out of context, the core and caches went from +0.267 (r6) to +0.380 (r8)
and +0.472 (r9). In context, r9's products sit in the DSPs' multiplier
registers, so the multiply shares Execute's cycle with its forwarding mux,
30 rows from the core (+0.156 in r12). It still did better overall than
r13's revert.

**A cycle-costing lever, measured for the owner:** the banks' inputs
registered, so that answers come at 3 + WAIT instead of 2 + WAIT (soc.md
§10.6's fallback).
- **Timing:** as a timing-only experiment on r18's RTL (registers in front
  of each bank's RAM, the answers left a cycle late, so not functional):
  +0.226, and **+0.307 / +0.345** with 0.5 ns over-constraint (and with ASM),
  against r18's +0.107, +0.185 and +0.231 on the same strategies, about
  +0.12 ns. A real version would also move each AMO's write a cycle later,
  which changes the contract.
- **Cycles:** measured in the CPU shell, with answers at 3 instead of 2
  (the shell's latency, allowed to 3 in a scratch build): +0.73% on the CPU
  kernels (strided +3.29%, reduction +1.98%, dhrystone +1.25%, coremark
  +0.69%, conv2d +0.12%), and +0.54% on the 99 self-checking programs.

**Decided by the owner, 7 October 2026 (third round):** 20.2's timing is
signed off on r19's default build, +0.309 ns, with the spread recorded. The
cycle-costing fallback is not adopted, and timing is re-checked in 20.3.
Recorded in soc.md §13.

The classes left within +0.3 ns, each the worst in some build:
- the stall chain: a data cache's state, `d_req_ready`, Execute's advance,
  then about 200 Decode and Execute enables (fanouts of 57–164);
- a cache's request to a bank: about 7 ns of routing on 7–10 levels;
- a data cache's request into the NPU buffer's shift enables, through the
  fabric's grant;
- a store's array write on the fabric's acceptance;
- the NPU's tile MAC count (r17 splits it);
- the devices' answer into the fabric.

**20.2's exit, met** (evidence in `docs/results/phase20/soc-20.2/`). The
release-critical simulations (`make soc-tests`, `make core-shell-equiv`) were
captured twice, identically; the other suites ran once.
- **soc.md §10.4's regression:**
  - the 99 programs, cycle for cycle against the CPU shell;
  - the gate programs against the Phase 19 SoC, every difference traced to
    counted bank conflicts or to the owner-approved levers, and pinned;
  - determinism.
- **Two harts:** litmus, the memory checker and the reset stress, on both
  device builds.
- **10 ns in context:** r19, +0.309 ns setup and +0.024 ns hold, no failing
  endpoint. It was built twice from the same sources with identical
  configuration data. The margin holds for that build, not across
  placements (other strategies +0.05 to +0.10 ns), as the owner decided.

**Signed off by the owner, 8 October 2026** (with "begin 20.3").

## Milestone 20.3: the DMA (signed off by the owner, 8 October 2026)

**What is built.**
- **The engine** (`rtl/dma/aster_dma2.sv`, soc.md §6): v1's DMA ABI 1 at
  `0x3000_0000`, unchanged, so v1's driver (`software/drivers/aster_dma.c`,
  untouched) runs as it is. Its counters are v1's counter ABI 5 at `0x100`,
  in v1's order, with their metadata at `0x180`, on the window of the command
  word at `0x2000_3080`, aligned with the fabric counters.
- **The copy:**
  - The source's 8-byte units are read in order on port R, two in flight,
    into a four-unit read buffer.
  - A funnel forms each destination unit from the two source units it
    spans, so every write on port W is one destination unit, with partial
    byte enables only at the ends.
  - Writes go out through a two-entry buffer whose head is the port.
  - At most 8 bytes move a cycle.
- **The ports' requests leave from registers.** The read's valid is formed
  both ways, the fabric's acceptance choosing. The engine relies on the
  fabric answering a read exactly 2 + WAIT cycles after its acceptance
  (asserted), so a read can go out in the cycle an answer comes back.
- **The ports are shared with the ARM side.** While the harts are held, they
  carry the ARM side's main-memory accesses (`arm_go`), so nothing is
  multiplexed after the ports' registers. A start or a stop clears them with
  the fabric.
- **ABORT is cooperative, as in v1.** BYTES_DONE is the prefix whose writes
  were answered.
- **In the SoC:**
  - the engine's reset is the run's, a cycle after hart 0's, as the NPU's;
  - its completion is the interrupt controller's source 1;
  - its invalidated lines are the data caches' snoop hits on W's port;
  - the devices no longer answer its page.

  The decisions this took are in soc.md §13, "Clarifications in 20.3".

**The DMA's shell** (`verification/dma/`, `make dma-tests`, in `make
check`): the engine alone, its ports answered as the fabric answers them, at
WAIT 0, 1 and 3, four seeds of eight modes (96 runs):
- every length 0–80 at every alignment pair, on time and under back-pressure;
- random jobs on time, each request's acceptance cycle and JOB_CYCLES
  against a cycle model (`dma_model.h`). They are up to 4 KiB, but every
  16th is 8 KiB to about 47 KiB, near a legal copy's widest (the longest run
  is 48,119 bytes);
- back-pressure, with the same large jobs;
- range, wrap and overlap errors at their boundaries, LENGTH 0 with a bad
  descriptor, each rejection alone, and hart 1's writes;
- ABORT, the engine's reset, and the SoC's STOP at random points, with the
  ARM side's accesses on the ports while held;
- a mixed mode, which also freezes, resumes and restarts the counters'
  window.

In every run:
- **the oracle:** every job is checked against an oracle of the whole
  memory;
- **offered requests:** none is withdrawn or changed before it is taken, and
  none is newly offered once an ABORT takes effect;
- **JOB_CYCLES** is the job's length as the shell sees it;
- **BYTES_DONE,** read during jobs, is the answered prefix;
- **the counters** equal the shell's own count of each event, with per-job
  sums on some jobs.

`make dma-mutants`: 39 planted bugs, each caught:
- the 14 added after the first review are ones the shell missed then;
- the 39th cuts the engine's unit counts to 12 bits, which only a job over
  32 KiB breaks.

**In the SoC** (`make soc-tests`):
- **The DMA program** (`software/tests/soc_dma.c`, through v1's driver), on
  both device builds:
  - 3,392 copies, each against a CPU copy of the same source: every length
    0–40 and twelve larger sizes up to 4 KiB, all at every alignment pair;
  - copies at the limits, which pass: from LIMIT_LO, and a source and a
    destination each ending exactly at LIMIT_HI;
  - a 16,000-byte copy, misaligned, with its guards;
  - the errors through the driver, ABORT's prefix, and the completion
    interrupt;
  - the counters, exactly: each job's tallies, and their accepted reads and
    writes, stalls and invalidated lines against the fabric counters'
    independent counts;
  - hart 1, refused by the driver and its writes ignored;
  - hart 1's cache seeing the DMA's writes: all 64 lines it held were
    invalidated;
  - **contention:**
    - Copies run while hart 1 streams through 8 KiB of its own memory,
      checking every load.
    - A 4 KiB job then runs while the NPU also runs a 32 × 32 × 64 GEMM. The
      NPU is still busy when the job ends, and its result is checked whole.
    - The DMA waited for the others: 140 stalls at WAIT 0 and 47 at WAIT 3,
      against about 1,500 NPU and 43,000–44,000 hart-1 accesses.
    - Twelve RESUMEs during that job each dropped their cycle's events from
      every counter. 12 reads were dropped at WAIT 0 and 6 at WAIT 3, and the
      cross-checks stayed exact.
  - no exception taken.
- **The testbench** checks every port R answer against memory at its
  acceptance (about 97,000 a build), with the bank rule. Every port W write
  goes through the memory checker and the snoop checks (about 94,000). The
  DMA's accesses must stay inside its job's ranges, and while the harts are
  held the ports may carry only the ARM side's.
- **The rest of `soc-tests` is unchanged:** the 99 regression programs,
  cycle for cycle (each loaded through the DMA's ports), the gate programs
  with their traced cycles, litmus, the reset stress, the devices and their
  lockstep.

**The DMA against the CPU copy, in cycles.** Each entry is DMA end to end
through the driver (the job alone) / the CPU copy.
- The CPU's copy is a word loop with a byte prefix and tail, so a byte loop
  when the alignments differ.
- Misaligned means the source at offset 3 and the destination at offset 6.
- The table's second pass is the one recorded, with warm instruction caches
  and an untimed 8-byte copy before each timed one (the driver's stack back
  in the data cache). The CPU copy's source may also be in its data cache
  from the first pass.

| Bytes | WAIT 0, aligned | WAIT 0, misaligned | WAIT 3, aligned | WAIT 3, misaligned |
| ---: | ---: | ---: | ---: | ---: |
| 16 | 160 (8) / 79 | 153 (9) / 192 | 193 (14) / 91 | 180 (18) / 192 |
| 64 | 153 (14) / 151 | 153 (15) / 720 | 180 (29) / 151 | 180 (33) / 720 |
| 256 | 153 (38) / 494 | 160 (39) / 2,839 | 260 (89) / 501 | 273 (93) / 2,845 |
| 1,024 | 271 (134) / 1,838 | 271 (135) / 11,287 | 513 (329) / 1,845 | 513 (333) / 11,293 |
| 4,096 | 641 (518) / 7,242 | 641 (519) / 45,105 | 1,473 (1,289) / 7,277 | 1,473 (1,293) / 45,135 |

- **Throughput:** at WAIT 0 the job moves 8 bytes a cycle. At WAIT 3 it is
  limited by two reads in flight against a five-cycle answer, so a unit
  takes 2.5 cycles.
- **Overhead:** the driver's register accesses and polls add 115–152
  cycles (mean 133) at WAIT 0, and 147–184 (mean 172) at WAIT 3. The polls
  come every few tens of cycles, so the end-to-end figure moves in steps.
- **Where the DMA wins:**
  - aligned: from 256 bytes, at both waits. At 64 bytes the two are even at
    WAIT 0 (153 against 151), and the CPU wins at WAIT 3 (180 against 151);
  - misaligned: from 16 bytes.

  This is a starting point for 20.5's DMA thresholds.

**Timing in context** (the board design, 10 ns). Each RTL revision is
built with three placement strategies, as in 20.2:
- default;
- o5: placement over-constrained by 0.5 ns;
- o5 ASM: the same with the AltSpreadLogic_medium placer.

| Revision | Default | o5 | o5 ASM | What changed |
| --- | ---: | ---: | ---: | --- |
| d1 | +0.109 | +0.059 | +0.047 | the DMA integrated |
| d2 | +0.028 | +0.190 | +0.138 | the NPU buffer's request from a register; the engine's unit counts narrowed to 15 bits |
| d3 | +0.191 | +0.142 | +0.171 | the data caches' answer kept in a register |
| f1 | +0.010 | +0.162 | +0.270 | the counters' window as one signal from the devices (99ef503) |
| g1 | +0.098 | +0.027 | +0.121 | the fabric's reservation compared as its offset in main memory (15 bits, not 30) |
| g2 (final) | +0.031 | +0.118 | +0.168 | both caches' tags narrowed to main memory's span (5 bits, not 20; TAG_SPAN) |

- **No cycle changed.** Each change keeps every cycle the same:
  - The NPU buffer is emptied at a start or a stop. While the board's reset
    is high, the fabric sees the same requests of N in every cycle.
  - The data cache's registered answer is asserted equal to its definition
    in every cycle. `make core-shell-equiv` runs 879 programs the same,
    cycle for cycle, as 20.2's golden.
  - The window signal changes only the DMA's counters.
  - The reservation is checked by `equiv_fabric` against 20.2's golden
    fabric every cycle, in every mode.
  - The narrow tags are asserted equal to full ones every cycle (soc.md
    §13).

  The SoC's hart-side figures are 20.2's exactly: litmus, the reset stress,
  dispatch, the gate programs' traced cycles, and the 99 regression
  programs, cycle for cycle.
- **The limits are placement, not logic.** Every limiting path is a
  requester's registered address, through the fabric's arbitration, into a
  requester's next state, and about 7.3 ns of its ~9.3 ns is route:
  - hart 1's data cache into a bank's write enable;
  - a data cache's tag lookup into its request registers;
  - the data caches into the NPU buffer's enables;
  - R's grant into the DMA.

  The narrowing removed 865 LUTs (g2: 33,866) and shortened those paths'
  logic, but across these revisions the default build swung from +0.010 to
  +0.191 ns and the others moved as much. Taking the fabric's arbitration
  off these paths would mean registering its bank inputs, which costs
  cycles. The owner declined that in 20.2.

**Strategies on g2's RTL** (the cycle-preserving lever left: placement;
eleven builds):

| Strategy | Setup | Strategy | Setup |
| --- | ---: | --- | ---: |
| default | +0.031 | o5, ExtraTimingOpt placer | +0.043 |
| o5 | +0.118 | o5, AltSpreadLogic_high placer | +0.188 |
| o5 ASM | +0.168 | o5, AggressiveExplore router | +0.118 |
| o6 | +0.092 | **o5, ExtraNetDelay_high placer (o5 END)** | **+0.375** |
| ExtraNetDelay_high placer alone | +0.072 | o3, ExtraNetDelay_high placer | +0.035 |
| o5 END, rebuilt | +0.375 | | |

- **g2-o5end is the one build of 20.3 with good margin:**
  - setup +0.375 ns, hold +0.022 ns;
  - only 8 endpoints within +0.45 ns;
  - every net routed, no DRC error, and the netlist guard met;
  - its worst path is hart 0's own result into its DOT8 multiplier;
  - 33,975 LUTs (63.9%), 23,677 flip-flops, 77 block RAM tiles, 27 DSPs.
- **It is reproducible.** A rebuild from the same sources gives the same
  configuration data and the same slack.
- **It is one placement, not the strategy's property.** The same placer
  gives +0.072 without the over-constraint and +0.035 at 0.3 ns, and on
  f1's RTL o5 END gives +0.109.
  - Across g2's ten strategies, the median is about +0.1 ns (+0.105).
  - The default directives give +0.031, a thin pass.
  - So, like r19 in 20.2, the margin holds for this build and not across
    placements. Any later change to the RTL re-rolls the placement.
  - It is built with
    `SOC_DIRECTIVES="over=0.5;place=ExtraNetDelay_high"`. The owner signed
    off on it, and later milestones re-check timing with several strategies
    (the decisions below).
- **The netlist guard** used to count the ARM side's 32 write-data
  registers. Those are gone, since the ports' registers are now the DMA's,
  so it counts port W's 64 data registers in their place
  (`ASTER_NETLIST npu_rams=40 npu_wdata_regs=32 w_wdata_regs=64`).
- **Most of the DMA's flip-flops are its counters:** 12 of its 14 count 64
  bits (768). The other two, forwards and write-backs, are constant 0 and
  removed.

**20.3's exit, met** (evidence: `docs/results/phase20/dma-20.3/`):
- **soc.md §10.5:**
  - the DMA in its shell (96 runs, 39 of 39 mutants caught);
  - in the SoC, through v1's driver unchanged, on both device builds;
  - the rest of `soc-tests` unchanged and cycle-exact, run twice with
    identical output.
- **Against CPU copies at every size and alignment in the SoC:** 3,392
  copies against CPU copies (every length 0–40 at all 64 alignment pairs,
  and 12 larger sizes up to 4 KiB at all 64), a 16,000-byte copy, and the
  timing table above. In the shell, jobs reach about 47 KiB.
- **10 ns in context:**
  - every build of the final RTL closes;
  - g2-o5end, reproducible, has +0.375 ns, the good margin the owner set in
    20.2;
  - the spread is recorded above.

**Decided by the owner, 8 October 2026** (soc.md §13):
- **20.3's timing is signed off on g2-o5end** (+0.375 ns, reproducible),
  recorded as one placement.
- **Later milestones re-check timing:** each builds its final RTL with
  several strategies and signs off on its best reproducible build, with the
  spread recorded. The default directives stay as they are.

**Found and fixed on the way** (beyond the reviews' findings, all fixed):
- **`make check` failed from 20.2 on.** Its host test of the core's
  planted-bug campaign (`scripts/mutation_campaign.py`) found eight anchors
  that 20.2's restructuring had made stale; 20.3's tag change made two more.
  - All ten are re-anchored to their counterparts in the current RTL.
  - `make host-tests` passes again (355 tests).
- **The campaign itself (`make core-aster-mutants`)** had not run since 20.2
  restructured the core. On the current RTL it catches 109 of its 114:
  - the ten re-anchored ones, nine caught;
  - the rest as before.

  The five not caught are exactly Phase 18's accepted ones, unchanged:
  - 18.3's four, equivalent or unobservable (phase18.md):
    - Execute advancing in an interrupt's cycle;
    - a CSR write gated by its trap;
    - the CSR write repeated in M1;
    - a trap record's RVFI mstatus, which Spike's log says nothing of. The
      mstatus a trap leaves is checked through the handlers' reads.
  - 18.6's `sys-retire-at-commit`, equivalent under its serialization.

  The arguments still hold on the current core (a fetch fault decodes as
  nothing, `aster_core.sv:302`; a serializing instruction enters M1 only
  with M2 empty, asserted), so none of the five tells anything new.
- **The fabric shell's store-conditionals** never landed 64 KiB from their
  reservation. They do now (a required bin), and a mutant that ignores the
  offset's top bit is caught (37 of 37).
- **The L1 unit tests' span builds** didn't vary every kept tag bit.
  - Their pages now set each one.
  - The data cache's remote pages are 3, 7, 11 and 19, so a snoop can
    differ from a held line in any one bit.
  - A new campaign, `make l1-span-mutants` (`scripts/l1_span_mutants.py`),
    drops each kept bit from each narrow compare. All 15 are caught on every
    seed. Its first run found the snoop compare's bits 14 and 15 untested,
    which led to the remote pages.

**Signed off by the owner, 8 October 2026** (with "proceed with 20.4").

## Milestone 20.4: the workload matrix (signed off by the owner, 9 October 2026)

**The plan:** [`matrix.md`](matrix.md), the matrix soc.md §9 asks for, made
concrete, and [`asterbench-v12.md`](asterbench-v12.md), its records.

**Approved by the owner, 8 October 2026,** with four decisions (matrix.md
§9):
- **The axes cross** as R plus each axis alone, plus named crosses where a
  family's question is about an interaction: about 8,000 records.
- **The records' configuration** comes from the hardware where a hart can
  read it, and from the build, checked by the runner, where it cannot.
- **The data cache off** is built, as a `DCACHE` parameter.
- **The NPU's 32-bit port** is built, as a lane adapter on port N.

AsterBench v12 is approved as drafted. CoreMark's official ten-second score
is 20.5's, on the board.

**The steps** (matrix.md §8):
1. the infrastructure, proven on the CPU baseline;
2. the v1-retained and gate workloads, with the scaling and v1 gates
   measured;
3. the other families;
4. the full matrix, its manifest complete, the overlap and totals
   reconciled, and the evidence.

### Step 1: the infrastructure

**AsterBench v12:**
- **The record:** one console line of 133 fields
  (`software/runtime/asterbench_v12.{h,c}`, with every counter page in
  `aster_counters.h`). A window opens with START, after the NPU's totals are
  cleared, and is read after FREEZE.
- **The validators:** `scripts/asterbench_v12.py` and
  `verification/common/asterbench_v12_record.h`. Their shared corpus
  (`verification/host/test_asterbench_v12.py`, in `make host-tests`) is 10
  valid records, 66 broken invariants and more than 1,000 mutations of
  fields and framing. The two validators agree on every one.
- **The self-test:** `software/tests/soc_v12.c`, in `soc-tests`, has three
  windows: cold; warm with both harts, the DMA and the NPU; and a kernel
  window. Each record passes both validators on the real counters,
  including the DMA's exact reconciliation with the fabric. Its
  configuration matches the testbench's readback (`check_config`).

**The build variants** (`make matrix-sims`, `scripts/soc_variants.py`):
- one hart;
- +1, +2 and +4 waits;
- the NPU's five other geometries;
- the data caches off, alone and with each wait.

New RTL, both simulation-only by default:
- **`DCACHE` in the data cache** (the owner's decision). Only a cached load
  looks up and refills; with the cache off, a main-memory load goes by the
  access path with its main-memory flag. Its unit test
  (`l1d_unit_uncached`) passes 200 seeds, and the default build is the same
  logic.
- **The NPU's 32-bit port adapter on port N.** Writes are replicated to both
  halves with placed byte enables; each answer's half comes from an in-order
  queue, with assertions against overflow and an unowed answer.

Every two-hart variant passes `soc-tests`' two-hart programs: litmus, the
reset stress, dispatch, the devices, the DMA and the v12 self-test.
- **The devices and DMA programs** now take the data caches' mode as a
  build define, since with the cache off no line is held or invalidated.
- **On a cache-off build,** the reset stress's "a refill" coverage counts
  uncached loads owed, since there are no refills.
- **The one-hart build** cannot run the two-hart programs, which wait for
  hart 1. It runs its own, `software/tests/soc_h1.c`, which checks:
  - the hart count reads 1;
  - SECONDARY_RUN is ignored;
  - hart 1 retires nothing, and its ports accept nothing;
  - two v12 records, valid in both validators.

**The matrix runner** (`scripts/matrix.py`) works in five stages:
1. it builds each entry's firmware, cached;
2. it runs the simulations in parallel;
3. it validates each record, in both validators, and its configuration;
4. it applies the family's oracle;
5. it writes the manifest.

**v1's CPU kernels** run unchanged through `software/matrix`'s compatibility
layer.
- **It shadows `workload.h`.** v1's `aster_perf_clear` and
  `aster_perf_snapshot` are, as in v1, the control word's store inline, now
  to ABI 4's, so the window is exactly v1's. v12's set-up and reading stay
  outside it.
- **A warm run restores `.data` and `.bss`** from the copy taken at entry
  before its timed pass.
- **The layout:** all matrix firmware links with
  `verification/core/firmware/link_matrix.ld`, 96 KiB as one region with
  both stacks at the top.

**The runner, hardened after review:**
- every exception is recorded as a failed entry;
- the firmware's cache key covers every header and the compiler;
- the manifest records the toolchain, the tree, untracked files included,
  and each firmware's footprint;
- `--repeat-every N` runs a sample again and requires byte-identical
  records.

**The CPU family**, as re-captured in step 2 from b8c9f62 (`build/matrix/h-cpu`; the first capture,
`cpu-2`, predates the cold word): 108 entries, 108 captured, in 28 s. That is memory × data cache × cache
state for each of the six kernels, plus one hart.
- **The oracle:** each record matches v1's baseline record's identity and the independent checksum model.
- **Determinism:** 18 entries run again give byte-identical records.
- **Cold and warm:** each of the 54 pairs runs binaries that differ only in the cold word (step 2).

| Kernel | Warm, R | Cold, R | Cache off, warm | Cache off, +4 waits, cold | v1's aster_minimal | v1 / v2 (cold) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CoreMark (CRC run) | 466,330 | 466,975 | 610,939 | 837,270 | 1,922,272 | 4.12× |
| Dhrystone | 858,191 | 859,399 | 1,113,474 | 1,608,313 | 3,128,553 | 3.64× |
| sort/search | 696,154 | 696,891 | 894,757 | 1,160,013 | 2,183,301 | 3.13× |
| FFT | 314,745 | 316,491 | 407,694 | 542,237 | 3,057,473 | 9.66× |
| strided | 2,377 | 2,732 | 2,626 | 3,746 | 9,087 | 3.33× |
| Conv2D | 1,109,074 | 1,111,066 | 1,588,381 | 2,228,542 | 5,820,652 | 5.24× |

- **v1 / v2 divides by the cold figure:** v1's programs START once after reset, so its figures are first
  passes. This table's first version divided by the warm figure (strided read 3.82× there).
- **One hart** gives the same cycles as two, warm and cold, since hart 1 is idle.
- **Against 18.7's figures** (measured with the CPU shell's testbench window and layout), cold:
  - within a few cycles: sort/search +14, strided +10, Conv2D −55;
  - further off: CoreMark +369, Dhrystone +1,978, FFT −1,071.
  - The cold word gave the cold builds the warm builds' layout, which moved CoreMark's cold figure by 368
    cycles (it was +1 against 18.7 in `cpu-2`). These kernels move by up to about 0.2% with the layout of
    their code and data (the data cache is direct-mapped); the cause of each delta is not investigated
    further.
- **The review's measurement:** the first run's window held the harness's own calls on the kernels that use
  aster_perf_clear (about 80 cycles of strided's 2,498), until the shadow's START and FREEZE became v1's inline
  stores.

**Two fixes to 20.3's DMA program,** whose checks rested on coincidences
that a slower build broke:
- **The RESUMEs** are now a burst at the job's start, 0 to 3 `nop`s apart
  in turn, so their phase sweeps every offset against the reads. They drop
  7 to 22 reads on every build.
- **The NPU's overlap** is now both engines' busy flags sampled at one instant: right after the DMA's submit,
  and while it runs (check 71). It is no longer required to outlast the job. Under contention the RESUMEs'
  I/O stores take long enough that the job can end before polling begins, so a sample taken only while
  polling was itself a coincidence.
- **The poll loop is bounded,** so a hung job fails check 65 rather than timing out.


### Step 2: the v1-retained and gate workloads (done, pushed as d097b2b)

**Captured from b8c9f62,** a clean tree: `build/matrix/h-coh`, `h-dsp`, `h-ml` and `h-ecg`, with the CPU
family's `h-cpu`.
- **The runs:** 1,053 entries captured and 41 unsupported, 1,889 records.
- **Every record** passes both validators and its family's independent oracle.
- **Determinism:** 86 entries run again give byte-identical records and firmware.
- **Cold and warm:** each of the 163 pairs differs only in the cold word.
- **One cold run is faster than its warm twin:** MNIST on two workers, by 340 cycles (0.007%). It is not a
  cache effect: the cold run has more misses. Hart 0 retires 98 fewer instructions spinning, so it is the
  timing of the two harts' handshakes.

**Two windows.** Every warm run outside the CPU family records two windows (matrix.md §4):
- its end-to-end window: the case's own, which is v1's for a v1-retained case;
- its kernel window: the computation alone, its data in place.

A cold run records the end-to-end window. matrix.md §10 lists nine readings of the plan found here, for the
owner's review; the owner accepted them on 8 October 2026, when step 2 was pushed ("its ok!").

**How the matrix measures.** Four reviews and checks of my own changed the method; every figure below is from
the method as it now stands.
- **Cold and warm are one binary:** cold or warm is one data word. A compile-time switch had moved code and
  data, and MNIST's cold windows came out up to 0.24% faster than its warm ones.
- **Each warm window has its own warm-up pass** of the same code. With one warm-up, a kernel window ran its
  code for the first time: a GEMM's kernel window took 66 to 76 instruction misses on hart 0, and now takes
  none.
- **Outputs are poisoned between passes, each by the hart that writes it.**
  - `make matrix-poison-check` plants a timed pass that skips its engine in Conv2D's e2e and kernel windows,
    and both fail. With the poisoning compiled out, the e2e plant passes unnoticed.
  - Poisoning from hart 0 alone had invalidated hart 1's warm lines: the gate's reduction took 38,245 cycles
    against 37,623 (coh-4 and coh-5, before 325d18b).
- **The computation's functions are compiled out of line,** in every program. Inlined, a small change
  elsewhere changed GCC's code for Conv2D's hot loop: 12 more instructions an output row and 7.3% more cycles
  on two workers.
- **The work intervals** follow v12's definition (matrix.md §10.7):
  - Hart 1 stamps its first start and its last end.
  - Hart 0's interval starts at 0. It ends at the window's end where hart 0 works after its last wait, else
    at its last share's end, stamped. In an NPU kernel window it is 0 and 0.
  - A stamp is a call and a counter read: 17 cycles beyond a store at R, 21 at +4 waits (the `stamp_cost`
    case). The stamps inside every two-worker window count against v2.
- **An NPU kernel interval** runs from the job's START to its end. The job's accounting comes after.
- **The oracles** check both windows' identity and checksum, and the method's evidence:
  - the exact DOT8 count of every method;
  - the NPU's jobs and MACs;
  - the DMA's jobs and bytes;
  - hart 1 idle with one worker and busy with two.

**The coherence family** (`h-coh`): 94 planned. 93 are captured; dispatch and join on the one-hart build is
unsupported. Determinism 12/12; 16/16 pairs.
- **The cases:** the two reductions and the three GEMMs, on one worker and on two.
- **§3's harts and workers × memory cross:** one hart, two harts with one worker, and two with two, at each
  of the four memory waits. The one-hart build has a variant at each wait, each passing its test program.
- **One at a time:** cold, and the cache off.
- **Dispatch and join,** and the stamp's cost.

| Workload (warm, R) | e2e, 1 worker | e2e, 2 workers | Speedup (gate: 1.8×) | Kernel, 1 | Kernel, 2 | Speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| reduction, the gate's (fill and sum in parallel) | 73,995 | 37,715 | **1.962×** | 36,998 | 19,284 | 1.919× |
| reduction, v1's (fill on hart 0) | 73,999 | 58,615 | 1.262× | 36,962 | 21,600 | 1.711× |
| GEMM 64×64×64, DOT8 | 215,179 | 109,041 | **1.973×** | 201,216 | 101,854 | 1.976× |
| GEMM 96×96×96, DOT8 | 703,738 | 354,565 | **1.985×** | 672,610 | 338,935 | 1.984× |
| GEMM 128×64×128 (M×N×K), DOT8 | 843,637 | 432,920 | **1.949×** | 805,386 | 404,287 | 1.992× |

- **The scaling gate, measured:** across every wait, cold and the cache off, the four gate cases scale end to
  end from 1.945× (the gate's reduction, +4 waits) to 1.999× (GEMM 128×64×128, the cache off). It is
  required in 20.5.
- **The one-hart build** gives exactly the one-worker cycles at every wait.
- **v1's reduction** scales 1.18× to 1.34× across the axes, as v1's own 1.29×: hart 0's fill inside the
  window is serial.
- **Against v1** (v1's inputs and window, and like v1's figures the first pass after reset): v1's best
  reduction is two workers, 233,112 cycles. v2's best in that window is v1's version on two workers, cold:
  58,948 cycles, **3.95×**. (The gate's version, 38,690 cold, has a different window.)

**Dispatch and join** (`smp_overhead.c`), warm. The round trip is 64 empty jobs. The handoff is one job with
its four moments stamped, and each difference holds about one stamp.

| Memory | Round trip, a job | Dispatch | Job | Join |
| --- | ---: | ---: | ---: | ---: |
| R | 88.13 | 71 | 33 | 42 |
| +1 wait | 95.19 | 73 | 36 | 44 |
| +2 waits | 102.19 | 78 | 39 | 47 |
| +4 waits | 116.25 | 89 | 45 | 52 |
| the cache off | 78.23 | 65 | 32 | 34 |

- **The cache off is faster:** each flag is then read from memory directly, rather than invalidated and
  refilled.
- **Cold,** the round trip is 89.06 cycles a job.

**Conv2D 32×32, K = 5** (`h-dsp`): 82 planned. 80 are captured: the eight methods across the axes, and
three seeds at R. Two workers on the one-hart build are unsupported. Determinism 10/10; 8/8 pairs.

| Method | e2e, warm | e2e, cold | Kernel, warm | v1's best (NPU, 4,837,408) ÷ cold |
| --- | ---: | ---: | ---: | ---: |
| im2col, scalar | 1,508,534 | 1,508,767 | 951,664 | 3.21× |
| im2col, two workers | 781,547 | 782,071 | 473,785 | 6.19× |
| im2col, DOT8 | 1,511,654 | 1,512,185 | 954,724 | 3.20× |
| im2col, NPU | 575,274 | 575,662 | 17,936 | 8.40× |
| direct, scalar | 1,136,794 | 1,137,943 | 1,084,376 | 4.25× |
| direct, two workers | 596,193 | 597,280 | 542,898 | 8.10× |
| direct, DOT8 | 673,494 | 674,702 | 621,060 | 7.17× |
| direct, NPU | **84,307** | **84,660** | 27,824 | **57.1×** |

- **Against v1:** direct NPU, cold in v1's window, is 57.1× v1's best.
- **The direct NPU's window** is mostly not the NPU. Its kernel window, the job from START to its end, is
  27,824 of the e2e window's 84,307 cycles. Building the inputs and the checksum, both inside v1's window,
  are the rest.
- **im2col's NPU kernel** is 17,936 cycles, against direct's 27,824. Its e2e window is 6.8× direct's,
  because the im2col copy is inside v1's window.
- **The NPU's geometry:** the direct method takes 86,291 to 105,395 cycles across the five other geometries
  (the 4×4, 32-bit, one-strip NPU slowest).
- **The seeds:** the kernel windows agree within 32 cycles across the three. The other two seeds' e2e windows
  take 4,192 to 4,210 cycles more: one more instruction for each generated input, to form their constant.

**The MNIST MLP** (`h-ml`): 8 methods, all captured but two workers on the one-hart build.
- **The oracle:** every logit and class equals the independent model's (checksum 0x601191ed). The model
  classifies 30 of the 32 images correctly.

| Method | e2e, warm | A image | Cold, a image | v1's best (NPU, 433,903 an image) ÷ cold | Kernel, warm | NPU busy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| scalar | 9,704,295 | 303,259 | 303,284 | 1.4× | 9,639,968 | — |
| two workers | 4,880,762 | 152,524 | 152,513 | 2.8× | 4,813,381 | — |
| DOT8 | 6,576,957 | 205,530 | 205,562 | 2.1× | 6,512,514 | — |
| NPU, an image at a time (v1's: N = 1, K split) | **192,199** | **6,006** | **6,019** | **72.1×** | 115,968 | 59.2% |
| NPU, batched by 1 (tile mapping) | 285,456 | 8,920 | 8,934 | (48.6×) | 212,736 | 73.8% |
| NPU, batches of 4 | 114,828 | 3,588 | 3,601 | (120×) | 55,808 | 48.2% |
| NPU, batches of 8 | 86,775 | 2,712 | 2,723 | (159×) | 29,824 | 34.1% |
| NPU, batches of 32 | 71,734 | 2,242 | 2,254 | (193×) | 17,208 | 23.9% |

- **Against v1:** the NPU an image at a time, in v1's per-image window and cold, is 72.1× v1's best.
  Batched figures are throughput, not v1's per-image latency, and are shown in parentheses.
- **The batch crossover:**
  - For one image, v1's K-split mapping (6,006 cycles) beats the tile mapping (8,920), which uses one of the
    array's eight rows.
  - Batching overtakes from batches of 4, trading latency for throughput. An image's result waits for its
    batch: 14,354 cycles in 4, 21,694 in 8, 71,734 in 32. Throughput falls to 2,242 cycles an image.
- **The NPU's busy time** falls as batches grow, because the requantization, ReLU and argmax on the CPU
  stay per image.
- **The array's rows** set the tile mapping's active cycles: 102,400 at batch 1, 25,600 at 4, and 12,800 at
  8 and 32. A strip is eight rows, and a batch below 8 fills only some of them. On a 4×4 NPU, a batch of 4
  fills a strip.
- **Two workers are sensitive to placement.** Across this step's captures the method took 4.88 M to 5.19 M
  cycles; this one, with the computation out of line, 4.88 M.
  - Hart 0's data-cache misses ranged from 37,784 to 84,943 (38,043 here), and its instruction misses from
    9 to 1,543.
  - Hart 1's data misses ranged only from 35,138 to 37,719.
  - The harness changed between captures, not the kernels: hart 0 retired 2.914 M to 2.927 M instructions.
  - Both caches are direct-mapped, and where the code ends sets where the data lies, so hart 0's input
    vector conflicts with its weight stream by address in some builds. Choosing placements is tuning,
    20.5's.

**The CIFAR-10 CNN** (`h-ml`): 5 methods; all captured but two workers on the one-hart build.
- **The oracle:** every logit and class equals `cifar_reference`'s independent model (checksum 0xfeb1b300).
  The model classifies 14 of the 20 images correctly.

| Method | e2e (v1's window, 20 images), warm | Cold | v1's best (NPU, 65,568,218) ÷ cold | Kernel, warm | NPU busy |
| --- | ---: | ---: | ---: | ---: | ---: |
| scalar | 60,685,903 | 60,686,892 | 1.08× | 54,427,361 | — |
| two workers | 33,175,379 | 33,176,719 | 1.98× | 28,064,851 | — |
| DOT8 | 48,572,112 | 48,573,379 | 1.35× | 42,313,091 | — |
| NPU, im2col (v1's) | 6,430,573 | 6,431,629 | 10.2× | 155,400 | 2.4% |
| NPU, direct (channels last) | **4,084,765** | **4,085,650** | **16.0×** | 158,560 | 3.8% |

- **Against v1:** the NPU direct, cold in v1's window, is 16.0× v1's best.
- **The CPU's stages dominate on the NPU:** the three layers' jobs take 155,400 to 158,560 cycles of
  4–6 M. The staging, requantization, ReLU and pooling are the rest. Direct's window is 2.35 M cycles
  shorter than im2col's, with nearly equal kernels: it builds no im2col matrices.
- **v1's checksum and class check** are folded inside the window as each image ends, as v1 did.
- **The NPU's geometry:** the direct method takes 4,112,285 to 4,315,005 cycles across the five other
  geometries.

**Streaming ECG** (`h-ecg`): 684 planned. 648 are captured; the two pipelines on the one-hart build are
unsupported. Determinism 33/33; 72/72 pairs.
- **The cases:** every chunk and FIR case (chunks of 16 to 128 samples, FIRs of 8 to 32 taps, where the chunk
  is longer) with both models, each across the axes (§3).
- **The oracle:** an independent model of the pipeline. For v1's model it equals `workload_reference`'s on
  every case.
- **The deadline:** every chunk of every entry is classified within its period at 360 Hz. The slowest chunk
  anywhere takes 58,377 cycles (scalar, 128-sample chunks, 32 taps, the larger model, the cache off). Even
  the tightest deadline, a 16-sample chunk's, is 4.4 M cycles at 100 MHz.

| Method (v1's case: 64 samples, 16 taps, v1's model) | e2e, warm | Cold | v1's (1,528,505) ÷ cold | Kernel, warm | Samples a second, cold, at 100 MHz | A chunk's latency, cold: worst / mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1's pipeline, ported | 200,613 | 201,841 | 7.57× | 195,632 | 507 K | 13,681 / 12,596 |
| scalar, one hart | 177,293 | 177,977 | 8.59× | 173,482 | 575 K | 11,731 / 11,109 |
| DOT8, one hart | 200,067 | 201,091 | 7.60× | 196,138 | 509 K | 13,468 / 12,551 |
| two-hart pipeline | **175,038** | **175,849** | **8.69×** | 170,000 | 582 K | 14,242 / 12,977 |

- **Against v1:** v2's best in v1's window is the two-hart pipeline, 8.69× v1's, but only by 1.3% over
  scalar (8.59×), less than the layout effect. In the previous capture this same pipeline cell took 6.7%
  longer (hart 1's data misses 4,818 against 3,218), and scalar was best. At +4 waits scalar is faster
  (177,461 against 177,554). The gate holds either way, at 8.6× to 8.7×.
- **v1's pipeline, ported, is not v2's best.** It ties DOT8 on one hart: 200,613 cycles against 200,067.
  Both lose to the scalar FIR, because v1's DOT8 kernel gathers the taps a byte at a time.
- **The two-hart pipeline** overlaps one chunk's FIR with the last one's classifier. It is 12.7% faster than
  v1's pipeline, and 1.3% faster than scalar. The overlap's proof and its tuning are 20.5's (matrix.md
  §4.8).

| Warm e2e cycles at R, v1's model / twice its features | 16×8 | 32×8 | 32×16 | 64×8 | 64×16 | 64×32 | 128×8 | 128×16 | 128×32 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v1's pipeline | 157,278 / 173,142 | 175,001 / 182,527 | 155,977 / 163,210 | 183,400 / 187,292 | 200,613 / 204,442 | 214,386 / 209,557 | 187,872 / 190,102 | 223,213 / 225,154 | 289,975 / 291,862 |
| scalar | 107,577 / 131,736 | 124,865 / 136,927 | 132,606 / 144,409 | 133,699 / 139,645 | 177,293 / 183,169 | 215,682 / 221,550 | 138,077 / 141,044 | 199,699 / 202,601 | 309,172 / 312,067 |
| DOT8 | 155,807 / 181,638 | 173,619 / 184,223 | 155,010 / 165,333 | 182,729 / 188,617 | 200,067 / 205,271 | 205,610 / 210,746 | 187,209 / 189,858 | 222,671 / 225,227 | 289,616 / 292,165 |
| two-hart pipeline | 114,647 / 117,454 | 134,664 / 136,230 | 130,314 / 132,063 | 145,888 / 146,765 | 175,038 / 176,246 | 186,777 / 188,021 | 153,236 / 153,645 | 198,685 / 199,771 | 268,429 / 269,161 |

- **The DOT8 FIR overtakes the scalar one at 32 taps:** 205,610 cycles against 215,682 at 64-sample chunks,
  and 289,616 against 309,172 at 128.
- **The larger model is faster in one cell:** v1's pipeline at 64 × 32, 209,557 against 214,386. That is
  placement. Hart 1's FIR is a link in v1's serial chain, and its data misses there are 2,233 against
  3,318, while hart 0's are 519 in both: the larger model's arrays move the buffers in the direct-mapped
  cache.

**Step 2 is complete:** every workload of matrix.md §8's step 2 is in the matrix, and the scaling and v1
gates are measured. It was pushed as d097b2b.

Its figures are of its captures. Step 4's final capture is of record: step 3's method changes and the final
runtime's layout moved some of them. For example:
- two workers on GEMM 128×64×128: 432,920 → 429,210 cycles, 1.949× → 1.965×;
- the ECG pipeline against v1: 8.69× → 8.66×.

### Step 3: the other families (done, pushed as bb024f3)

**Captured on clean firmware sources:**
- `build/matrix/s3-mem`, `s3-dma` and `s3c-coh` from c8aea66;
- `s3b-dsp` and `s3b-ng` from ef23ab8, after the step's last review (only these docs were uncommitted).

The runs:
- **14,930 entries captured, 230 unsupported, 17,451 records.**
- The coherence and DSP runs include step 2's cases there, now on the fixed method.
- **Every record** passes both validators and its family's independent oracle.
- **Determinism:** 201 entries run again give byte-identical records and firmware.
- **Cold and warm:** each of the 5,759 pairs differs only in the cold word.

**The method, after three reviews in the step:**
- **Each window's START and FREEZE** are now in the code the warm-up runs too. Where the warm-up called the
  work from elsewhere, the window's own last line was fetched cold inside it. That biased small DMA copies:
  the aligned crossover read 255 bytes, and reads 127 now.
- **The armed hand-over** returns only once hart 1 waits at its flag. About 25 cycles of the dispatch had
  fallen inside two-worker kernel windows.
- **Poisoning by the writer** extends to the new cases. shared_mix's hart 1 now resets its own half, and the
  first version's hart 1 had taken 149 data misses against 21.
- **The oracles' new exact checks:**
  - the DMA's reads, writes and invalidations;
  - the AMO and sc counts;
  - the rings' links;
  - every byte and guard of each copy.
- **Four new readings of the plan** are in matrix.md §10 (accepted by the owner with 20.4's sign-off):
  - 10: one window for one-hart microbenchmarks and for the DMA;
  - 11: the memory family's details;
  - 12: the armed hand-over's own code can leave a few of hart 1's instruction lines cold;
  - 13: the FIR's 256 samples as 256 outputs.
- **The DSP's FFT** first took 1.94× v1's cycles: a divide a butterfly, from a loop written for the split. It
  now runs v1's loops, and its scalar time at R, warm, is v1's to within 58 cycles. At +4 waits and with the
  cache off, the code around the loops differs from v1's, and so do the times: 318,575 against 315,785, and
  400,250 against 407,694.

**The memory hierarchy** (`s3-mem`, §4.2): 1,050 planned, 1,034 captured. The two-hart streams on the one-hart
build are unsupported. Determinism 35/35; 464/464 pairs.

| Cycles a read (warm / cold) | R | +4 waits | Cache off | Cache off, +4 |
| --- | ---: | ---: | ---: | ---: |
| sequential ring, 1–4 KiB (256 B: 9.1 / 9.5) | 9.0 / 9.3–9.4 | 9.0 / 9.8–9.9 | 10.0 / 10.0 | 14.0 / 14.0 |
| sequential ring, 32 KiB | 10.25 | 12.25 | 10.0 | 14.0 |
| random ring, 32 KiB | 13.5 | 20.7 | 10.0 | 14.0 |
| strided, 1 word | 8.25 | 10.26 | 8.0 | 12.0 |
| strided, 4 words or more | 12.0–12.2 | 20.0–20.2 | 8.0–8.2 | 12.0–12.2 |
| working set, ≤ 4 KiB | 7.0 | 7.0 | 8.0 | 12.0 |
| working set, ≥ 8 KiB | 8.25 | 10.25 | 8.0 | 12.0 |

- **The working set's knee** is the 4 KiB data cache.
- **Beyond the cache, the cache off is faster for scattered reads:** a line refill costs more than one
  uncached word, by 3.5 cycles at R and 6.7 at +4 waits.
- **memcpy,** v1's fair copy, in bytes a cycle at R, warm:
  - aligned or at the same offset: 0.72 at 4 KiB, 0.55 to 0.57 beyond the cache;
  - at different offsets: 0.08, v1's byte path.
- **Two harts streaming** (16 KiB halves, a bank's lines each, hart 1's start swept over 8 staggers), against
  one hart alone at 21,583 cycles:
  - **in one bank:** 21,715 to 23,749 cycles (median 21,752), 6 to 6,056 bank conflicts. The collisions
    depend on the harts' phase: +0.6% to +10.0% on the one hart's time;
  - **in different banks:** 21,725 to 21,748, at most 3 conflicts;
  - **with the cache off,** under 9 conflicts in either: an uncached word holds its bank briefly.

**The DMA** (`s3-dma`, §4.3): 11,232 entries, all captured, through v1's driver against v1's fair CPU copy.
The points:
- **sizes:** 25, from 0 bytes to 32 KiB, v1's own included;
- **alignments:** 6, including v1's (1, 1) and (1, 2);
- **the destination** cached or not;
- **the overlap cases.**

Determinism 75/75; 4,992/4,992 pairs.

| R, warm, aligned | DMA cycles | Bytes a cycle | DMA busy | CPU copy | Bytes a cycle |
| --- | ---: | ---: | ---: | ---: | ---: |
| 256 B | 184 | 1.39 | 38 | 424 | 0.60 |
| 1 KiB | 315 | 3.25 | 135 | 1,480 | 0.69 |
| 4 KiB | 662 | 6.19 | 518 | 5,718 | 0.72 |
| 32 KiB | 4,251 | **7.71** | 4,102 | 59,471 | 0.55 |

- **The crossover:** the DMA beats the CPU copy for every size from:
  - 127 bytes when aligned, 63 to 127 at the same offset, and 15 to 31 at different offsets, warm or cold, at R
    and with the cache off;
  - aligned at +4 waits, 255 bytes, or 127 with the destination cached when warm; at +2 waits cold, 255
    with the destination not cached; at +4 waits with the cache off, 127 warm.
- **A cached destination** costs the DMA invalidations: exactly one a line up to 1 KiB, 247 at 4 KiB, where
  the 4 KiB cache aliases. Its time moves by 21 cycles or less at 64 bytes, 1 KiB and 4 KiB.
- **The overlap:**
  - The copy disappears behind unrelated work when the work is at least as long. A 16 KiB copy beside a 1 KiB
    checksum takes 2,319 cycles, against 4,342 one after the other.
  - **At 16 KiB** the saving is nearly all the DMA's busy time, on hart 0 and on hart 1: 1,980 to 2,066 of
    its 2,055 to 2,110 busy cycles.
  - **Smaller copies, hart 0, warm,** save less of it: 73 of 138 at 1 KiB, 435 of 532 at 4 KiB.
  - **Cold at 1 KiB,** hart 0's overlap is 8 to 15 cycles slower than serial.
  - **Hart 1** saves 177 to 199 cycles at 1 KiB, more than the DMA's 134 to 146 busy cycles: the driver's own
    time around the job overlaps hart 1's work too.
  - Polling is not counted as work (matrix.md §10.10).

**The coherence cases** (`s3c-coh`, §4.4): v1's nine kernels and a new producer/consumer, at 2, 129 and 1,024
items, across the harts and workers × memory cross. With step 2's cases, 645 planned; 504 captured, 141
unsupported. Determinism 26/26; 88/88 pairs.

| 1,024 items, R, warm | 1 worker | 2 workers, e2e | 2 workers, kernel | Kernel, a item |
| --- | ---: | ---: | ---: | ---: |
| atomic add | 9,257 | 5,128 | 4,835 | 4.7 |
| lr/sc counter | 23,592 | 12,337 | 19,097 | 18.6 |
| CAS by lr/sc | 44,063 | 22,605 | 31,913 | 31.2 |
| lock-protected sum | 30,772 | 39,470 | 39,516 | 38.6 |
| false sharing | 8,235 | 4,618 | 4,342 | 4.2 |
| padded control | 9,258 | 4,631 | 4,330 | 4.2 |
| ping-pong | — | 116,258 | 115,933 | 113.2 |
| SPSC queue | — | 62,948 | 62,527 | 61.1 |
| shared mix | 99,487 | 51,185 | 50,006 | 48.8 |
| producer/consumer (4 rounds) | — | 97,670 | 97,619 | 95.3 |

- **False sharing of v1's AMO counters costs nothing here.** The false-shared and padded counters take the
  same cycles on two workers, with no invalidations between them. AMOs are done at the memory, and the
  write-through caches never hold a line exclusively. Plain stores to a line the other hart reads were not
  measured.
- **On one worker** the two differ by 12%, from code generation alone (matrix.md §10.11).
- **The counters contend more in the kernel window:**
  - the lr/sc counter's harts break each other's reservations once an increment (1,013 sc failures against
    3);
  - the CAS counter is 41% slower than in e2e.

  The windows differ in more than the harts' phase, though. v1's e2e window starts hart 1 from reset about
  250 cycles late, cold (34 instruction misses against 4), and on v1's code path; the kernel window releases
  both within about 100 cycles, under the runtime. So phase is a likely cause but not a measured one: no
  stagger sweep was run here, as it was for streaming.
- **The lock-protected sum** is slower on two workers than on one.
- **The scaling gate's workloads** on the fixed method:
  - the gate's reduction, 1.962×;
  - the GEMMs: 1.974×, 1.986× and 1.965×.

**DSP** (`s3b-dsp`, §4.5): the dot product, the FIR and v1's FFT beside Conv2D. 674 planned, 657 captured;
two workers on the one-hart build are unsupported. Determinism 27/27; 66/66 pairs.

| Kernel window, R, warm | Scalar | Two workers | DOT8 (v1's) | NPU |
| --- | ---: | ---: | ---: | ---: |
| dot, K = 8 | 187 | 424 | 204 | 76 |
| dot, K = 256 | 2,999 | 1,783 | 2,088 | 164 |
| dot, K = 4,096 | 48,778 | 24,610 | 33,982 | **1,597** |
| FIR, 256 outputs, 4 taps | 29,222 | 14,915 | 29,702 | 823 |
| FIR, 32 taps | 108,088 | 54,305 | 85,716 | 1,725 |
| FIR, 256 taps | 738,872 | 369,682 | 533,727 | **8,949** |

- **The NPU** runs the FIR as one job whose A rows overlap (A_STRIDE 1), with no Toeplitz copy. It is 36× to
  83× scalar on the FIR, and 31× on the 4,096-long dot.
- **Two workers** halve the FIR and the long dots; under K = 64 the hand-over outweighs the work. The
  two-worker kernel window exceeds its e2e window at every K from 7 to 4,096, and on the 4- and 256-tap FIRs,
  by about 70 cycles or less (matrix.md §10.12).
- **The dot's two workers at K = 0 and 1** leave hart 1 nothing to do but are kept: they measure the hand-over
  itself. The one-row GEMM's two workers are unsupported, since the sweep compares engines.
- **The FFT,** v1's code, takes 314,687 cycles on one worker in v1's window (v1's own in the CPU family:
  314,745), and 196,134 on two (1.60×). The transforms alone take 284,612 and 164,281 (1.73×).

**The NPU GEMM sweep** (`s3b-ng`, §4.6):
- **The shapes:** 40 of them: v1's twelve study shapes, npu.md §7's three, the N sweep (64×64×64 is in both),
  every partial-tile pair of M and N, and N = 1 at a large K.
- **The methods:** the NPU, v1's DOT8 kernel on one and two workers, and scalar under 32³.
- **The runs:** 1,559 planned, 1,503 captured. Unsupported: two workers on the one-hart build, or on a one-row
  C. Determinism 38/38; 149/149 pairs.

| NPU, R, warm | Kernel | e2e | PE utilization (MACs ÷ 64 × job cycles) | Tiles | Bytes a MAC |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1×1×1 | 69 | 138 | 0.0% | 1 | 20.0 |
| 8×8×31 | 235 | 302 | 15.5% | 1 | 0.41 |
| 32×32×32 | 1,363 | 1,426 | 38.6% | 16 | 0.19 |
| 64×64×64 | 5,260 | 5,321 | 78.3% | 64 | 0.09 |
| 96×96×96 | 15,207 | 15,273 | **91.0%** | 144 | 0.06 |
| 128×64×128 | 17,676 | 17,741 | **92.8%** | 128 | 0.05 |
| 64×128×64 | 9,924 | 9,989 | 82.8% | 128 | 0.09 |
| 33×33×32 | 1,596 | 1,665 | 34.6% | 25 | 0.19 |
| 784×1×25 | 4,477 | 4,536 | 6.9% | 98 | 1.44 |

- **Bytes a cycle,** read and written over the job, against the 64-bit port's 8:
  - 3.3 to 4.7 on the large square-ish shapes (4.70 at 64³, 3.25 at 128×64×128);
  - up to 7.61 on the narrow ones (32×1×784), which stream A.
- **The set-up** (the descriptor, e2e less kernel) costs 57 to 69 cycles a job.
- **Against the best DOT8 code** (19.4's, the coherence family's GEMM), npu.md §7's three cases: in kernel
  windows the NPU is 38× to 46× one worker and 19× to 23× two (40× to 48× and 20.5× to 24× end to end). v1's generic DOT8 kernel, the sweep's DOT8 method for every
  shape, is far slower: up to 890× on 128×64×128. Those ratios are against that kernel, not the best code.
- **The NPU's geometry:**
  - the 8×8 array is 3.2× to 4.1× the 4×4 builds on §7's cases;
  - the second strip saves 10% to 15% on the 8×8 (64×64×64: 5,260 against 5,844 on one strip), and 2.9% to
    7.1% on the 4×4.
- **Two workers' kernel windows can exceed their e2e windows** with v1's DOT8 kernel: in 79 records, by up
  to 0.3% (128×64×128 at +2 waits: 29,487 cycles more). The cause is not established:
  - **The large shapes:** the kernel window has more bank conflicts (398,728 against 305,516 in this case's
    first capture), with equal data misses. That points to contention, but hart 1 starts at the same moment
    in both windows (93 cycles in), so it is not the release.
  - **33 of the 79:** they show no extra conflicts; their excess is 38 cycles or less, with 1 to 3 more
    instruction misses.

  How many records show it moves with the layout.

**Step 3 was pushed as bb024f3** after five watchdog reviews. Its figures are of its captures; step 4's
final capture moved some of them (below).

### Step 4: the full matrix (done; signed off)

**The final capture:** every family from one clean commit, 87c6b0a (`build/matrix/m5-*`), bundled in
[`results/phase20/matrix-20.4/`](results/phase20/matrix-20.4/README.md).

| Family | Captured | Unsupported | Determinism | Cold/warm pairs | Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU | 108 | 0 | 18/18 | 54/54 | 30 s |
| coherence | 504 | 141 | 26/26 | 88/88 | 49 s |
| DSP | 657 | 17 | 27/27 | 66/66 | 97 s |
| ML | 124 | 2 | 16/16 | 13/13 | 1,298 s |
| ECG | 648 | 36 | 33/33 | 72/72 | 84 s |
| memory | 1,034 | 16 | 35/35 | 464/464 | 90 s |
| NPU GEMM | 1,503 | 56 | 38/38 | 149/149 | 452 s |
| DMA | 11,232 | 0 | 75/75 | 4,992/4,992 | 474 s |
| **All** | **15,810** | **268** | **268/268** | **5,898/5,898** | |

- **The manifest is complete:** 17,444 entries.
  - **15,810 captured,** with 19,018 records. That is over twice matrix.md §3's estimate of about 8,000;
    the DMA family's sizes, alignments and offsets alone give 11,232 entries.
  - **1,166 unsupported,** each with its reason:
    - 268 are the runner's: two workers on one hart, the pipeline and the streams on one hart, one row of C,
      dispatch and join on one hart;
    - 898 are matrix.md §7's: the configurations (the instruction cache off, an L2, zero-wait memory; and,
      where the NPU ran, ECG's pipelines included, a 2×2 array and 8×8 on a 32-bit port) for each captured
      case and method, and the methods (the NPU, two workers, DOT8, the DMA) for each case §7 names.
  - **None failed.**
  - **468 planned for 20.5:** each captured case and method with the 2 and 8 KiB caches (matrix.md §2).
  - Two of §7's items are not combinations of the matrix's cases, so they are not entries: v1's
    private-region permissions and warm stop, and energy per inference.
- **Correctness:** every record passes both validators and its family's independent oracle.
- **Determinism, over the whole matrix:** a second full capture gives all 15,810 entries' records byte for
  byte, on the same firmware and simulations (equal hashes). It was made from 437ca62. Six of its eight
  runs had uncommitted docs and analysis scripts, which the capture does not use.

**R is 20.3's R.** 20.4's one RTL change (328891a: `DCACHE` and the NPU's 32-bit adapter) leaves the
default build's behaviour as it was:
- **Every R firmware image of the final capture,** 3,885 of them, replayed on the SoC simulation built from
  20.3's commit e6e2b98, prints the same console byte for byte and ends on the same cycle
  (`matrix_golden.py`; every record and counter, cycle for cycle).
- `make core-shell-equiv` against e6e2b98: 879 of 879 runs the same, cycle for cycle and RVFI record for
  record.

Step 1's "the default build is the same logic" now rests on these. The netlist is not identical. Synthesis
maps 20.4's RTL to 22 more LUTs (34,519 against 34,497). On the same strategy, the routed builds have 34,001
against 33,975 (o5end) and 33,871 against 33,866 (the defaults).

**The totals gate** (soc.md §11), checked in every record from counters that nothing in the window reads
(`reconcile_totals`):
- **The NPU's bytes against the fabric's.** Each NPU request is a read of the port's width or one C word, so
  f_accepted_n = npu_bytes_read / port bytes + npu_bytes_written / 4. It holds in all 19,018 records, the
  4×4 and 32-bit builds included.
- **The cycles, bounded by the fabric's counters.** An engine's requests are accepted or waiting only while
  it runs, at most one a cycle on each port. So:
  - f_accepted_n + f_waited_n ≤ npu_job_cycles;
  - the DMA's R and W requests each ≤ its busy cycles;
  - its bytes ≤ 8 × its busy cycles.

  All 19,018 records hold, multi-job windows included. The bounds bite where an engine runs: 2,429 records
  have NPU job cycles, with the fabric's count reaching 0.983 of them; 6,012 have DMA busy cycles, reaching
  0.999.
- **One-job windows** (1,458 NPU, 5,832 DMA): the totals equal the job's own counters, read after FREEZE
  (`MATRIX_JOBS`, matrix.md §6). That holds by construction, since TOTAL adds each JOB. So it checks the
  plumbing, not the engine.
- **The MACs and the DMA's bytes** match each oracle's exactly, against the workload. The DMA's reads,
  writes, waits and invalidations are the validator's, checked against the fabric's.

**The overlap gate** (`matrix_overlap.py`). Every two-worker record is classed by its harts' intervals
(matrix.md §10.7). Where hart 0 works after its last wait, its interval spans that wait, so it cannot show
overlap. There, the script reads overlap from a speedup over the same kernel on one worker. That is a new
reading of the gate, put to the owner as matrix.md §10.14.

| Class | Records | Cases |
| --- | ---: | --- |
| Overlap, stamped: the intervals overlap by more than hart 1's two stamps (40 cycles), and both harts retire | 1,422 | the GEMMs (the sweep's and the gate's), the FIR, the streams, the coherence cases, ping-pong, the queues, producer/consumer; Conv2D, the FFT, MNIST and CIFAR in their kernel windows; dispatch and join once (+4 waits) |
| Overlap, by speedup (§10.14, accepted): hart 0's interval spans its wait, and the one-worker twin is slower | 117 | both reductions (both windows), Conv2D, the FFT, MNIST and CIFAR in their e2e windows, the dot at K ≥ 64 (both windows) |
| Not shown: hart 0's interval spans its wait, and the one-worker twin is not slower (0.20× to 0.85× here), or there is none | 823 | ECG's four pipelines (756: their proof is 20.5's, matrix.md §4.8), the dot at K ≤ 8 (and once at 64), dispatch and join |
| No overlap within the stamps | 85 | the coherence cases at 2 items and dispatch and join: in 70, hart 0 is done before hart 1 starts; the other 15 overlap by under 40 cycles |
| The DMA beside hart 1, against its serial twin | 108 | saves 0.8% to 48% (1.008× to 1.92×) |
| The DMA's serial twins | 108 | — |

- **No stamping faults.**
- **Stamps show concurrent intervals, not useful work.** 15 stamped records are slower than one worker:
  lock_sum, whose lock serialises the harts (down to 0.54×); shared_mix at 2 items; the 3×1×7 GEMM.
- **Speedups over the same kernel on one worker** reach 2.23×. 71 records exceed 2×:
  - **DOT8 GEMMs:** 16×16×64, 33×33×32, 96³, 64³ and 64×32×64. Two harts have two data caches: at +4 waits,
    16×16×64 takes 1,540 misses on one worker and 132 + 519 on two. Those conflicts follow the layout: the
    one-worker twin is 12% slower here than in step 3's capture, which would have given 1.99×.
  - **Conv2D im2col** on two workers.
  - **The padded counters:** v1's one-worker loop retires 5,141 instructions, against 4,298 on both harts
    (2,161 + 2,137, at R).

**The scaling gate, measured** (required in 20.5). Two workers against one, DOT8 for the GEMMs:

| Workload (warm, R) | e2e, 1 worker | e2e, 2 workers | Speedup | Kernel, 1 | Kernel, 2 | Speedup | e2e across the six configurations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| reduction, the gate's | 73,958 | 37,687 | **1.962×** | 36,998 | 19,192 | 1.928× | 1.944× to 1.971× |
| GEMM 64×64×64 | 215,175 | 109,033 | **1.973×** | 201,219 | 101,873 | 1.975× | 1.957× to 1.995× |
| GEMM 96×96×96 | 703,722 | 354,285 | **1.986×** | 672,634 | 338,491 | 1.987× | 1.981× to 1.998× |
| GEMM 128×64×128 | 843,600 | 429,210 | **1.965×** | 805,370 | 404,472 | 1.991× | 1.948× to 1.998× |

All four clear 1.8× in every configuration both ran in: R warm and cold, the cache off, +1, +2 and +4 waits.

**Against v1, measured** (required in 20.5). The comparison uses v1's inputs and window, cold, at R:

| Workload | v1's best | v2's best | Method | v1 ÷ v2 |
| --- | ---: | ---: | --- | ---: |
| Conv2D 32×32 K=5, ×4 | 4,837,408 | 84,551 | NPU, direct | **57.2×** |
| reduction, 1,024 words ×4 | 233,112 | 58,874 | v1's, two workers | **3.96×** |
| MNIST MLP, an image | 433,903 | 6,019 | NPU, an image at a time | **72.1×** |
| streaming ECG, 16 × 64 | 1,528,505 | 176,523 | the two-hart pipeline | **8.66×** |
| CIFAR-10, 20 images | 65,568,218 | 4,085,916 | NPU, direct | **16.0×** |
| CoreMark (CRC run) | 1,922,272 | 467,475 | the Aster core | **4.11×** |
| Dhrystone | 3,128,553 | 859,400 | the Aster core | **3.64×** |
| sort/search | 2,183,301 | 696,883 | the Aster core | **3.13×** |
| FFT | 3,057,473 | 317,346 | the Aster core | **9.63×** |
| strided | 9,087 | 2,732 | the Aster core | **3.33×** |
| Conv2D (CPU) | 5,820,652 | 1,111,074 | the Aster core | **5.24×** |

Every v1-retained workload has a faster v2 method. The CPU kernels compare with v1's `aster_minimal`
figures.

**What moved since steps 2 and 3** (`checks/moved-from-steps-2-3.log`, on the same simulations):
- **Layout alone, for step 3's captures and step 2's CPU, ECG and MNIST ones.** The final firmware's runtime
  gains 228 bytes of code, 72 of read-only data and 64 of .bss, for the jobs' counters read after FREEZE.
- **Step 3's method changes too, for step 2's coherence, Conv2D and CIFAR.** The armed hand-over now waits
  for hart 1 at its flag, and each window's START and FREEZE sit in the code its warm-up runs. CIFAR's
  hand-over change moved it by under 0.04%.

| From | Changes | Windows | Unchanged | Median change | Range |
| --- | --- | ---: | ---: | ---: | --- |
| step 2: CPU | layout | 108 | 37 | 0.001% | −0.00% to +0.59% (the FFT) |
| step 2: coherence | layout and method | 170 | 26 | 0.013% | −8.5% to +6.3% (dispatch and join's warm-up) |
| step 2: Conv2D | layout and method | 152 | 51 | 0.005% | −0.13% to +0.04% |
| step 2: ML | layout (MNIST); layout and method (CIFAR) | 235 | 128 | 0.000% | −1.4% to +10.9% (MNIST on two workers, +4 waits: layout) |
| step 2: ECG | layout | 1,224 | 310 | 0.050% | −7.9% to +5.9% (the pipelines at +4 waits) |
| step 3: memory | layout | 1,194 | 949 | 0.000% | −4.3% to +4.7% (the same-bank streams) |
| step 3: DMA | layout | 11,232 | 11,003 | 0.000% | −0.85% to +0.60% |
| step 3: coherence | layout | 920 | 745 | 0.000% | −42% to +69% (lr/sc and CAS contention) |
| step 3: DSP | layout | 1,248 | 915 | 0.000% | −2.0% to +26% (the dot's shortest kernel windows) |
| step 3: NPU GEMM | layout | 2,857 | 2,100 | 0.000% | −18% to +43% (the small GEMMs on two workers) |

What layout alone moves, by kind:
- **The long CPU kernels:** under 0.6%.
- **The DMA's copies:** under 0.9%.
- **The memory family's one-hart reads and copies:** up to +2.0%.
- **One hart elsewhere moves too,** wherever the direct-mapped caches' conflicts shift:
  - the 16×16×64 GEMM at +4 waits: +12% on DOT8 (154,296 cycles) and on scalar (219,470);
  - ECG on one hart: −5.5% to +1.2%;
  - the dot's shortest windows: up to +26%.
- **Two harts on a long kernel move too:** MNIST on two workers, end to end, +5.3% at R (4,880,762 →
  5,137,682 cycles, its speedup over scalar 1.99× → 1.88×) and +10.7% at +4 waits (+10.9% in its kernel
  window, the table's extreme).
- **Two harts in contention move most:** −42% to +69%. Which banks the harts meet in also follows the layout.
  This qualifies step 3's "contention by phase": the layout moves it too.

Figures at that scale are readings of one layout, not properties of the method. The tables of steps 2 and 3
are their captures'; the bundle's figures are of record.

**Timing in context** (the owner's rule of 8 October 2026), on 20.4's RTL (unchanged at 87c6b0a):
- **19 strategies,** each built at 10 ns in context; all meet setup and hold. The worst slack ranges from
  +0.033 to +0.333 ns, median +0.163 (`timing/builds.csv`). 20.3's ten gave +0.031 to +0.375, median about
  +0.1.
- **The best, m4-o5asm-mgi** (`over=0.5;place=AltSpreadLogic_medium;route=MoreGlobalIterations`): +0.333 ns,
  hold +0.022.
  - **Reproducible:** rebuilt from the same sources, it gives the same configuration data and slack
    (`timing/reproducibility.txt`).
  - **Its near-critical endpoints:** seven are under 0.4 ns: the fabric's snoop into hart 1's data cache,
    the NPU's register queue into the fabric, the SoC's CONTROL run bit into bank 0, and two data-cache
    writes.
- **The default directives** give +0.154 ns.
- **Area:** 33,988 LUTs (63.9%), 77 block RAM tiles (55.0%), 27 DSPs (12.3%): under 80%.
- **Signed off by the owner** (9 October 2026): 20.4's timing on m4-o5asm-mgi. As with 20.3's, the margin holds for
  that build, not across placements.
- **The variant builds** are axes of the simulated matrix, never built for the board, so no timing claim is
  made for them:
  - the waits are simulation-only (matrix.md §2);
  - aster_soc.sv marks `DCACHE` as simulation-only;
  - the NPU's other geometries and one hart are RTL parameters.

  A variant that 20.5 adopts needs its own in-context timing.

**The reviews in step 4** (two watchdog rounds) changed the method before the final capture:
- **Overlap:** hart 0's whole-window interval no longer counts as overlap on its own, and a stamped overlap
  must exceed the stamps.
- **Totals:** the cycle totals are bounded by the fabric's counters, beyond the one-job identities.
- **Provenance:** the final capture was made from a committed, clean tree.
- **After the capture:**
  - the manifest gained §7's exclusions;
  - the golden replay checks each firmware's hash and starts from fresh consoles;
  - this record's claims were corrected (the cause of the moves, the scaling table's configurations).

**For 20.5:** the board's console is 4 KiB. A warm entry's two records already come to about 4,500 bytes,
and `MATRIX_JOBS` adds about 130 bytes a record. The board run must drain the console between windows, or
print less.

**Step 4 is complete:**
- the manifest is complete;
- the overlap and the totals are reconciled;
- the evidence is bundled;
- the timing is re-checked by the owner's rule.

20.4's exit is met, with the owner's reading in §10.14:
- **Correctness and records:** met.
- **Overlap:** shown for every multicore workload, except:
  - ECG's pipelines, whose proof is 20.5's (matrix.md §4.8);
  - the smallest sizes, where the hand-over outlasts the work (published as losses).

  The overlap of some workloads rests on the speedup reading, matrix.md §10.14:
  - both reductions, in both windows;
  - Conv2D, the FFT, MNIST and CIFAR, in their e2e windows;
  - the dot at K ≥ 64, in both windows. Its K ≤ 8 records are not shown, so without §10.14 the dot shows
    overlap at no size.

  The owner accepted that reading with the sign-off. 20.5 could still stamp hart 0's share there, for
  overlap from the stamps.
- **Totals:** reconciled.
- **Scaling and v1:** measured.

**Signed off by the owner, 9 October 2026** ("I sign off"), after this record. The sign-off covers 20.4,
matrix.md §10.10–14, and its timing on m4-o5asm-mgi (soc.md §13). 20.5 begins after 03:00 on 10 October
2026.

## Milestone 20.5: the tuning and the board run (in progress, 10 October 2026)

**The plan:** [`tuning.md`](tuning.md), **approved by the owner on 10 October
2026** with its nine decisions as recommended (soc.md §13). The steps
(tuning.md §11):
1. the infrastructure, including a board smoke run of 20.4's signed-off image;
2. the cache geometry;
3. the tuning;
4. the final configuration;
5. the board run;
6. the report and the closeout audit.

### Step 1: the infrastructure (done; awaiting its review and the owner's word on the layouts)

**The board.** The full two-hart SoC ran on the PYNQ-Z1 for the first time, through the new runner
(`matrix_board.py`).
- **First run:** every program that finished ended at exactly its simulated tohost cycle, 33 of 33.
- **The console:** its 4 KiB ring overflowed. The ARM side's reader fell 4,508 bytes behind, and consoles were
  overwritten before being read. So by the plan (tuning.md §7.1) the console grew to 16 KiB, as an RTL change:
  - **verified:** soc-tests, all 29 checks, pass;
  - **timed** in context on ten strategies, all meeting 10 ns: +0.031 to +0.373 ns. The best, o5asm-mgi at
    +0.373 ns, rebuilt identically. 80 block RAM tiles (57%).
- **The smoke set on it:** 36 of 36 programs end on the board exactly as simulated, at 100 MHz.
- **ECG's stamped programs** print up to 20 KB of stage lines, faster than the reader drains. Paced at 1 ms a
  line, all 72 end on the board as simulated, within 5,003 bytes of the reader.

**The layout knob** (tuning.md §3): pads between the image's parts, each buffer's offset kept past its
alignment.
- **L0 is the layout of record:** at every pad 0 the images are byte-identical to the final capture's (5,808 of
  5,920 at this commit; those that differ are the two-worker builds with stamps, and 15 commutative `xor`s).
- **The proof:**
  - the five approved layouts missed the 16×16×64 GEMM's 20.4 move (+12%; they moved it ±0.3%);
  - eight layouts reach at least half of 20.4's move in 261 of 262 windows. The one short is the 2-item lr/sc
    kernel window with the cache off, which 20.4 moved by 6 cycles.
- **For the owner:** eight layouts instead of decision 2's five. No tuning change is judged until then.

**The overlap evidence** (tuning.md §6): both harts stamp their shares, on `MATRIX_SHARE` lines.
- **Every two-worker program** now shows its overlap from stamps: the reductions, Conv2D, the dot, FIR and FFT,
  MNIST and CIFAR. The exceptions are the dot at K = 0 and 1, where hart 1 has no work.
- **The speedup reading** (matrix.md §10.14) is now a cross-check.

**ECG's stage stamps** (tuning.md §5), in 72 separate `_stages` entries, show:
- **v1's ported pipeline:** sequential per chunk;
- **the two-hart pipeline:** each chunk's FIR beside the previous chunk's classifier, 26,814 cycles of overlap
  in v1's case. It is bound by v1's byte-gathering DOT8 FIR, about 10,400 cycles a chunk.

**The copy helper** (`aster_copy`): the DMA or v1's fair copy, by size and offset.
- **Its test** (`soc_copy`): 1,200 copies on two builds, every byte, guard and choice checked.

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
- [x] 20.1 as in the table above — signed off by the owner (7 October 2026), the §4.4 arbitration change approved; re-confirmed by the owner (7 October 2026) after 20.2 corrected its timing (the fabric missed 10 ns; carried by 20.2)
- [x] 20.2 as in the table above — signed off by the owner (8 October 2026); its timing signed off on r19's build (7 October 2026)
- [x] 20.3 as in the table above — signed off by the owner (8 October 2026); its timing signed off on g2-o5end (8 October 2026)
- [x] 20.4 as in the table above — signed off by the owner (9 October 2026), with matrix.md §10.10–14; its timing signed off on m4-o5asm-mgi (9 October 2026)
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

