# Milestone 20.2 — the two-hart SoC

The text of record is the "Milestone 20.2" section of
[`../../../phase20.md`](../../../phase20.md), with soc.md §13's 20.2
clarifications and the owner's decisions (three rounds, 7 October 2026).
`SHA256SUMS` covers every file here.

The exit (phase20.md's milestone table) has three parts: soc.md §10.4's
regression; litmus, the memory checker and reset tests on two harts; and
10 ns in context. All of it is on the final RTL (commit 345a302's).

**`capture-1/`, `capture-2/`** (`make soc-tests`, run twice; every file is
the same in both, as §10.4's determinism asks):
- **`soc-board.log`:** 18.7's 99 programs on the regression build (hart 1
  held, the CPU shell's register page). Each runs as in the CPU shell, cycle
  for cycle and RVFI record for record. The CPU shell itself runs in
  lockstep with Spike (`suites/core-aster-l1-tests.log`).
- **`soc-tests.log`:** the two-hart programs, run on the device builds
  `soc_dev` and `soc_dev_w3` (three added memory waits):

  | Program | `soc_dev` | `soc_dev_w3` |
  | --- | --- | --- |
  | Litmus: 26 shapes × 2,000 trials, nothing forbidden (`litmus.*.log`) | 2,642,894 loads checked; 125,902 sc's (29,902 failed); 64,000 AMOs; 1,073,899 snoops | 2,844,626 loads checked; 132,176 sc's (36,176 failed); 64,000 AMOs; 1,073,899 snoops |
  | Hart 1's reset stress: 4 seeds × 1,000 rounds, 5,334 resets | caught 520 owed answers, 69 AMOs before their write, 37 refills, 315 reservations | caught 1,041 owed answers, 55 AMOs before their write, 36 refills, 350 reservations |
  | The runtime's dispatch and join, mean round trip | 72 cycles | 90 cycles |

  The devices are also checked, and each hart's RVFI trace is consistent.
- **`soc-gates.log`** (`make soc-gates`, which `soc-tests` runs first):
  §10.4's comparison with the Phase 19 SoC on its gate programs. Every
  result and console record is the same apart from its cycles.
  - **Why cycles differ:** Phase 19's refills had a port of their own and
    its data cache always went ahead of the NPU. In Phase 20's banks the NPU
    waits behind refills in its bank, a hart can wait behind the NPU, and
    the NPU no longer waits behind data accesses in other banks.
  - **The counts:** the testbench attributes every NPU wait, and counts the
    harts' waits behind the NPU, from the fabric's own arbitration.

  | Program | Without the buffer and register stage | NPU waits | Hart waits behind the NPU | As built (the two owner-approved levers) |
  | --- | ---: | --- | ---: | ---: |
  | GEMM gate | +4 | 4, behind refills | 4 | +120 |
  | MNIST | 0 | none | 0 | +264 |
  | Coherence (it races the hart against the NPU) | +206 | 406: 403 behind data accesses, 3 behind refills | 348 | +565 |
  | Faults | 0 | none | 0 | 0 |

  As built, only the coherence program has conflicts: 418 NPU waits (412
  behind data accesses, 4 behind refills, 2 behind an AMO) and 370 hart
  waits behind the NPU. The levers' share on the final RTL, against the
  build without them: the GEMM gate +116 and MNIST +264, all the buffer's;
  coherence +359, of which the buffer +320 and the register stage +39;
  faults 0. A hart counts once a cycle, including a write the starvation rule
  refuses for the unit of the NPU's held-back read.

  - **What the script enforces, on both Phase 20 builds:**
    - the CPU's own measurements (`cpu_cycles`) are Phase 19's;
    - no NPU wait is unexplained;
    - without the levers, a program with no conflict keeps Phase 19's
      cycles exactly;
    - each difference and its conflict counts match the traced values.
  - **Phase 19's own gates pass on the Phase 20 SoC as built:** GEMM
    utilization 78–93%, speedups 41–48×, MNIST 6.64× at worst.
- **`make-soc-tests.out`:** also covers the devices against 8809f72's, in
  lockstep for 3 million cycles.

**`suites/`** (the final RTL):
- **`core-shell-equiv.1.log`, `.2.log`** (`make core-shell-equiv`, run
  twice, identical): the CPU shell from 8809f72 and the current one on 879
  runs: 7 modes × the kernels and every suite, plus 4 random modes × 40
  programs. Both shells must pass, with the same status line, RVFI trace,
  console and signature.
- **The L1 unit tests:** 200 seeds each.
- **`fabric-tests`:** every mode, including `equiv_fabric`, which compares
  the fabric with the golden every cycle.
- **The fabric mutants:** 36 of 36 caught, and 31 of 31 in the reference.
- **The rest:** the NPU suites, the core's suites, the performance gate and
  the board simulation.

**`fpga/`: r19, the sign-off build.** It is built with `make
fpga-aster-soc` and the default directives, in Vivado 2025.1, on the whole
device with the Zynq PS.
- **Slack:** +0.309 ns setup and +0.024 ns hold, with no failing endpoint
  among 73,582.
- **Routing:** all 55,257 nets routed. There is no DRC error, only
  DSP-pipelining warnings.
- **Utilization:** 33,685 LUTs (63.3%), 22,126 flip-flops (20.8%), 77
  block-RAM tiles (55.0%) and 27 DSPs (12.3%).
- **The worst path:** hart 1's data cache's registered request address,
  through the fabric's arbitration, to a bank's write enable. Only 2
  endpoints lie within +0.35 ns (`paths/`).
- **`bitstream.sha256`:** the bitstream's hash. The bitstream itself is not
  kept.
- **`reproducibility.txt`:** the build was made twice from the same
  sources, and the configuration data is identical.

The margin holds for this build, not across placements. The same RTL gives
+0.05 to +0.10 ns with other strategies, because placement varies by about
±0.1 ns. The owner decided to sign off on this build with the spread
recorded, and timing is re-checked in 20.3.

**`timing/builds.csv`:** every in-context build of 20.2's timing work, with
its slack and directives: the last build under each tag. There are 98, of
which 29 failed sign-off: 23 before the owner's first decisions, 4 of r5's
strategies, and r7 and r8. phase20.md's lever tables are drawn from them
(its baseline, −2.458 ns, is an earlier build under the tag `base`).
The three `experiment-bank-inputs-registered` rows are the timing-only
experiment measured for the owner, which is not functional.
