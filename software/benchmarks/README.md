# AsterBench

The initial workload copies 64 RAM words four times. Source word i is
`0x13570000 ^ (i * 0x1021)`. Initialization is outside the measurement
window; byte-for-byte validation and checksum generation happen after freeze.
One buffer is 256 bytes, so the four repetitions copy 1,024 logical bytes.
Instruction fetches and memory traffic are reported separately from that
logical workload size.

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

Every counter is exactly 16 hexadecimal digits after `0x`. The checksum
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
