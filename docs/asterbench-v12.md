# AsterBench v12 — the Phase 20 SoC's workload records

Status: **approved by the owner, 8 October 2026** (with
[`matrix.md`](matrix.md)); to be implemented in 20.4's first step. v2–v11
records keep their meanings. v12 is
soc.md §8's record:
- v11's engine-attributed fields;
- both harts' counters, not only the primary hart's;
- the SoC's configuration;
- the new DMA's and the NPU v2's totals;
- the fabric counters;
- each hart's work interval, for the overlap gate.

## Record shape

- **One line:** an ASCII record on the console, beginning
  `ASTERBENCH,version=12,` and ending with a newline.
- **An exact schema:** every field appears once, an unknown field is
  rejected, and encodings are canonical:
  - counts are unsigned decimals with no leading zeros;
  - `seed` and `checksum` are `0x` and eight lowercase hex digits.

  Counts are decimal, not v11's sixteen hex digits, to keep the record
  short (about 2.5 KB).
- **Order:** the order below is the emitter's; a parser may accept any.

**Identity**

| Field | Meaning |
| --- | --- |
| `name` | the case's name, unique within its family (`conv2d_direct_k5`, `memcpy_4096_a03`, …) |
| `family` | `cpu`, `memory`, `dma`, `coherence`, `dsp`, `npu_gemm`, `ml`, `ecg` |
| `method` | `scalar`, `multicore`, `dot8`, `dma`, `cpu_copy`, `npu`, `npu_direct`, `npu_im2col`, `pipeline` |
| `window` | `kernel` or `e2e` (matrix.md §4) |
| `status` | `PASS` only if every output and guard check passed in the firmware; otherwise `FAIL` |
| `size`, `iterations`, `param` | the case's own parameters, as each family defines them |
| `seed`, `checksum` | the inputs' seed and the outputs' checksum, which the oracle recomputes |

**Configuration** (read from the hardware where a hart can read it; the rest
from the build, checked by the runner: the owner's decision, matrix.md §9,
item 2)

| Field | Meaning | Source |
| --- | --- | --- |
| `clock_hz` | the design's clock | ABI 4 metadata `0x88` |
| `harts`, `workers` | elaborated harts (1 or 2); harts given work (1 or 2, at most `harts`) | `0x2000_2008`; software |
| `dcache` | 1 if the data cache caches main memory, 0 if it is off | the build, checked against the testbench's readback |
| `cache_state` | `cold` (the first pass after reset) or `warm` | software |
| `line_words`, `line_count` | the caches' geometry | ABI 4 metadata `0x90`, `0x94` |
| `memory_wait` | the backing memory's wait cycles (1 + the added waits) | ABI 4 metadata `0x98` |
| `npu_dim` | the NPU's array size | the NPU's GEOMETRY register (`{4·DIM, 4·DIM, DIM, DIM}`) |
| `npu_port_bytes`, `npu_strips` | the NPU's port width and A strips | the build, checked against the testbench's readback (`0x3F058`) |

**Per hart** (`h0_`, `h1_`; ABI 4's fourteen counters, in its order, over the
common window)

`cycles`, `retired`, `memory_transactions`, `icache_accesses`,
`icache_misses`, `dcache_accesses`, `dcache_misses`, `backing_transactions`,
`amos`, `sc_success`, `sc_failure`, `dirty_interventions`, `invalidations`,
`writeback_words`.

Notes on these counters:
- **`cycles`** is the common window's, the same in both harts' pages.
- **`amos`** counts every completed A instruction: lr, sc and the AMOs,
  unlike `f_amos`, which counts only the AMOs.

Then:
- **DOT8, v11's four events:** `dot8_accept`, `dot8_wait`, `dot8_complete`,
  `dot8_retire`.
- **The hart's work interval:** `work_start` and `work_end`, in cycles from
  the window's start, stamped by software around the hart's useful work (0
  and 0 if it had none).

**The DMA** (ABI 5's counters at `0x3000_0100`, by index, over the window;
`dma_jobs` counted by software)

| Field | Source |
| --- | --- |
| `dma_jobs` | the STARTs software saw accepted (v11's meaning) |
| `dma_completed_jobs` | index 10, successful jobs; a zero-length START counts here |
| `dma_aborted_jobs` | 11 |
| `dma_error_jobs` | 12 |
| `dma_rejected` | 13, rejected commands |
| `dma_busy_cycles` | 0 |
| `dma_wait_cycles` | 1, offered but not accepted, both ports |
| `dma_reads`, `dma_writes` | 2 and 3, answers |
| `dma_bytes` | 4, payload bytes |
| `dma_backing_reads`, `dma_backing_writes` | 5 and 6, acceptances |
| `dma_invalidations` | 9 |

Indices 7 and 8 read 0 and are left out.

**The NPU** (its totals, `0x4000_0100`, cleared at the window's start)

`npu_jobs`, `npu_completed_jobs`, `npu_aborted_jobs`, `npu_error_jobs`,
`npu_job_cycles`, `npu_active_cycles`, `npu_macs`, `npu_bytes_read`,
`npu_bytes_written`, `npu_tiles`. The outcomes and the tiles are summed by
software over the window's jobs, as v11's were.

**The fabric** (its 48 counters at `0x2000_3300`, in its order; requesters
`i0 d0 i1 d1 n r w`)

| Fields, in the hardware's order | Count |
| --- | --- |
| `f_accepted_<req>` | 7 |
| `f_waited_<req>` | 7 |
| `f_bank<b>_reads` for banks 0–3 | 4 |
| `f_bank<b>_writes` for banks 0–3 | 4 |
| `f_bank<b>_conflicts` for banks 0–3 | 4 |
| `f_snoops_c<c>p<p>` | 6 |
| `f_invalidations_c<c>p<p>` | 6 |
| `f_resv_ended_h<h>` | 2 |
| `f_amos` | 1 |
| `f_longest_<req>` | 7 |

Each data cache's snoop ports are p0, the other hart's writes; p1, the
NPU's; and p2, the DMA's.

## Invariants

The validators reject a record that breaks any of these. Most of them make
the record reconcile with itself, which soc.md §11's totals gate needs.

**Configuration**
- `workers <= harts`.
- `h1_cycles == h0_cycles`, since both read the common window.
- With one hart, every other `h1_` counter and its interval are zero.
- `dcache` is 0 or 1.
- `npu_dim` is 4 or 8. `npu_port_bytes` is 4 or 8, and 8 when `npu_dim` is
  8. `npu_strips` is 1 or 2.

**Each hart**
- `retired`, `memory_transactions`, the accesses and the misses are each at
  most `cycles`.
- `backing_transactions` is at most twice `cycles`, since a refill and a
  write can both count in one cycle.
- Misses are at most accesses.
- `dirty_interventions` and `writeback_words` are 0 (the caches are
  write-through).
- `work_start <= work_end <= cycles`.
- DOT8: `accept == complete == retire`, and `wait == 0`, since the core
  never makes a dot8 wait.

**The DMA**
- The outcomes sum to `dma_jobs`, and every total is 0 when it is 0.
- It reconciles with the fabric:
  - `dma_backing_reads == f_accepted_r`;
  - `dma_backing_writes == f_accepted_w`;
  - `dma_wait_cycles == f_waited_r + f_waited_w`;
  - `dma_invalidations == f_invalidations_c0p2 + f_invalidations_c1p2`.

  The fabric counts the ARM side's accesses too, but none happen while the
  harts run.

**The NPU**
- The outcomes sum to `npu_jobs`, and every total is 0 when it is 0.
- `npu_active_cycles <= npu_job_cycles`.
- `npu_macs <= npu_active_cycles × npu_dim²`.
- With no NPU job, `f_accepted_n == 0`.

**The fabric**
- Each snoop port's invalidations are at most its snoops.
- Each requester's longest wait is at most its cycles waited.
- The banks' writes sum to at most
  `f_accepted_d0 + f_accepted_d1 + f_accepted_n + f_accepted_w`. An AMO
  and a failed sc each count as one bank write at their acceptance.

**A passing record** has every output check passed, every DMA and NPU job
completed, and none rejected unless the case is about rejection.

## The validators and the corpus

- **Python:** `scripts/asterbench_v12.py`.
- **C++:** `verification/common/asterbench_v12_record.h`, with a command-line
  wrapper.
- **The shared corpus** (`verification/host/test_asterbench_v12.py`, in
  `make host-tests`), as v11's: valid records from every family, then every
  field deleted, duplicated, mis-encoded and set out of range, and each
  invariant broken. Both validators must agree on every one.

## Measurement

- **The window** starts and freezes with the common command word
  (`0x2000_3080`), as v11's did. It covers both harts' counter pages, the
  DOT8, DMA and fabric counters, and the window's cycles.
- **The NPU's totals** are cleared (CLEAR_TOTALS) just before the window
  starts. Software checks that no job crossed either edge.
- **Not measured:** console output, checksums' formatting and oracle work.
- **The manifest** (matrix.md §6) binds each record to its firmware,
  simulation build, source tree, toolchain and oracle result.
