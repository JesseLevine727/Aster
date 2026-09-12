# Phase 4 cache closeout evidence

Captured on 2026-09-12 from clean commit
`966ddea297f97b867a5a97e72497423826269152`. All 61 captures have `dirty: false`
and source fingerprint
`28fedef1e2173a8172601d4523c83677715ce8d823b0e1f34bdbd19c10bd6ce0`.
Compiler: RISC-V GCC 16.1.0 (`g6afcc4f6d`), RV32IM/ILP32, `-O2`;
simulator: Verilator 5.020. Each JSON preserves the full command, compiler
binary/version/flags, per-source hashes, firmware/ELF/model hashes and raw
newline-terminated AsterBench v2 record. These are real RTL simulations,
not estimated performance or physical-board measurements.

## Reproduce and audit

From the repository root, with that source revision checked out and its
toolchain available:

```sh
make check
make cache-matrix cache-boundaries
make phase4-soc-matrix
python3 scripts/cache_experiments.py --output-dir build/results/new-cache-study
python3 scripts/cache_experiments.py --output-dir docs/results/phase4 --audit-only
```

The [manifest](manifest.json) identifies all 60 distinct configurations and
their overlapping experiment groups, plus the independent repeat. The
[summary CSV](summary.csv) contains the audited typed records. The auditor
checks complete coverage, executing settings, independent checksums,
consistent source/toolchain provenance, matched retirement counts and an
identical repeated record/firmware hash. The temporary build directory from
the recorded command need not exist: choose a new build/output directory.
Local build logs remain in `build/results/phase4-clean-966ddea`; compact
captures and regression output are retained here, not generated binaries.

All studies use eight measured repetitions and nominal 31.25 MHz. Default
seed is `0x13570000`. Default private I$/D$ capacity is 256 bytes **each**,
with four words per line. `memory_wait` is the total additional backing-memory
wait cycles, not a delay on top of synchronous BRAM timing.

The pointer walks use identical measured instructions and PCs for matched
sizes, and the ring starts at `0x10000000`. Both initialize outside the window,
warm up with one complete traversal, then measure dependent reads. The host
checks exact kernel addresses/opcodes across the sweep and 2/4096-word
boundaries. Firmware checks the accumulated sum, final index and complete
single-cycle graph; the host independently checks the expected checksum.

## 1. Cache versus no cache and memory latency

Speedup is uncached cycles divided by cached cycles; **below 1 is a slowdown**.
Memcpy uses a 256-byte buffer; walks use a 512-byte ring. Rows at each latency
use the same firmware work, checksum, retirement count and native transactions.

| Workload | Backing timing | Uncached cycles | Cached cycles | Speedup |
| --- | --- | ---: | ---: | ---: |
| memcpy | async / 0 waits | 13,910 | 14,518 | 0.958× |
| memcpy | sync / 1 wait | 18,034 | 15,106 | 1.194× |
| memcpy | sync / 4 waits | 30,918 | 16,870 | 1.833× |
| sequential walk | async / 0 waits | 25,648 | 26,936 | 0.952× |
| sequential walk | sync / 1 wait | 32,831 | 27,966 | 1.174× |
| sequential walk | sync / 4 waits | 55,404 | 31,056 | 1.784× |
| random walk | async / 0 waits | 25,648 | 29,016 | 0.884× |
| random walk | sync / 1 wait | 32,831 | 31,710 | 1.035× |
| random walk | sync / 4 waits | 55,404 | 39,792 | 1.392× |

Memcpy retires 3,101 instructions and makes 4,636 native transactions in every
row; each walk retires 6,158 and makes 8,207 native transactions. Caches reduce
backing transactions from 4,636 to 588 for memcpy, 8,207 to 1,030 for the
sequential walk and 8,207 to 2,694 for the random walk. Traffic reductions
alone do not guarantee speedup: the blocking refill/write-through controller
adds overhead, and immediate backing reads leave little latency to hide.

## 2–3. Working-set sweep and access pattern

Synchronous memory, one wait cycle, 256-byte I$ and 256-byte D$. Both uncached
patterns produce the same cycles for a matched ring size.

| Ring bytes | Uncached cycles, either pattern | Cached sequential cycles | Cached random cycles |
| ---: | ---: | ---: | ---: |
| 64 | 4,159 | 3,262 | 3,262 |
| 128 | 8,255 | 6,462 | 6,462 |
| 256 | 16,447 | 12,862 | 12,862 |
| 512 | 32,831 | 27,966 | 31,710 |
| 1,024 | 65,603 | 55,873 | 65,521 |
| 2,048 | 131,135 | 111,678 | 134,430 |
| 4,096 | 262,207 | 223,294 | 274,486 |
| 8,192 | 524,351 | 446,526 | 553,158 |

The warmed ring fits through 256 bytes; these runs have only six measured
backing transactions including the non-ring boundary/result work. Above that,
sequential traversal benefits from adjacent words in each 16-byte refill.
Random traversal loses that locality: at 8 KiB it makes 63,782 backing
transactions versus 16,390 sequentially, and is 5.49% slower than uncached
despite reducing backing traffic by 51.34%.

Counters combine I$ and D$, not just ring loads. Write-through result stores,
marker/pipeline boundaries and residual warm-up state are included. For
64/128-byte rings the combined miss count differs by two between patterns,
while cycles and backing traffic are identical. Do not interpret the combined
miss count as a pure ring-load miss rate. Warm-up is specified, not a promise
that every unrelated cache line starts in an identical state.

Additional random seeds 0, 1 and `0xa57e` were tested at 512 and 4,096 bytes.
At 4,096 bytes their cached cycle counts are 275,854, 275,494 and 275,062;
all remain slower than the matched 262,207-cycle uncached run. These samples
check seed sensitivity; they are not a statistical characterization of all
access distributions.

## 4. Cache-size sensitivity

512-byte ring, synchronous one-wait backing memory, 16-byte lines. The sweep
changes **both** private cache sizes, so it is not an isolated D$-only study.
The uncached reference is 32,831 cycles for either pattern.

| Capacity per private cache | Lines | Sequential cycles | Random cycles |
| ---: | ---: | ---: | ---: |
| 64 bytes | 4 | 28,002 | 34,194 |
| 128 bytes | 8 | 27,966 | 33,366 |
| 256 bytes | 16 | 27,966 | 31,710 |
| 512 bytes | 32 | 25,662 | 25,662 |
| 1,024 bytes | 64 | 25,662 | 25,662 |

Once the warmed ring fits at 512 bytes, increasing capacity to 1,024 bytes
does not improve measured cycles for this workload. This is a workload-specific
plateau, not evidence that 512 bytes is universally optimal. Cache data
capacity excludes tags/valid bits; no area or frequency extrapolation is made
for the sweep geometries.

## Verification and FPGA build evidence

[Regression output](regression.txt) records:

- Fresh-directory `make check`, including 15 host tests, ISA/runtime/map/trap
  tests, counter/retirement units, cache/UART units and both serialized UART
  simulation paths.
- 36 cache geometries × three seeds = 108 scoreboard runs: 549,936 completed
  requests and 540 deliberately aborted requests, with all byte masks, exact
  lower beats, random stalls, side-effecting bypass and reset invariants.
- 24 SoC configurations across cache enable, geometry, async/sync reads and
  zero/one/four wait cycles, each with the full Phase 1 suite and AsterBench.
- UART FIFO depths 1/3/64; the random-walk record through both standalone and
  Linux serial paths over two boots; the maximum 1,024-wait setting with a
  small random ring.

Both default-geometry Vivado 2025.1 builds completed routing and bit generation
from the implementation in the source checkpoint. Full report text is retained
under [standalone](fpga/standalone/timing_summary.rpt) and
[Linux](fpga/linux/timing_summary.rpt); trailing whitespace is normalized.

| Build | Setup slack | Hold slack | LUTs | Registers | BRAM tiles | Routing errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Standalone | 15.348 ns | 0.064 ns | 5,573 | 5,875 | 32 | 0 |
| Linux overlay | 13.446 ns | 0.034 ns | 6,197 | 6,608 | 32 | 0 |

Neither build has unconstrained internal endpoints. The standalone reset
input and five UART/LED outputs are explicitly false-pathed; the Linux shell
has five false-pathed UART/LED outputs. The Linux build reports zero DRC and
methodology findings. The standalone build is **not warning-free**:
REQP-1839 flags asynchronous reset propagation to BRAM controls (reported at
the tool's 20-instance limit), ZPS7-1 notes the PL-only design, and methodology
reports LUT-driven async reset, RAM output-register/byte-enable optimization
and generated-clock source warnings. Static timing and serial simulation do
not prove analog reset/BRAM safety; these remain relevant caveats for standalone
hardware use. The selected physical workflow is the Linux overlay, not this
standalone shell. No warnings were suppressed to manufacture a clean report.

Generated artifact SHA-256 values (binaries intentionally untracked):

```text
92170ac720fb28afe11742cf7d10da04e24fd9eec1dd0244198e606bbf3851eb  standalone .bit
4d0117d2b5a4d012fee8357fffd6f5ae9aafe193f2fef791e10e2e84561ef4e8  Linux .bit
2a5f7057ef0428a75c9ad042245be1de7d8d6f438fc6ab5b36cc46c677751d48  Linux .hwh
1e8496321af24b609f850fd7187dc9fda8e283e968d06c72c47888e676cd8f09  standalone hello.hex
```

The hashes identify these artifacts, not a claim that Vivado bitstreams are
byte-reproducible across builds. Phase 4's simulation/experiment exit is met.
At this capture checkpoint Phase 2 was still open. Subsequent recovery found
an auxiliary-reset polarity defect in the Linux shell: clean timing/DRC did
not prove that its AXI bus could leave reset. Do not deploy the old Linux
bit/HWH pair above. The corrected, physically tested replacement is retained
in the [Phase 2 closeout](../phase2/README.md). Its change is in the FPGA shell,
not the CPU/cache/firmware RTL underlying these experiments. Phases 1–4 are
now closed; no Phase 5 work was started.
