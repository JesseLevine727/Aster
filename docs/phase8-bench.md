# AsterBench v6: scalar versus packed INT8 computation

Status: the complete clean 174-capture simulation study and matching physical
study pass. Results below are labeled by their evidence boundary. The
predeclared [Phase 8 study](phase8.md#asterbench-v6-fixed-paired-experiment)
remains 174 captures per platform, not the smaller development test set.

## Execution and fairness

`software/benchmarks/dot8.c` calls the same six real compiled functions tested
by the functional runtime. A single ELF contains scalar and custom methods.
Each paired job uses the same seed and fixed A/B/Y addresses, and reinitializes
all input/output/guard bytes before each method. Four jobs alternate
scalar/custom and custom/scalar order. Two warm boots exercise retained RAM.
The secondary remains reset and every DMA counter must remain zero.

Both kernels use the existing GCC `-O2`, RV32IMA/ilp32, no LTO and identical
`noipa` function boundaries. Scalar data are ordinary nonvolatile C objects;
the scalar loop compiles to signed byte loads and ordinary multiply/add.
Aligned custom input groups use safe four-byte copies after an alignment test;
unaligned groups use byte loads and unsigned shifts. FIR uses eight outputs;
GEMM is row-major 3xK by Kx5 with B gathering inside the custom kernel. K=0
writes every output zero, and tails use scalar multiplication without overread.
Output accumulation is explicitly modulo 2^32, not signed-overflow C.

The shared counter window is `dispatch_load_pack_compute_store`: START,
completion fences, dispatch, loads, packing/gathering, loops, arithmetic and
output stores through the final fence. START/FREEZE command edges are excluded
by the hardware bank contract. Preparation, correctness checks, UART and global
STOP/flush are outside the window. This is prepared cache state, not cold cache
or isolated-instruction latency. Keep scalar/custom cycles for each matched
shape/alignment/cache/order, including ratios below one. Never infer 4x, a
cross-workload average, higher Fmax or Phase 9 NPU performance.

## Layout and records

The dedicated linker fixes A at `0x10001000`, B at `0x10003000`, Y at
`0x10006000` and private result records at `0x10008000`. A/B start offsets are
64/64 or 65/66. A used bytes are K, K+7, or 3K; B used bytes are K, K, or 5K.
Each input allocation is `(used+194)&~63` bytes. The output allocation is
48 words (192 bytes), with useful outputs starting at word 16. Every unused
word/byte remains a checked guard. No host prepares RISC-V data RAM.

Input byte i in bank b (A=0, B=1) is
`((seed >> ((i%4)*8)) ^ (i*73) ^ (i/8) ^ (b*0x5b)) & 255`.
Output guard word i is `0x6d5a0000 ^ (i*0x01010101) ^ seed` modulo 2^32.
The scalar reference interprets input bytes as signed two's-complement INT8,
uses a widened mathematical sum and compares all 32-bit output bit patterns.

Each newline-terminated `ASTERBENCH,version=6,...` row includes exact workload,
dimensions, addresses, allocation sizes, seed, order, method, window, policy,
cache/memory settings, ABI identities, errors and a 384-digit lowercase hex
dump of the entire little-endian Y allocation. The 50 64-bit counters are the
existing 28 CPU counters, 14 DMA counters, then four dot8 counters per hart:
accept, wait, complete and retired. Whole kernel windows require custom totals
equal to `floor(K/4)*outputs` and wait equal to twice that count; scalar and
secondary custom totals are zero. General cut windows need not have these
equalities; the rule is specific to complete benchmark kernels.

Each private RAM method record is 132 words. Its first 32 words are:

| Words | Fields, in order |
| --- | --- |
| 0..7 | job, method (0 scalar/1 custom), pass, seed, kind (dot/FIR/GEMM=0/1/2), K, A offset, B offset |
| 8..15 | A address, B address, Y address, A allocation bytes, B allocation bytes, Y allocation bytes, outputs, harts |
| 16..23 | DMA-bank flags, clock Hz, line words, line count, memory wait, total errors, A errors, B errors |
| 24..31 | Y errors, instruction ABI 1, counter ABI 6, CPU ABI 4, DMA ABI 1, DMA counter ABI 5, mathematical groups, reserved zero |
| 32..131 | 50 low/high 64-bit counter pairs: hart 0 CPU, hart 1 CPU, DMA, hart 0 dot8, hart 1 dot8 |

The record parsers do not silently tolerate unknown fields, duplicate keys,
partial streams, reordered pairs, mixed configurations, noncanonical/costly
numbers, nonfinite JSON values or missing guards. v2/v3/v4/v5 interfaces remain
unchanged. Record validation alone is not source or physical provenance.

## Independent observations and build hooks

`make dot8-firmware` produces actual ELF, disassembly, map and full ROM.
`make dot8-bench` runs the real coherent two-hart-capable SoC, with configurable
`DOT8_WORKLOAD`, `DOT8_K`, `DOT8_ALIGNMENT`, `DOT8_JOBS`, `DOT8_SEED`,
`DOT8_BOOTS` and `DOT8_UART_SEED`. Cache/timing/hart/geometry options retain the
existing Make names. Use a fresh build directory or new `DOT8_RAM_PREFIX`:
RAM captures are exclusive and failed/partial files must not be overwritten.

The preflight ELF reader checks loaded bytes, executable function ranges,
fixed NOLOAD buffers/results, scalar/custom instruction encodings and normal
multiply/tail code without running a command from an evidence manifest.
It checks the actual ROM against ELF load segments before starting Verilator.

The C++ scoreboard reconstructs all RAM from architectural stores, checks every
output store and full A/B/Y guards for every method, counts all actual window
events, and observes real RVFI kernel PCs and custom instruction retirement.
It compares the complete UART row and 132-word RAM record with independent
expectations. `DOT8_OBS` rows retain all 50 counts, output-store totals,
kernel retirement counts/first/last edges, executed PC sets and custom PC sets.
Every acknowledged STOP compares all 64 KiB against the architectural-store
oracle and writes actual RAM bytes exclusively; `ASTERSTOP` binds the boot,
record count, stores and lifetime retirement. The Python oracle independently
rechecks row semantics, ordered observations, actual ELF instruction PCs and
complete saved buffers/results. `scripts/dot8_results.py` additionally binds
the complete clean Git source inventory, compiler/backend/binutils/Verilator/
host-C++/Make identities and system headers, exact shared compiler flags,
fixed build and execution arguments, separate build/simulator logs, ELF/ROM,
map/disassembly, all observations and both complete stopped-RAM snapshots.
Saved commands are never executed by an audit; kernel addresses and instruction
words are independently checked against the actual ELF and disassembly.
Dirty captures are explicitly development-only and rejected by default.

`scripts/dot8_study.py` freezes all 174 cases, rejects subsets and mixed tools/
sources, and gives each of the six repeats a separately fresh build root.
It retains every paired scalar/custom cycle ratio and all 50 counters, reports
first-any-win separately from first-all-win, and retains ties and later
reversals. Crossover is sampled K, never interpolated or a universal threshold.
Use `python3 scripts/dot8_study.py plan` to inspect the exact plan, `capture
--output /new/directory` to run it from clean source, and `audit /path/study.json`
for a read-only full check. Per-capture `dot8_results.py capture`/`audit` supports
development cases without labeling them a completed study.

Development verification includes zero K for all three workloads, scalar
tails, aligned/unaligned inputs, maximum dot K=4096 and maximum FIR/GEMM K=64
with cache off/on. Seventeen directed captures (272 method rows) plus the
initial dot64 capture pass the independent C++ and Python/ELF/RAM checks.
Synthetic parser fixtures are explicitly test data, never measured results.
Mutation tests cover every field/type, output digit, saved result word and
input/output byte, actual ELF/ROM/profile/opcode corruption and PC evidence.
Two early firmware compiler-warning failures (misleading indentation and
unsigned K<0 in the zero-K reference loop) were retained and fixed with warnings
still fatal. Two capture-test failures are also retained: the initial synthetic
one-group PC fixture incorrectly selected both aligned and unaligned instruction
sites; correcting the fixture preserved the exactly-once gate. The command
mutation test then exposed an unbound compiler-prefix setting; the validator now
requires the standard compiler command or its fingerprinted absolute path.
The completed clean simulation study is distinct from those development runs;
the complete regression and physical board gates are recorded in the accepted
Phase 8 bundle.

## Complete clean simulation study

Source `6a0449c035570a9f9d24538140bdb08a039f8651` passes all 174 captures,
348 warm boots, 1,392 paired jobs and 2,784 method records. All six independently
rebuilt repeats have identical records, observations, stops, ROM and RAM. Every
kernel's full output, guards, actual retired instruction sites and 50-counter
window pass independent checks. The physical study uses the same ELF/ROMs and
reproduces every recorded counter; it is independently audited and is not
inferred from simulation.

The following are scalar cycles divided by custom cycles at the largest sampled
K. Ratios below one would indicate a custom slowdown. These are separate
workloads/configurations, not an average speedup or universal crossover.

| Kernel | Input-base alignment | Cache | First all-pairs custom win K | Largest K | Scalar/custom at largest K |
| --- | --- | --- | ---: | ---: | ---: |
| Dot | aligned | off | 4 | 4096 | 3.456655× |
| Dot | aligned | on | 4 | 4096 | 4.945886× |
| Dot | unaligned | off | 4 | 4096 | 1.325939× |
| Dot | unaligned | on | 15 | 4096 | 1.744679× |
| FIR, eight outputs | aligned | off | 4 | 64 | 1.981878× |
| FIR, eight outputs | aligned | on | 4 | 64 | 2.587211× |
| FIR, eight outputs | unaligned | off | 4 | 64 | 1.408323× |
| FIR, eight outputs | unaligned | on | 4 | 64 | 1.761938× |
| GEMM, 3×5 outputs | aligned | off | 4 | 64 | 2.017784× |
| GEMM, 3×5 outputs | aligned | on | 4 | 64 | 2.347995× |
| GEMM, 3×5 outputs | unaligned | off | 4 | 64 | 1.375439× |
| GEMM, 3×5 outputs | unaligned | on | 4 | 64 | 1.565204× |

No later sampled reversal occurs after those first all-pairs wins in this fixed
study; the audit still checks and retains reversals rather than assuming that
property. “Aligned” describes the initial A/B pointers, not every FIR sliding
window or GEMM gathered element. K is inner-product length, not a DMA byte size.

Two actual cache-on board captures already reproduce these simulation records:
aligned dot K=4096 uses **393,831 scalar versus 79,628 custom cycles**; unaligned
dot K=7 uses **1,116 versus 1,389 cycles**, a genuine **0.803456× slowdown**.
The first case retires 28,696 versus 12,319 instructions and performs 8,197
versus 2,053 data-cache accesses; the custom path retires 1,024 DOT8 instructions.
Four lanes therefore neither guarantee nor cap a fourfold whole-kernel ratio:
packing into aligned word loads, fewer loop instructions and replacing the
pinned core's iterative scalar MUL all matter. This is not an instruction-only
throughput measurement.

Zero-length cases still store all zero outputs and exercise distinct C control
paths, with no DOT8 activity. For example, aligned cache-off dot K=0 measures
279 scalar versus 319 custom cycles (0.874608×). FIR/GEMM zero-K and tiny-tail
slowdowns are retained too. The complete physical study and independent final
audit retain the full board evidence, including every cache/alignment/K case
and six fresh-build repeats.

### Routed cost at the unchanged clock

The study images retain 31.25 MHz, 32 BRAM tiles and zero DSP blocks. Relative
to the accepted Phase 7 DMA images, cache off adds 1,114 LUTs and 663 registers;
cache on adds 1,165 LUTs and 757 registers. This includes the custom unit,
eight event counters and shell/lifecycle integration, not isolated multiplier
area. Total LUT/register counts are 17,221/12,782 and 20,727/17,271 respectively.
Setup slack is 4.544/4.319 ns; hold is 0.041 ns and pulse width 14.750 ns for
both. Routed/reset/HWH gates pass. This is not an Fmax search or a Phase 9 NPU
measurement.

## Physical capture boundary

`dot8_bridge.py` accepts only the explicit Phase 8 bridge, instruction and
counter identities. Its inherited writes are limited to RUN/STOP and stopped
ROM loading; no ARM DMA descriptor, custom-compute or data-RAM write API exists.
It reads idle/frozen DMA and dot8 diagnostics and requires all state to reset
after acknowledged STOP. Older host helpers still reject the Phase 8 identity.

`run_pynq_dot8.py` validates the reference ELF/ROM/captures and routed overlay
before importing PYNQ. It then checks the exact currently loaded path/hash,
known HWH/map, board identity, clock and idle state, and repeats these guards
before explicit PCAP download. Each real boot retains UART, full stopped RAM,
frozen device/compute diagnostics, all 50 measured counters and exact reference
deltas. Failures preserve partial files and drain/stop a successfully identified
new bridge; an unknown identity is never written. Offline audits do not execute
saved commands, and host audits additionally check collector/source Git blobs.

`pynq_dot8_study.py` requires all 174 independently audited reference captures
and both compatible images before any board write. It executes 87 captures in
each cache mode, including the three separately rebuilt repeats, recording the
exact prior/loaded bitstream hash chain. Warm boots do not reprogram the PL.
This collector and its mutation/isolated-import tests are implemented and the
physical study is complete. A first physical-test fixture accidentally escaped
its synthetic ROM newlines; that failure was retained and the fixture corrected
without altering the canonical-ROM or board-preflight gates.
