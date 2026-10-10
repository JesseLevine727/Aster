# The Phase 20 tuning and board run (milestone 20.5)

Status: **approved by the owner, 10 October 2026,** with every decision
in §10 as recommended ("approve all 9").

20.5 closes Phase 20 (soc.md §12):
- **Scope:** tuning from 20.4's captured data (DMA thresholds, cache
  geometry, partitioning and placement), ECG, and the board run.
- **Exit:**
  - every soc.md §11 gate, the scaling and v1 gates now required;
  - the final image on the PYNQ-Z1 at 100 MHz, on two boots;
  - the Phase 20 report and its closeout audit.

The starting point is 20.4's bundle
([`results/phase20/matrix-20.4/`](results/phase20/matrix-20.4/README.md)).
Its figures are of record. §10 lists the plan's decisions, approved by the owner.

**Out of scope,** as in Phase 20's non-goals:
- energy per workload;
- an L2, set aside by the owner;
- more than two harts;
- changes to the NPU.

## 1. What 20.4 found

| Finding (20.4's final capture, R unless stated) | What it means for 20.5 |
| --- | --- |
| **Hart 1 idles in every NPU method.** Hart 1 retires nothing in any of them. The NPU's busy share of the e2e window: CIFAR 3.8% (4.09 M cycles for 20 images); Conv2D im2col 3.1%, direct 32.8%; MNIST 59.2% on its per-image NPU method, 23.9% batched by 32 | The largest tuning target: the CPU work around the NPU (staging, im2col, requantization, pooling) on both harts, overlapped with the NPU's jobs (§4.3) |
| **The DMA's crossover:** it beats the CPU's fair copy from 127 bytes aligned; from 63 to 127 at the same offset; from 15 (warm) or 31 (cold) at different offsets. It moves 7.71 bytes a cycle against the CPU's 0.55 | A copy policy by size and alignment (§4.1). ECG's pipeline DMA-copies 64-byte chunks, below the aligned crossover. Its CPU method copies with a byte loop, not the fair copy |
| **The data cache's knee is at its size,** 4 KiB (step 3's table). A working set within it reads at 7.0 cycles a word; beyond it, at 8.25, and a random walk at 13.5 | 8 KiB may pay where working sets are 4–8 KiB; 2 KiB measures the other side (§4.2) |
| **Layout moves figures.** 20.4's runtime gained 228 bytes of code, 72 of read-only data and 64 of .bss, inserted between other objects. That moved the 16×16×64 GEMM by +12% at +4 waits on one hart, MNIST on two workers by +5.3% at R, and two-hart contention cases by −42% to +69%. The long CPU kernels stayed within 0.6% | Tuning must win across layouts, not on one (§3). Placement can make two-hart figures both faster and steadier (§4.4) |
| **Two workers lose on small work:** the coherence cases at 2 items, the dot at K ≤ 8, the 3×1×7 GEMM; lock_sum serialises the harts | Split only above measured sizes (§4.3) |
| **ECG:** the two-hart pipeline is 8.66× v1, but cold it is only 0.8% faster than scalar on one hart (176,523 cycles against 177,975). Its overlap is not yet shown | The overlap proof and its tuning (§5) |
| **matrix.md §10.14's speedup reading carries the overlap** of both reductions and the dot at K ≥ 64 (both windows), and of Conv2D, the FFT, MNIST and CIFAR (e2e windows) | Stamps can show it directly (§6) |
| **The board's console is a 4 KiB ring,** which the board script already drains while a program runs. 841 of the 3,885 R programs print more than 4 KiB: up to 5,132 bytes, none over 8 KiB. 20.5's stamps will add more | Draining must keep up, or the console grows (§7.1) |
| **The gates hold today:** scaling 1.944× to 1.998× end to end; the tightest point is 1.882×, the gate reduction's kernel window at +4 waits. v1 3.13× to 72.1×; timing +0.333 ns; area 63.9% of LUTs, 55% of block RAM | Tuning must keep them. The scaling margin is about 0.08 |

## 2. Verification

As in 20.4:
- **Every new build variant is verified before it is measured.** That
  includes the 2 and 8 KiB caches, and the console if it changes. Each runs
  soc-tests' two-hart programs (litmus, the memory checker, the hart-1 reset
  stress, dispatch, the devices, the DMA, the v12 self-test), and the L1
  unit tests where the caches change (matrix.md §2).
- **The final RTL** passes all of soc.md §10's checks again:
  - the fabric's shell, if the fabric changes;
  - the DMA's shell;
  - the SoC's tests;
  - soc.md §10.4's regression: Phase 19's gate programs, and 18.7's board programs
    cycle for cycle against the CPU shell;
  - the golden replays against the last signed-off RTL, where the default
    build's behaviour should not change.

## 3. How a tuning change is judged

- **Every result is published,** the losses too (soc.md §11).
- **The original methods stay.** A tuned variant is a new case under the
  existing methods, named for its original with `__` and a tag (amended in step 1: the approved plan's example
  had one `_`). For example, `cifar_cnn_npu_direct__2h` uses method
  `npu_direct` with two workers, and v12's method list is unchanged.
  - Each variant is planned before it is captured, with matrix.md §3's
    crossing: R plus each axis alone, plus its family's named crosses
    (matrix.md §9).
  - So the manifest stays complete, and the Records gate covers the
    variants.
- **Across layouts.** A layout knob pads *between* parts of the image, not
  the whole image. It has three pad points:
  - between the runtime and the kernel's code;
  - before the kernel's data objects;
  - between the two harts' buffers.

  So it moves each part's bank phase (address bits [5:4]) and cache index
  relative to the others, as 20.4's insertion did. **The layouts are fixed:**
  - **L0:** every pad 0. This is the layout of record.
  - **L1, L2, L3:** every pad 16, 32 and 48 bytes, the other bank phases.
  - **L4:** the first two pads 1 KiB, a quarter of the caches' index.
  - **L5, L6, L7** (added in step 1, below): the bank phases of L1–L3 with
    the data shifted a further 320, 640 and 960 bytes. That is 20, 40 and
    60 cache lines, none a power of two, so no buffer size aliases it.

  **Step 1's proof** (before any tuning comparison) ran 20.4's
  layout-sensitive cases at the layouts: the 16×16×64 GEMM in every
  configuration, and the lr/sc and CAS counters.
  - **L0** equalled the final capture in all 262 windows.
    - **Before the share stamps (§6),** with every pad 0, 5,905 of the 5,920
      distinct firmware images were byte-identical to the final capture's.
      The other 15 (shared_mix) differ in two commutative `xor`s' operand
      order, and their records are the same (51 of 51).
    - **With the stamps,** only the two-worker builds that carry them
      differ: 5,808 are identical. The stamps compile in only where a
      program uses them, so every one-worker image is as before.
  - **The five approved layouts fell short:** they missed the GEMM's moves
    (20.4: +12% at +4 waits; the five: ±0.3%).
    - L1–L3's data pads change the buffers' cache index by one to three
      lines.
    - L4's 1 KiB shift equals the GEMM's buffer sizes, so it maps one
      buffer's conflicts onto another of the same shape.
    - The code pad also pushes every data section after it, since `.text`
      comes first.
  - **The eight layouts reach** at least half of 20.4's move in 261 of the
    262 windows (from the clean commit 2463544): the GEMM's moves (−10.7% at
    +4 waits) and the contention cases (−41% to +74%).
    - **The measure:** the spread over the layouts, against the size of
      20.4's move. Measured instead as the largest single move from L0, the
      result is the same.
    - **The one short:** the lr/sc counter's 2-item kernel window with the
      cache off, which 20.4 moved by 6 cycles (246 to 240). No layout moves
      it.

  **Eight layouts instead of decision 2's five: pending the owner's
  approval.** The change is made before any change is judged, and it only
  makes the rule stricter, since a change must win on every layout. Until
  the owner approves, no tuning change is judged.
  - **The code pad and the CPU kernels:** v1's CPU kernels keep their hot code
    in `.text.benchmark`, linked ahead of all `.text`. So for them, the code
    pad moves the runtime and the data, not the kernel against the runtime.
  - **The hart pad** applies only to buffers declared as hart 1's own
    (`MATRIX_ROOM_HART`, `MATRIX_AT_HART`: 20.5's placement variants). The
    programs of 20.4 split one array between the harts, so their halves move
    together.
  - **The pads survive alignment.** Most kernels' buffers are 64-byte
    aligned, which would swallow a pad placed before them. So a pad is an
    offset applied after the alignment: each buffer is declared with room
    for it, and its code uses the base plus the offset. Each layout's actual
    addresses (mod 64 and mod 4 KiB) are read from the ELF and recorded in
    the manifest.
  - **Proven first:** before the knob judges anything, it must reproduce
    20.4's moves: the 16×16×64 GEMM at +4 waits, and the lr/sc and CAS
    contention cases.
  - **Fits in memory:** the pads, and the room each buffer is declared
    with, must fit the case's free memory.
    - The large layouts need room: L5–L7 add 336 to 1,008 bytes to each
      buffer, and L4 about 2 KiB in all. GEMM 128×64×128 has about 1.5 KB
      free on one worker and 528 bytes on two, so it fits only some of
      them.
    - The runner finds the fit at build time: a layout that does not fit
      is unsupported, with that reason.
    - A comparison uses the layouts both of its sides fit. Each case's
      layouts are listed in its plan before capture.
- **The rule:** a change wins if it is faster in every window it has, on
  every layout it fits, at R, warm and cold.
  - **Faster than its original:** each at R with its own workers, in the
    same case, window and layout. A two-worker variant runs R's
    configuration on two workers; its original runs on its own.
  - **Only a win is adopted.** The cache geometry is the exception: the
    owner adopts it by sign-off (§4.2).
  - **Its other configurations** (the waits, the cache off, the NPU
    geometries) are measured at L0 and published. A loss there is
    reported beside the win.
  - **A change that wins on some layouts and loses on others** is
    published as not distinguishable, and not adopted.
  - **Layout-sensitive cases:** two-hart windows, and windows under 10,000
    cycles. Their final figures are given with their range over the
    layouts, beside L0's.
- **The same oracles, records, windows and checks as 20.4.** No gate is
  changed to fit a result.
- **The original methods' figures** will move with 20.5's runtime changes.
  How far is reported, as 20.4's step 4 did.

## 4. Tuning

### 4.1 DMA thresholds

- **A copy helper in the runtime** (`aster_copy`) picks the DMA or the CPU's
  fair copy by size, alignment and whether the destination is cached. (Step
  1's helper picks by size and offset; the destination's cache state joins
  it in step 3, if the finer sweep shows it matters at R.)
  - **Its thresholds:** from a finer sweep around 20.4's crossovers (every
    size from 32 to 160 bytes, and 8 to 63 misaligned), on each layout.
  - **Freed time:** whether the time the DMA frees is used is reported too.
    Polling is not freed time (phase17-plus.md §4, matrix.md §4.3).
- **Where it is used:**
  - ECG's chunk moves;
  - the ML programs' staging copies;
  - any copy the partitioning below adds.

  Each is a tuned variant (§3).

### 4.2 Cache geometry

- **The RTL:** the caches' line count as a parameter (2, 4 and 8 KiB,
  direct-mapped, 16-byte lines), with:
  - ABI 4's line-geometry words (`0x90`, `0x94`) following it;
  - the CPU shell's cache model following it;
  - the L1 unit tests and soc-tests at each size (§2).
- **Measured:**
  - the 468 combinations planned for 20.5 (every case and method at R,
    warm, at 2 and 8 KiB);
  - cold, for the gate workloads;
  - the layouts (§3).
- **Adoption is the owner's sign-off,** with the data in hand: per family,
  the gate workloads, and timing and area. soc.md §2's 4 KiB caches stay
  unless the owner adopts another size.
- **If another size is adopted:**
  - soc.md §10.4's regression runs on a 4 KiB build of the same RTL, as its
    comparisons are defined at 4 KiB;
  - 18.7's board programs run against the CPU shell at the adopted size,
    whose cache model follows the parameter;
  - the timing is re-checked by the owner's rule.

### 4.3 Partitioning

Which engine and which hart does which part of a workload:
- **The CPU work around the NPU, on both harts:**
  - CIFAR, Conv2D and MNIST: hart 1 does part of the staging, im2col,
    requantization and pooling;
  - work on the next image, tile or layer overlaps the NPU's current job;
  - the DMA stages where the copy policy says it pays.

  CIFAR's direct method spends about 3.93 M of its 4.09 M cycles off the
  NPU, so two harts bring it near 2 M at best.
- **Two workers only above measured sizes:** a split policy from 20.4's
  losses (the dot below K = 64, the small GEMMs, the 2-item coherence
  cases), as the runtime's default for tuned variants.
- **MNIST batching:** the batch size by throughput and latency. 20.4 gave
  8,920 cycles an image at batch 1 and 2,242 at 32 on the batched method,
  and 6,005 warm on the per-image method. v1's comparison keeps the
  per-image method.

### 4.4 Placement

Where code and data sit:
- **Data across the banks** (address bits [5:4]): per-hart buffers placed so
  that the two harts' streams meet in different banks. 20.4 measured
  same-bank streaming at +0.6% to +10.0% by phase.
- **Code against the instruction cache:** the hot kernels aligned away from
  the armed hand-over's lines (matrix.md §10.12), and from each other.
- **Measured** across layouts (§3): placement aims to make two-hart figures
  faster and less layout-dependent. The spread before and after is
  published.

## 5. ECG

- **The overlap proof** (matrix.md §4.8; soc.md §11's ECG gate):
  - **The harts' stages:** each hart stamps each stage of each chunk.
  - **The engines' intervals** (soc.md §9's ECG outputs): each DMA and NPU
    job is stamped at its submission and lasts its own JOB_CYCLES. A
    polling span is not an engine interval.
  - **The output:** the stamps go on a new `MATRIX_STAGE` line beside the
    record, in the window's time base. The runner's `MATRIX_ECG` latency
    line stays as it is.
  - **The gate:** stage overlap shown, or the pipeline labelled sequential
    per chunk.
- **The stamps' cost:** the stamps sit inside ECG's v1-retained window, and
  the two-hart pipeline leads scalar by under 1% cold. So the stamped runs
  are separate entries: the unstamped runs keep the gate's figures, and the
  stamped ones carry the proof (§10).
  - **Their names:** each stamped twin is its case's name plus `_stages`.
    These are instrumented twins, not tuned variants, which take `__`.
  - **Their pacing:** a stamped program prints up to about 20 KB of stage
    lines, faster than the board's reader drains the ring. So it pauses
    100,000 cycles (1 ms) after each line, outside the window, the same
    cycles in the simulation and on the board.
  - **The result:** all 72 stamped programs end on the board as simulated,
    their console within 5,003 bytes of the reader, under half of the
    16 KiB ring. Unpaced, they overflowed.
- **Tuning:**
  - the chunk moves by the copy policy (§4.1);
  - the stages' split between the harts balanced from the stamps;
  - v1's byte-gathering DOT8 FIR replaced, in a tuned variant.

  Samples a second, and each chunk's latency against its 360 Hz deadline,
  stay the outputs.

## 6. Overlap evidence (matrix.md §10.14)

Where hart 0's interval spans its own wait, the programs also stamp the end
of hart 0's share, on a `MATRIX_SHARE` line beside the record:
- the reductions, Conv2D, the FFT, MNIST, CIFAR, the dot, and the two-hart
  tuned variants;
- the record's interval keeps matrix.md §10.7's rule, so v12 is unchanged;
- overlap is then shown from stamps;
- matrix.md §10.14's speedup reading stays only as a cross-check.

**The stamps' cost** (measured in step 1): four stamps a window, and a check
each stretch.
- **The gate's reduction:** +47 cycles end to end and +1 in the kernel window
  at R.
- **v1's reduction:** +142 and +132.
- **The scaling gate's lowest point:** 1.890×. In 20.4 it was 1.882×, and
  layout moves it either way.

The stamps compile in only where a program uses them (`MATRIX_SHARES`).
- **Cold, they cost more:** +235 cycles on the gate's reduction end to end,
  +164 on v1's. That moves the v1 gate's reduction from 3.96× to 3.95×.
- **Against the plan's estimate:** about six times the "about 20 cycles"
  above, since a window has four stamps and a check each stretch.

The overlap script pairs each tuned variant with its original (step 1).
Overlap is read only against a one-worker original. A win over a two-worker
original is reported apart, as the tuning's own speedup.

**The two-hart NPU variants' windows.** In an NPU kernel window hart 0 only
starts and polls the job (matrix.md §10.7: its interval 0 and 0), and hart 1 has
nothing of the job's to do. So a two-hart NPU variant records the e2e window
alone, where its overlap is. This is a new reading, approved by the owner (§10).

## 7. The board run

### 7.1 The console

The board script already reads the console while a program runs, as a ring.
The RTL buffer wraps, and can be read with the harts running. A program
fails only if it gets more than 4 KiB ahead of the reader.

**The plan:** keep the 4 KiB ring if the drain keeps up.
- **The check:** step 1's smoke run measures the drain on the programs that
  print fastest and most, including 20.5's stamped ones. The lag depends on
  how fast a program prints, not only how much.
  - Every byte must match the simulation's console.
  - **"Keeps up" means** the reader's largest lag behind the writer stays at
    or under half the ring: 2 KiB of the 4 KiB ring, 8 KiB of the 16 KiB one.
    That leaves a margin for the 7,800 board runs to come.
  - **The rule holds for the whole board run:** the script records each
    program's largest lag, and fails any program over half the ring.
- **If it cannot keep up:** the console grows to 16 KiB. That is four block
  RAM tiles, and the ARM window becomes `0x30000`–`0x33FFF`, with these
  changed to match:
  - the read mux;
  - the board script's overflow limit and mask;
  - the testbench's drain, which has the same 4 KiB ring.

  It is an amendment to soc.md §7.4 and §10.6, verified (§2) and timed
  (§8).

### 7.2 The run

- **First, a smoke run of 20.4's signed-off image** (step 1). The two-hart
  SoC (SHELL_PAGE 0) has never run on the board. The smoke run measures:
  - per-program load time;
  - the console's drain;
  - the script's two-hart path.

  The script is extended for the full SoC:
  - no register page;
  - hart 0's retired count at tohost compared, and hart 1's read but not
    compared (below);
  - the matrix's programs;
  - runs longer than its present one-hour ssh limit.
- **The final image:** built by the owner's timing rule. On the board:
  - it is loaded through the FPGA manager, with FCLK0 measured at 100 MHz;
  - its configuration words (`0x3F058`, `0x3F05C`) are checked against the
    simulation's.
- **The programs:** every R program of the final capture, about 3,900. Each
  must end as its simulation did:
  - tohost at the same cycle, with hart 0's instructions retired;
  - the same console byte for byte, so every record and counter is the
    simulation's (soc.md §10.6: each program). The records carry hart 1's
    counts for each window.

  Hart 1's live retired count (`0x3F060`) is not compared: hart 1 keeps
  spinning after tohost, so the count depends on when it is read.
- **Time:** the programs run 30 s in total at 100 MHz; loading dominates.
  The total is measured in the smoke run, not assumed.
- **Two boots:** the whole set on two separate loads of the bitstream, with
  the same results. That is stricter than earlier phases' warm boots.

### 7.3 CoreMark's official score

- **The run:** in 20.5, on the board (matrix.md §9), on hart 0 of the final
  image at R. It lasts at least ten seconds, timed with the timer at the
  measured clock, under CoreMark's own run rules and CRCs.
- **Cycle for cycle:** the same program is simulated in full (about 10⁹
  cycles, roughly 35 minutes) and must match the board, as every program
  does.
- **Reported** as CoreMark and CoreMark/MHz. 20.4's CRC-run cycles remain a
  correctness check, not a score (phase17-plus.md §3).

## 8. Timing and area

The final image follows the owner's rule:
- several strategies;
- the best reproducible build signed off;
- the spread recorded.

So does every RTL change on the way: the console if it grows, and the cache
geometry if adopted. Area must stay at or under 80% of LUTs, block RAM and
DSPs. Variant builds that are not adopted stay simulation-only, with no
timing claim.

## 9. The Phase 20 report and the closeout audit

- **The report** answers the README's research questions 1–6 from the
  bundles:
  - which engine wins, for what;
  - working set, banks and latency;
  - caches and coherence;
  - the DMA's crossover and freed time;
  - NPU geometry and utilisation;
  - ECG's overlap and deadline.

  It publishes the losses, uses phase17-plus.md §3's reporting definitions
  (GOPS with a MAC as two operations, seconds at the measured clock), and
  says what is out of scope (energy, an L2).
- **The closeout audit** is a read-only script, as earlier phases' audits
  are. By phase17-plus.md's rule, it must reject:
  - negative slack, failing endpoints, unrouted nets and DRC errors;
  - resource use over 80%;
  - missing required workloads and missing raw records;
  - source drift: a bundle not bound to its commit;
  - wrong counter attribution;
  - any board program that does not match its simulation.

  It also:
  - re-derives every gate figure from the raw records;
  - has checks that can fail, each shown failing on a planted fault;
  - says PASS only if every gate passed, and otherwise "incomplete".

## 10. Decisions, approved by the owner (10 October 2026)

Each was approved as recommended, the first option of each.

1. **The console (§7.1):**
   - keep the 4 KiB ring if the drain keeps up, else 16 KiB
     (recommended);
   - 16 KiB from the start;
   - 8 KiB, which 20.5's stamps may outgrow.
2. **The layout knob (§3):**
   - padding between parts, proven against 20.4's moves first, with the
     knob's zero as the layout of record (recommended);
   - no layout judging, with figures as one layout's.
3. **Tuned variants as new cases under the existing methods,** planned
   with §3's crossing and the originals kept (recommended); or tuning that
   replaces the methods.
4. **The cache geometry (§4.2):** adoption as your sign-off with the data
   in hand (recommended); or 4 KiB kept whatever the data.
5. **Overlap stamps (§6):**
   - stamps beside the record, with matrix.md §10.7 unchanged (recommended);
   - hart 0's record interval ending at its share, which changes matrix.md §10.7.
6. **ECG's stamps (§5):**
   - separate stamped entries, so the gate's figures carry no stamps
     (recommended);
   - stamps in the gate's runs, counted against v2.
7. **The board's program set (§7.2):**
   - every R program on two separate loads of the bitstream
     (recommended);
   - the gate workloads and a sample of each family. That departs from
     soc.md §10.6's "each program".
8. **The two-hart NPU variants' windows (§6):** the e2e window alone
   (recommended); or a kernel window redefined for them.
9. **Phase 21's scope.** 20.5's board run covers most of Phase 21's exit
   (phase17-plus.md: the all-engine image at 100 MHz with no failing
   endpoints and clean routing and methodology checks; the acceptance
   subset, CoreMark included, on repeated boots).
   - **Recommended:** keep Phase 21 as a short phase. Its acceptance subset
     is 20.5's board set, and it adds the formal routing and methodology
     checks on the final image.
   - **Alternatively,** fold it into 20.5. That amends soc.md §10.6 and the
     roadmap.

## 11. The steps of 20.5

Each step has its watchdog review before it is pushed.

1. **Infrastructure:**
   - the layout knob, proven against 20.4's moves;
   - the copy helper;
   - the share and stage stamps;
   - the overlap script's pairing of variants with their originals;
   - the board script for the full SoC;
   - a board smoke run of 20.4's signed-off image, which measures load time
     and the console's drain, and decides §7.1;
   - if the drain does not keep up, the 16 KiB console: RTL, verified and
     timed.
2. **Cache geometry:**
   - the parameter;
   - its tests and soc-tests at each size;
   - the 468 combinations captured.

   Then the owner's decision (§4.2).
3. **Tuning:** DMA thresholds, partitioning, placement and ECG.
   - The tuned variants are planned, added and judged across layouts.
   - The adopted set is fixed.
4. **The final configuration:**
   - soc.md §10's checks on the final RTL (§2);
   - the full matrix captured from one clean commit, as 20.4's step 4, with
     every gate now required;
   - the bundle;
   - the timing by the owner's rule.
5. **The board run:** every R program on two loads, cycle for cycle; and
   CoreMark's official score.
6. **The Phase 20 report and the closeout audit.**

## 12. Risks

- **Layout noise can swamp a tuning win.** That is why §3 judges across
  layouts, and some changes may be published as not distinguishable.
- **An 8 KiB cache may cost timing.** The tags and data arrays grow, and
  the margin today is +0.333 ns on one placement.
- **The board:**
  - the two-hart SoC has not run on it yet;
  - about 3,900 programs, twice, with loading time unmeasured until the
    smoke run;
  - the drain is Python polling over `/dev/mem`.
- **Partitioning ML work across harts** adds coherence traffic and
  hand-overs. 20.4's small-work losses bound where it pays.
- **The scaling gate's margin is about 0.08** at its tightest point (1.882×
  against 1.8×). A runtime change that slows the hand-over could eat it, so
  every change is checked against the gate.
