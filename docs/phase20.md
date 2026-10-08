# Phase 20: whole-SoC workload placement and concurrency

Status: **milestone 20.3 (the DMA) in progress.** 20.2 (the two-hart SoC) was signed off
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

