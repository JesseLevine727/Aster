# v1.1 closeout: shared L2 cache

Frozen implementation: `d18b387bb30b4c52d0551e8188ca52be7689d2e6`
Baseline: Aster v1.0 (tag `v1.0`)

## What was added

A memory-side, direct-mapped, read-allocate, write-through shared L2 between the
L1 module's backing port and the memory system. It caches RAM only, passes every
other address through combinationally, and stays coherent because every L1
refill, writeback, flush and DMA device access crosses it and RAM is always
updated. `ENABLE_L2=0` is bit-identical to the v1.0 baseline.

## Headline results

- **Latency crossover:** the L2 is **0.94×** (6% slower) at `wait0` and up to
  **1.98×** faster at `wait64`. It is a latency-hiding structure, not a
  size-for-size win.
- **Size scaling:** at `wait64`, a 1 KiB L2 gives 1.43× on the convolution but
  nothing on the reduction; 4 KiB captures both; 16 KiB reaches **1.79×** on the
  convolution.
- **Coherence:** every checksum matches its independent oracle in every
  configuration.

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Contract | `spec/l2.md` |
| 2 | Latency crossover study | `studies/l2-cache/study.json` |
| 3 | L2 size study | `studies/l2-size/study.json` |
| 4 | Analysis | `analysis.md` |
| 5 | Clean check | `verification/make-check.log` |
| 6 | Frozen source | `source/source-state.json` |
| 7 | Routed L2 overlay | `fpga/` |
| 8 | Physical acceptance | `physical/` |

## Routed overlay and physical acceptance

- Routed all-engine overlay with the L2 enabled: **WNS +3.158 ns** (unchanged
  from the non-L2 overlay's +3.164 ns), TNS 0, zero routing errors, reset
  signoff PASS. 56% LUTs, 23% BRAM.
- Two warm boots on the Pynq-Z1 at 31.25 MHz running `reduce_parallel` with the
  L2 enabled: **checksum `0x5c808000` matches the independent oracle on both
  boots** (254 368 cycles), with a clean STOPPED snapshot. The L2 is coherent
  on hardware.

## Remaining

- Write-back/inclusive L2 and L1/L2 inclusion policy are explicitly out of scope.

## Audit

```
python3 scripts/audit_l2.py docs/results/v1.1/closeout-bb881fc --current
```
