# Phase 5 evidence

Phase 5 is complete. [The `71e2570` closeout](closeout-71e2570/README.md) retains
18 real PYNQ warm boots, 48 benchmark jobs, complete legacy/multicore matrices,
FPGA signoff, raw reference/physical records and the hash-checked audit.
The initial simulation-only checkpoint below is historical; its source predates
the data-request fault qualification fix and its cycle counts are not current.

## Parallel software/RTL checkpoint: `4188064`

`parallel-4188064/one.json` and `two.json`, with their complete `.log` files,
were captured from clean source `4188064c4a98a36ca1bb9e8ba27b541009068722`.
Both metadata records report `dirty: false` and the same source fingerprint
`16cee07c28453ce6b005a48d87554a93a7c12333327672c43cef1c71157409f1`.
They contain all three jobs from both warm boots, compiler/firmware/model
hashes, raw UART records and PC-qualified per-hart kernel observations.

This is **Verilator simulation**, not an FPGA performance result. Both models
have two physical RTL hart instances and private 4-word × 16-line caches;
synchronous memory uses one added wait cycle. One-worker firmware holds the
second hart reset; two-worker firmware executes both slices. Work is identical:
64 words, four rounds, three jobs, base seed `0x13570000`.

| Active workers | Sum of three job intervals | Relative speed |
| --- | ---: | ---: |
| 1 | 121,515 cycles | 1.000× |
| 2 | 61,569 cycles | 1.974× |

Both warm boots reproduce all job records exactly. Each core's kernel
retirement interval overlaps the other's in every two-worker job. Each of the
16 counter-bank values is checked against an independent RTL event scoreboard;
slice checksums agree with the independent C++ and Python references. Firmware
also compares every output word with its scalar reference.

The interval includes GO dispatch, private input copying, computation, shared
output/checksum publication and completion waiting. It excludes startup,
input generation, ARM/READY setup, reference checking, counter readout and UART.
See the [complete benchmark contract](../../../software/benchmarks/README.md#phase-5-parallel-mix-v3).

Audit retained files from the repository root:

```sh
python3 scripts/parallel_results.py audit docs/results/phase5/parallel-4188064/one.json
python3 scripts/parallel_results.py audit docs/results/phase5/parallel-4188064/two.json
python3 scripts/parallel_results.py compare docs/results/phase5/parallel-4188064/one.json docs/results/phase5/parallel-4188064/two.json
```

Reproduce on the identified source revision with fresh output paths:

```sh
python3 scripts/parallel_results.py capture --workers 1 --sync-memory 1 --output build/parallel-reproduction/one.json
python3 scripts/parallel_results.py capture --workers 2 --sync-memory 1 --output build/parallel-reproduction/two.json
```

Development gates also passed for that implementation: `make check` (29 host
tests plus RTL/system tests), `make parallel-matrix` (24 configurations), and
`make parallel-workloads` (10 boundary/seed/round configurations). Those broad
development runs are not claimed as retained clean-revision final closeout
logs; the final evidence is retained separately in `closeout-71e2570/`.
