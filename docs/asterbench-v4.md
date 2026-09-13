# AsterBench v4: coherent/atomic jobs

Implementation checkpoint, **not yet final Phase 6 physical acceptance**.
The actual Phase 6 SoC runs `software/benchmarks/coherent.c`, compiled with
`-march=rv32ima -mabi=ilp32`, on protected independent stacks. No benchmark
implementation changes the pinned PicoRV32 source or legacy v2/v3 records.

## Work and measurement boundaries

Each firmware selects one workload; each warm boot runs 1–16 jobs, default 3.
`items` is 2–1024, `rounds` 1–64, `workers` 1 or 2. Job seed is base seed XOR
`job * 0x9e3779b9`, with 32-bit arithmetic. Only `shared_mix` uses `rounds`;
the other workloads retain this configuration field but do not multiply their
operation count by it. Generated inputs and wraparound are deterministic.

| Workload | Job work and independently checked result |
| --- | --- |
| `atomic_add` | N AMO increments; final N and sum of returned tickets N(N−1)/2 |
| `lrsc_counter` | N increments using a four-instruction constrained LR/SC retry loop; same ticket oracle |
| `cas_counter` | N C11 weak compare/exchange increments, including real retry paths; same oracle |
| `lock_sum` | Acquire/release spinlock protects ordinary RAM count and sum of indices 1 through N |
| `false_shared` | Adjacent per-worker atomic words; independent counts and old-value sums |
| `padded` | Equivalent per-worker words separated by 4096 bytes |
| `ping_pong` | N request/reply handoffs, ordinary payload/complement protected by release/acquire turn |
| `spsc_queue` | N ordered payload/complement pairs through an 8-slot wrapping producer/consumer queue |
| `shared_mix` | Partition N ordinary shared-RAM words; repeatedly update each with the integer mixing function |

For counter/lock/mix jobs, one worker does all N items; two workers split them
ceil(N/2), floor(N/2). For communication jobs, N still means N handoffs: with
two workers each executes one endpoint N times; a single worker executes both
endpoints locally. Do not equate a queue handoff with a counter increment or
claim a two-endpoint checksum should equal the single-endpoint checksum.

Primary initializes shared state before START. In two-worker jobs it releases
the secondary **inside** the common window, then executes its own kernel and
acquire-waits for completion before FREEZE. Thus `window=dispatch_work_join`
includes secondary startup, release, dispatch, kernel, synchronization, completion
and marker/pipeline overhead. It excludes initialization, snapshot reads,
secondary stop/flush, reference validation, UART formatting and global stop.
The secondary is safely stopped before UART and restarted for every job, so
it cannot poll during serial backpressure and change the next job's phase.
Primary remains running throughout; its cache state is not artificially cleared.
Secondary I$/D$ are cold each job; primary caches reflect initialization and
prior execution. This is **not** a universally cold-cache or kernel-only study.

Two private writeback D$ banks are real, but their controller serializes hits
as well as misses. Expect contention/startup overhead and report slowdowns.
Only 32-bit C atomics are claimed lock-free; no libatomic fallback is linked.
Cache-off uses the same atomic serialization and reservation rules.

Sharing records include actual `counter0_addr`/`counter1_addr`. Padding is
4096 bytes, enough to separate every supported line up to 1024 words. Adjacent
words share a line only when line size exceeds one word. The standalone D$
controller supports one-word/one-line tests; the integrated enabled ROM I$
requires at least two words and two lines. The v4 SoC configuration validator
enforces that distinction. Extreme simulation geometries are not FPGA capacity
or timing claims; the physical checkpoint is four words × sixteen lines.

## Strict serial record

Exactly one newline-terminated `ASTERBENCH,` record per job, no omitted or
duplicate keys, optional counters, trailing bytes or abbreviated PASS lines.
Unknown fields and wrong version/window/status fail. Numeric fields are unsigned
canonical decimal or lowercase `0x` hexadecimal, bounded to 32 bits except the
64-bit counters. Firmware emits fixed-width hexadecimal results/counters.

Required non-counter fields are:

```
version=4 name window=dispatch_work_join status=PASS
items rounds jobs job base_seed seed harts workers h0_units h1_units
result0 result1 checksum errors clock_hz l1 sync_memory
line_words line_count memory_wait counter0_addr counter1_addr
```

All fourteen counters appear for **both physical banks**, prefixed `h0_` and
`h1_`: `cycles`, `retired`, `memory`, `i_access`, `i_miss`, `d_access`, `d_miss`,
`backing`, `atomic`, `sc_success`, `sc_failure`, `intervention`, `invalidation`,
`writeback`. See the [ABI 4 event contract](phase6.md#phase-6-performance-register-contract-abi-4).
Inactive/absent workers have zero measured events except common elapsed cycles.
Counter consistency is checked without substituting a sum for actual per-hart
observations. Python and C++ have independent arithmetic reference functions.

`aster_coherent_results` retains eight words per job in hart 0 private RAM:
job, seed, result0, result1, checksum, errors, h0_units, h1_units.
`aster_coherent_output` retains the complete final output array, including all
mix words (not only its checksum) or the lock's ordinary-memory sum. Host
addresses come from checked ELF symbols, not a guessed compiler layout.

## Verification and evidence

The direct SoC harness observes each event at its actual edge and independently
accumulates all 28 registers' expected values between START and FREEZE. It also
observes actual kernel-PC retirements independently on both cores. UART counters
must match this scoreboard exactly. Every record is checked against independent
results and the architectural-store history, including the complete output array.
Every warm stop compares all 64 KiB of physical RAM to that history and writes
a complete little-endian snapshot. No destructive reset is used after first POR.

```
make coherent-bench
make coherent-bench COHERENT_WORKLOAD=spsc_queue COHERENT_ITEMS=129 SYNC_MEMORY=1
make coherent-bench-matrix
make coherent-bench-boundaries
make coherent-bench-sizes
python3 scripts/coherent_results.py capture --workload shared_mix --workers 1 --output build/v4/one.json
python3 scripts/coherent_results.py capture --workload shared_mix --workers 2 --output build/v4/two.json
python3 scripts/coherent_results.py audit build/v4/two.json
python3 scripts/coherent_results.py compare build/v4/one.json build/v4/two.json
```

Capture defaults to clean source and fresh isolated build output, preserving
raw stdout, full UART, independent per-hart observations, safe-stop/lifetime
records, every RAM snapshot, firmware, ELF, map and disassembly. SHA-256 binds
every artifact; the auditor checks actual ELF symbols and derives boot bytes
from ELF load segments independently of `nm` and the generic converter.
It never executes a path supplied by an untrusted saved capture. The complete
relevant Git revision tree must match the source manifest: a self-consistent
hash of a selectively omitted source subset is insufficient. Compiler driver,
cc1, assembler, linker, collect2, nm, objdump, Verilator, their versions/binaries,
used absolute compiler headers, compiler/linker flags, simulator hash and build
command are retained. Source/toolchain changes during capture cause failure.
`--allow-dirty` is explicitly development-only; such captures cannot pass the
default clean audit. Existing artifacts are not overwritten.

Comparison requires audited packages with the same source/toolchain/workload,
clock metadata and topology; only worker count and cache enable may change.
Ratios below one are slowdowns. Raw per-job/per-boot values remain available;
warm boots are not silently collapsed or required to be identical. Physical
serial/PCAP integration and the final versioned study/audit are separate gates.

## Fixed simulation study

`scripts/coherent_study.py` captures and audits the versioned
`phase6-default-size-communication-repeat-v1` plan. Its 57 captures retain
114 warm boots and 342 measured jobs, always two physical harts, synchronous
one-cycle backing RAM, four-word/sixteen-line geometry and no extra UART stalls:

- All nine workloads at 64 items/four rounds, crossed with one/two workers and
  cache-off/on (36 captures).
- `shared_mix` at 2/1, 129/16 and 1024/64 items/rounds with the same cross
  (12 captures). Seeds are respectively 0, 1 and `0xc0ffee`.
- Ping-pong and the wrapping queue at 1024 items, seed `0xffffffff`, with the
  same cross (eight captures).
- An independently rebuilt repeat of the default two-worker cached mix.

```
python3 scripts/coherent_study.py plan
python3 scripts/coherent_study.py capture --output build/v4-study
python3 scripts/coherent_study.py audit build/v4-study/study.json
```

The output directory must not exist. The first 56 captures share one freshly
created isolated build tree; configuration-specific firmware/model paths avoid
cross-configuration reuse. The repeat uses a second fresh tree. Each capture
checks its complete clean source and toolchain before/after building; the batch
rejects changes between captures. Failures preserve raw logs and a failed
manifest, which cannot pass the complete-study audit.

The read-only auditor requires the exact ordered plan and every full capture
package, revalidates actual ELF/ROM, serial observations and RAM, rejects extra
files, and recalculates the whole summary. The fresh repeat must have identical
ROM, records, per-hart observations, stops, symbols and full stopped RAM. Debug
paths/map/disassembly files are retained independently; their hashes are not
required to match across fresh build directories.

Results retain all 28 summed counters plus per-boot/job cycles. There are 28
worker-scaling, 28 cache-effect, four adjacent-versus-padded and one fresh-repeat
comparisons. Ratios are baseline cycles divided by candidate cycles, so values
below one remain slowdowns. Cycles per item include all configured mix rounds;
communication items mean complete handoffs. The study does not combine distinct
workloads into an overall speedup or turn this simulation study into a physical
timing claim. Small jobs expose dispatch/synchronization overhead rather than
subtracting it; exact cache-state/window caveats above apply to every result.

The complete study captured from clean `26e18cb` passes its independent audit
under `build/phase6-26e18cb/study/`. At the default 64-item cache-enabled size,
one-/two-worker summed-cycle ratios are 0.795385 (atomic add), 0.745677 (LR/SC),
0.825417 (CAS), 0.428351 (lock), 0.819575 (adjacent words), 0.948967 (padded),
1.317701 (ping-pong), 1.228518 (queue) and 1.877091 (mix). These are **simulation
measurements**, including dispatch/work/join; the small contended updates do
not scale. All underlying per-job counters and the independent fresh-repeat
proof are retained, not replaced by these rounded ratios.

## Physical collector and reference gate

`scripts/run_pynq_coherent.py` consumes a full audited v4 reference and a
`coherent_overlay.py` package. The board-side preflight checks their complete
artifacts, clean-source claims, actual ELF/ROM, routed/reset/HWH gates and
identical RTL/vendor/FPGA/build/reset input fingerprints before importing PYNQ.
The final host auditor additionally resolves source and collector files against
their actual Git revisions; the board itself need not contain a Git repository.
Firmware/host tools may have a later revision than the bitstream, but changed
hardware/build inputs are rejected.

Download requires `--download`; `--expected-loaded` must exactly match PYNQ's
current overlay path. Otherwise the runner refuses before PCAP/MMIO writes.
It never resets/halts ARM, uses JTAG or writes SD/QSPI images. It verifies the
Phase 6 runtime identity before register writes, drains/stops before loading
ROM, loads once and repeats the reference's warm-boot count without downloading
again. Every job travels through actual PL UART TX-to-RX and the AXI receive
FIFO. Raw `.uart` bytes are retained separately; TX/RX/FIFO/error counts and
independent lifetime retirements are captured before stop clears state.

After every boot the runner requires acknowledged STOPPED and captures all
64 KiB of RAM. Independent host math checks every saved job result and the
complete output array using actual ELF symbols. The initial physical gate
requires all 28 measured counters to match the corresponding direct-SoC
reference exactly; any difference is saved and fails acceptance pending an
independently reproduced explanation. There is no silent tolerance or result
substitution. Full snapshots are retained, but unallocated/unused physical RAM
bytes are not claimed to match the simulator's initialization pattern.

The output must be a new directory. Partial UART/report evidence is preserved
on error, and an identified Phase 6 bridge is always safely stopped in cleanup.
An unidentified bridge is never written. The versioned report fingerprints the
reference, overlay and every collector dependency; the default read-only host
audit checks them against Git, rejects partial/mutated evidence and verifies
the final safe state. Host tests use a fake MMIO/PCAP boundary, not hardware
measurements. Real board execution remains a separate acceptance requirement.

For Verilator 5.020, coherent targets explicitly raise loop unrolling limits
to accommodate nonblocking reset-array assignments at 1024 lines. This follows
the documented [BLKLOOPINIT limitation](https://verilator.org/guide/latest/warnings.html#blkloopinit);
no RTL reset semantics, fault check or assertion is disabled to pass that case.
