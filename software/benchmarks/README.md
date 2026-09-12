# AsterBench

The default workload copies 64 RAM words four times. Source word i is
`0x13570000 ^ (i * 0x1021)`. Initialization is outside the measurement
window; byte-for-byte validation and checksum generation happen after freeze.
One buffer is 256 bytes, so the four repetitions copy 1,024 logical bytes.
Instruction fetches and memory traffic are reported separately from that
logical workload size.

`BENCH_WORKLOAD` selects `memcpy`, `walk_sequential` or `walk_random`.
`BENCH_WORDS` is a power of two from 2 through 4096; repetitions range from
1 through 64 and seeds are unsigned 32-bit values. Defaults are 64 words,
4 repetitions and seed `0x13570000`.

Both walks chase a ring of word indices with one dependent RAM load per hop.
The random ring uses a seeded Fisher-Yates permutation constructed outside
the measurement window; seed zero is supported. Both patterns perform one
unmeasured full-ring warm-up, then `words * repetitions` measured hops through
the same instruction kernel. The linker places `walk`/`measure` at matching
PCs starting at a 256-byte boundary and `links` at RAM base `0x10000000`.
Host tests compare exact opcodes and addresses, including every working-set
sweep point, to prevent initialization-code size from biasing I$ behavior.

For walks, `bytes` is the ring footprint, not initialization/permutation
scratch space. Each node is visited once per traversal. The expected sum is
`words * (words - 1) / 2 * repetitions` modulo 2^32, and the final index must
be zero. An independent post-window visited bitmap also checks link bounds,
uniqueness and return to the start, so a degenerate short cycle cannot pass
on checksum alone. The host independently calculates each workload checksum.

## Record v2

One ASCII newline-terminated record begins `ASTERBENCH,`. Every field below
is required exactly once; unknown fields, duplicates, malformed numbers,
incomplete lines, failed status and invalid relationships are rejected.
Order is not significant. There is no abbreviated or optional-counter mode.

| Fields | Encoding / meaning |
| --- | --- |
| version, name, status | `2`, workload identifier, `PASS` |
| bytes, repetitions | decimal logical buffer size and repetitions |
| seed, checksum | exactly 8 hexadecimal digits after `0x` |
| clock_hz | decimal nominal RTL frequency, not simulation wall-clock speed |
| l1, sync_memory | decimal booleans read from RTL configuration registers |
| line_words, line_count, memory_wait | decimal cache geometry / backing wait cycles |
| cycles, retired | enabled clocks / non-trapping RVFI retirement events |
| memory_transactions, backing_transactions | native CPU requests / ROM-RAM lower-bus requests |
| cache_accesses, cache_misses | private L1 cacheable lookups / misses |
| dma_bytes, accelerator_cycles | reserved sources, required zero until connected |

Every counter is exactly 16 hexadecimal digits after `0x`. The memcpy checksum
starts at zero and folds each destination word as
`checksum = (checksum * 33) ^ word`, modulo 2^32. The current fixture's checksum
is `0xc4be3200`. It is a correctness fingerprint, not a cryptographic hash.

The hardware freezes every counter on the same command edge before any MMIO
readout or UART formatting. Counts include the marker/pipeline boundary
overhead; this is an explicitly defined hardware window, not a claim to count
only loop-body instructions. See the [counter contract](../../docs/architecture.md).

## Run, save, compare

```sh
make bench
make ENABLE_L1=0 SYNC_MEMORY=1 bench
python3 scripts/bench_results.py capture --l1 0 --output build/results/baseline.json
python3 scripts/bench_results.py capture --l1 1 --output build/results/candidate.json
python3 scripts/bench_results.py compare build/results/baseline.json build/results/candidate.json
```

Capture performs a fresh build, validates the full serial record, and saves
revision, dirty state, a per-source SHA-256 manifest, compiler binary/version,
effective compiler/linker flags, Verilator version, firmware/ELF/model hashes
and the exact build command. Clock/cache/memory/workload configuration comes
from the executing firmware record, not an assumed host configuration.
A dirty capture is explicitly labelled and fingerprinted. Keep sources stable
during capture; a changed source tree causes failure.

The JSON comparison rejects missing provenance, disagreement with raw serial
bytes, and mismatched workload name/size/repetitions/seed/checksum. It reports
counter deltas and speedup ratios, which may be below 1. The nominal-time ratio
also accounts for differing configured clocks; it is not a host-time benchmark.

`scripts/asterbench.py` validates a single raw record on stdin. All three C++
serial harnesses use `verification/common/bench_record.h`; the host regression
runs the same malformed/valid corpus against both implementations. The PYNQ
runner uses the Python parser. Full board-facing records are tested through
both standalone UART decoding and the Linux AXI/serial-loopback path.

## Phase 4 experiment suite

```sh
python3 scripts/cache_experiments.py --output-dir build/results/cache-study
python3 scripts/cache_experiments.py --output-dir build/results/cache-study --audit-only
```

The fixed plan contains 60 distinct configurations plus a fresh-process repeat:

- Cache off/on for all three workloads, with async/zero-wait, sync/one-wait
  and sync/four-wait backing memory.
- Sequential/random working sets of 64, 128, 256, 512, 1024, 2048, 4096 and
  8192 bytes, cache off/on, against a 256-byte I$ and 256-byte D$.
- Sequential/random 512-byte rings with 64, 128, 256, 512 and 1024 bytes per
  private cache (16-byte lines).
- Additional random rings at seeds 0, 1 and `0xa57e` for 512/4096-byte sets.

Overlapping configurations are deduplicated but retain every experiment group
in `manifest.json`. Each JSON includes raw UART bytes, typed counters and full
provenance; `summary.csv` is derived from validated captures. The auditor
requires the complete plan, expected settings/checksums, consistent source and
compiler hashes, matched retirement counts and an identical repeat record and
firmware hash. Each run starts a new simulator process, resetting RAM/cache;
an isolated temporary build directory may reuse identical models/firmware.
Do not edit source or switch revisions during a study.

Cache counters combine I$ and D$, including the documented marker/pipeline
boundary overhead. `bytes` alone is not the entire D$ working set: measured
stack and result stores also compete for lines. Warm-up excludes compulsory
ring initialization, but capacity/conflict behavior remains. Larger cache-size
experiments change both I$ and D$, so they are not pure D$-only sensitivity.
Ratios report actual cycles and traffic; a ratio below one is a slowdown.
The suite measures this single-issue core and blocking write-through caches,
not a prediction of a future multicore/L2 system or achieved FPGA frequency.

## Phase 5 parallel mix (v3)

`make parallel` uses the new multicore memory map and runtime, not the legacy
single-core overlay. `PARALLEL_WORDS` is 2..1024 (odd lengths supported),
`PARALLEL_ROUNDS` 1..64, `PARALLEL_JOBS` 1..16, and `PARALLEL_WORKERS` is 1 or 2
and cannot exceed `HART_COUNT`. Defaults are 64 words, four rounds, three jobs,
two physical harts/workers and base seed `0x13570000`. The same firmware can run
one worker on either one-core or two-core hardware without changing its map.

All arithmetic is modulo 2^32, with logical right shifts. For job `j` (1-based),
the seed is `base_seed XOR (j * 0x9e3779b9)`. Input word `i` is
`seed XOR (i * 0x1021)`. Each round `r` (zero-based) transforms each word:

```text
x = x + 0x9e3779b9 + r
x = (x XOR (x >> 16)) * 0x7feb352d
x = (x XOR (x >> 15)) * 0x846ca68b
x = x XOR (x >> 16)
```

With two workers, hart 0 owns indices `[0, ceil(words/2))` and hart 1 the rest;
with one worker, hart 0 owns all indices and hart 1 stays reset. Shared inputs
and outputs are uncached, with disjoint writers. Each worker copies its slice
to its own private cached buffer, performs rounds-major mixing, copies outputs
back, and publishes its count/checksum. A slice checksum sums
`output[i] XOR ((i+1) * 0x9e3779b9)` over its global indices. The complete
checksum is the modular sum of slice checksums. This is an error-detection
checksum, not a cryptographic hash: firmware additionally checks **every**
output against a differently traversed scalar reference, and host C++/Python
oracles independently check the expected slice results.

Each job uses ARM/READY/GO/DONE epochs through the ownership-restricted
mailboxes. The common counter window starts after READY but before GO and ends
after both result slices and DONE have been observed. It includes GO dispatch,
private input copies, kernel work, shared output copies/checksum accumulation,
and completion waits. It excludes shared input generation, output poisoning,
scalar validation, initial worker startup/ARM handshake, counter readout and
UART formatting. Those exclusions are the same for one-/two-worker comparisons;
GO/DONE overhead is present only when coordination is needed. Thus this is a
complete dispatched-job measurement, not total application boot-to-print time.

Caches are reset at each warm boot, not between jobs. Startup and each prior
job influence the next job's cache state; compare matching job indices and
report individual as well as summed-job cycles. UART printing between jobs is
outside the counter window but can change the phase of the waiting secondary
on a real serial platform; physical cycle counts need not exactly equal an
event-level UART simulation. Work identities and result checksums must match.
Per-hart counter semantics and accepted measurement edges remain exact.

V3 records start `ASTERBENCH,version=3,name=parallel_mix,status=PASS,...`.
All fields are mandatory, with decimal integers, fixed-width `0x` hex32 values
and hex64 counters; duplicate/unknown/missing fields or failed status are errors.
The schema is explicit in `scripts/asterbench_parallel.py` and independently
implemented in `verification/common/parallel_record.h`. Records contain:

- Job identity: bytes, rounds, jobs, job, base_seed, seed.
- Partition/results: harts, workers, each hart's word count and checksum, total
  checksum. An inactive hart has zero words, checksum and non-cycle events.
- Configuration: clock_hz, l1, sync_memory, line_words, line_count, memory_wait.
- Common elapsed cycles and both complete eight-counter banks, prefixed
  `h0_`/`h1_`. Both cycle counters equal the common interval; do not add them.
  Native/cache/retirement events are per hart; backing transactions may be
  summed for total shared-bus traffic. DMA/accelerator counters stay zero.

Cache misses are counted at lookup while accesses count completed cacheable
native operations. A measurement edge can cut an outstanding refill, so v3
does not incorrectly assume misses <= accesses in every interval. The RTL
harness instead independently counts the actual events and verifies all bank
values. It observes PC-qualified kernel retirements on each core and checks
overlap for jobs of at least 64 words and four rounds. Smaller jobs still run
both assigned slices but coordination may dominate or eliminate overlap.

`make parallel-matrix` covers three physical/active-core combinations × two
cache modes × four memory timing modes. `make parallel-workloads` covers
boundary/odd sizes, long rounds and different seeds. Both default to two warm
boots of three jobs per run. `scripts/parallel_results.py capture` retains both
raw serial streams, every typed job, per-job kernel observations, the complete
hashed build/run log and source/compiler/firmware/model provenance. Its audit
checks those bindings and independent references; its comparison checks equal
work, reports configuration/compiler differences and computes actual speedup
or slowdown. V2 capture and parsing remain separate and backward compatible.
