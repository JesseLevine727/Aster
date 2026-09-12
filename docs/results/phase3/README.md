# Phase 3 retained evidence

Captured on 2026-09-12 from clean source revision
`c2dfef74c1d9934faabab1647d9059516104f8af`. Each JSON contains actual serial bytes,
typed fields, the source manifest and compiler/firmware/model provenance.
These are Verilator measurements, not physical-board observations.

All runs use the same 256-byte buffer, four repetitions, seed `0x13570000`,
checksum `0xc4be3200`, 31.25 MHz nominal RTL clock and 16 lines × 4 words per
private cache. Every run retires 1,553 instructions and accepts 2,320 native
CPU requests; DMA and accelerator counters are zero.

| Capture | Memory | L1 | Cycles | Backing transactions |
| --- | --- | --- | ---: | ---: |
| [uncached-async.json](uncached-async.json) | zero wait | off | 6,962 | 2,320 |
| [cached-async.json](cached-async.json) | zero wait | on | 7,319 | 336 |
| [uncached-sync.json](uncached-sync.json) | synchronous | off | 9,026 | 2,320 |
| [cached-sync.json](cached-sync.json) | synchronous | on | 7,655 | 336 |

At zero wait, caching reduces traffic but costs 357 cycles (about 5.1% more).
The cache controller's miss/refill sequencing has overhead even when backing
memory is fast. At synchronous latency, it saves 1,371 cycles (about 15.2%
fewer, 1.179× speedup). These results are specific to this tiny copy workload
and geometry; they are not the complete Phase 4 working-set/access-pattern/
cache-size study.

[cached-async-repeat.json](cached-async-repeat.json) is a separate fresh build
and run with an identical full record and firmware SHA-256. Simulator hashes
can differ because they embed their distinct temporary ROM file paths.

Reproduce/compare from the repository root:

```sh
python3 scripts/bench_results.py capture --l1 0 --output build/results/new-uncached.json
python3 scripts/bench_results.py capture --l1 1 --output build/results/new-cached.json
python3 scripts/bench_results.py capture --l1 0 --sync-memory 1 --output build/results/new-uncached-sync.json
python3 scripts/bench_results.py capture --l1 1 --sync-memory 1 --output build/results/new-cached-sync.json
python3 scripts/bench_results.py compare docs/results/phase3/uncached-async.json docs/results/phase3/cached-async.json
python3 scripts/bench_results.py compare docs/results/phase3/uncached-sync.json docs/results/phase3/cached-sync.json
```

The complete simulation regression and Phase 1 four-configuration matrix also
pass at this implementation. UART paths validate the full v2 record twice;
host tests cover malformed records and provenance; dedicated counter and RVFI
tests cover event accuracy, stalled execution, common snapshots and rollover.

Vivado 2025.1 routed the corresponding RTL on `xc7z020clg400-1`:

| Variant | Setup slack | Hold slack | LUTs | Registers | RAMB36 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Standalone | 15.327 ns | 0.037 ns | 5,666 | 5,880 | 32 |
| Linux overlay | 13.378 ns | 0.044 ns | 5,878 | 6,625 | 32 |

Both complete bitstream generation without DRC errors. Linux methodology has
zero violations and no unconstrained internal endpoints. Asynchronous UART/LED
outputs have explicit false paths. These historical images were not physically
validated and contain the subsequently diagnosed Linux-shell reset defect.
The corrected current shell and firmware are physically validated in the
[Phase 2 closeout](../phase2/README.md); that later evidence does not retroactively
validate these old bitstreams.
