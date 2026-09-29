# AsterBench v11 — engine-attributed workload records

Status: **implemented (Phase 17-A4)**. The coherent-SoC workloads (reduction,
Conv2D engines, ECG, CIFAR) emit v11; `scripts/asterbench_v11.py` and
`verification/common/asterbench_v11_record.h` validate it against one shared
mutation corpus. Existing AsterBench v2–v10 records and their historical
meanings remain unchanged.

## Purpose

v10's generic record has two attribution problems on the all-engine coherent
SoC:

1. `dma_bytes` is sourced from a device-store event shared by DMA and NPU, so
   NPU result stores can be reported as DMA bytes.
2. `accelerator_cycles` is read from the NPU's last-job register. It omits prior
   NPU jobs in the same workload and does not describe DOT8 activity.

v11 gives each requester its own fields and defines totals across the entire
common workload window. It is a software-visible record contract; it does not
change the frozen v1 MMIO, DMA, NPU, or DOT8 ABIs.

## Record shape

One ASCII, newline-terminated record begins `ASTERBENCH,`. The schema is exact:
every field appears once, unknown fields are rejected, and numeric encodings are
canonical. The ordering below is recommended but parsers may accept any order.

```text
ASTERBENCH,version=11,name=...,category=...,status=PASS,
size=...,iterations=...,param=...,seed=0x........,checksum=0x........,
clock_hz=...,harts=...,workers=...,l1=...,sync_memory=...,
line_words=...,line_count=...,memory_wait=...,
cycles=0x................,retired=0x................,
memory_transactions=0x................,backing_transactions=0x................,
cache_accesses=0x................,cache_misses=0x................,
dma_jobs=...,dma_completed_jobs=...,dma_aborted_jobs=...,dma_error_jobs=...,
dma_bytes=0x................,dma_job_cycles=0x................,
h0_dot8_accept=0x................,h0_dot8_wait=0x................,
h0_dot8_complete=0x................,h0_dot8_retire=0x................,
h1_dot8_accept=0x................,h1_dot8_wait=0x................,
h1_dot8_complete=0x................,h1_dot8_retire=0x................,
npu_jobs=...,npu_completed_jobs=...,npu_aborted_jobs=...,npu_error_jobs=...,
npu_bytes_read=0x................,npu_bytes_written=0x................,
npu_tiles=0x................,npu_job_cycles=0x................,
npu_compute_cycles=0x................
```

The record is physically emitted on one line; line breaks above are for
readability. `version=11` is required. `seed` and `checksum` are exactly eight
lowercase hexadecimal digits after `0x`; every 64-bit field is exactly sixteen.
Decimal fields use canonical unsigned decimal notation.

`harts` is the elaborated hart count. `workers` is the number of harts assigned
useful work for this workload, not merely the number of elaborated harts. The six
CPU counters retain the v10 meanings and are read from the primary hart's counter
bank. DOT8's four events are reported independently for each hart. Engine
geometry, full memory capacities, L2 parameters, source revision, toolchain,
compiler flags, and physical image identity are required in the capture manifest
that accompanies the raw record; they are not inferred from a workload name.

## Counter meanings

| Field | Exact meaning |
| --- | --- |
| `cycles`, `retired` | Primary hart's common start-to-freeze window and non-trapping retired instructions, using the existing counter ABI semantics. |
| `memory_transactions`, `backing_transactions` | Primary-hart CPU request and backing-memory transaction counts; device engines retain separate requester-attributed fields below. |
| `cache_accesses`, `cache_misses` | Combined primary-hart instruction/data cache events, using the existing v10 counter mapping. |
| `dma_jobs` | DMA START commands accepted during the workload. A rejected command is not a job; a started error/abort remains a job. |
| `dma_completed_jobs`, `dma_aborted_jobs`, `dma_error_jobs` | Mutually exclusive terminal outcomes for accepted DMA jobs. At freeze their sum equals `dma_jobs`; a passing workload has all accepted jobs completed successfully. |
| `dma_bytes` | Sum of `bytes_done` for each accepted DMA job, including the completed prefix of an aborted job. It is payload bytes owned by the DMA engine only; cache writebacks and NPU writes are excluded. |
| `dma_job_cycles` | Sum of the DMA engine's busy-cycle counter for every accepted DMA job. It is engine occupancy, not CPU cycles saved or an overlap claim. |
| `hN_dot8_accept` | Valid Xasterdot8 PCPI operations accepted by hart N during the common window. |
| `hN_dot8_wait` | Cycles hart N's valid Xasterdot8 operation waits for completion/admission, using the existing DOT8 event counter definition. It is a wait-event count, not total workload cycles. |
| `hN_dot8_complete` | Xasterdot8 operations completed by hart N. |
| `hN_dot8_retire` | Xasterdot8 instructions retired by hart N. |
| `npu_jobs` | NPU START commands accepted during the workload, including jobs that later terminate in error or abort. |
| `npu_completed_jobs`, `npu_aborted_jobs`, `npu_error_jobs` | Mutually exclusive terminal outcomes. At freeze their sum equals `npu_jobs`; a passing workload has all accepted jobs completed successfully. |
| `npu_bytes_read`, `npu_bytes_written` | Sum of the NPU engine's per-job logical byte counters over all accepted NPU jobs. These describe engine payload byte operations, not 32-bit backing-bus traffic. |
| `npu_tiles` | Sum of completed NPU output tiles over all jobs. |
| `npu_job_cycles` | Sum of each NPU job's busy cycles, including descriptor checks, memory waits, tile movement, and any time paused while busy. |
| `npu_compute_cycles` | Sum of accepted NPU array-step cycles over all jobs. It does not imply every PE was active on every step; PE utilization is workload/shape-specific. |

The DMA and NPU totals are cumulative across the measurement window, not values
read from only the last job. Software may aggregate terminal per-job snapshots;
the reads used for that aggregation remain in the end-to-end CPU cycle window.
If a future hardware accumulator is used instead, its independent scoreboard
must prove equality with the per-job sum.

## Invariants and zero-activity rules

- All output and guard checks pass before a record may say `status=PASS`.
- `dma_completed_jobs + dma_aborted_jobs + dma_error_jobs == dma_jobs` at freeze.
- `npu_completed_jobs + npu_aborted_jobs + npu_error_jobs == npu_jobs` at freeze.
- `npu_compute_cycles <= npu_job_cycles`; all NPU totals are zero when
  `npu_jobs == 0`.
- All DMA totals are zero when `dma_jobs == 0`.
- `retired`, `memory_transactions`, `backing_transactions`, `cache_accesses`, and
  `cache_misses` are bounded by `cycles`; `cache_misses <= cache_accesses`.
- With one elaborated hart, h1 DOT8 activity events are zero.
- An absent hart has zero DOT8 counters. A workload with no DOT8 instructions
  has all DOT8 counters zero.
- In a successful DOT8-only interval, accepts, completes, and retires agree for
  each executing hart. Wait counts may be greater and reflect stalls.
- Unknown, duplicate, missing, truncated, overflowing, or inconsistent fields
  fail validation. A configuration mismatch is not a comparable speedup.

## Measurement and provenance

Start and freeze use the existing common counter-control edges. CPU, DMA, and
DOT8 hardware counters are read from their frozen banks. DMA/NPU totals are
accumulated across every job in the interval. Setup, transfers, software
materialization, engine wait/join, and result availability belong in the
end-to-end interval; a kernel-only interval is a separately named measurement.
UART formatting, host transport, checksum formatting, and artifact capture are
outside both intervals.

Every study artifact binds the raw UART record, ELF/ROM, source revision and
dirty state, toolchain/version/flags, SoC parameters, memory model/latency, cache
policy, NPU geometry, physical image (when applicable), independent oracle, and
derived metrics. A record under ignored `build/` is a development result, not a
retained baseline.

## Compatibility and implementation order

- v2–v10 parsers and historical records remain supported without reinterpretation.
- The v11 Python validator and C++ parser share a valid/malformed fixture corpus.
- RTL counter scoreboards independently inject DMA-only, NPU-only, and mixed
  device traffic and verify byte ownership and cumulative totals.
- Firmware migration follows only after the schema and scoreboards pass; all
  coherent workload records then use v11 consistently.
- `make phase17-conv-matrix` is a same-top v11 diagnostic until retained
  provenance is complete. Do not treat its ignored `build/` output as an accepted
  Phase 17 performance baseline.
