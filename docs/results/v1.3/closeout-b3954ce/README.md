# v1.3 closeout: 50 MHz slow-corner closure

Frozen implementation: `b3954ce52f2f08088693c1282f28d5016e0e5712`
Baseline: Aster v1.2 at `4955bfb`

## What changed

Three measurement-preserving pipelines and a timing-driven implementation close
the frozen design's slow corner at 50 MHz:

1. **Perf events.** The event/increment inputs at each performance-block
   boundary are registered before the 64-bit counters.
2. **Cache flush scan.** The flush-scan owner/index is registered before the
   data-array read.
3. **NPU descriptor check.** The malformed-descriptor check is pipelined into
   dedicated CHECK/VERIFY states.
4. **Implementation.** The overlay is built with `place_design -directive
   Explore`, `route_design -directive Explore` and `phys_opt_design -directive
   AggressiveExplore`.

## Headline results

- **Static closure:** WNS **+0.191 ns** at a 20 ns constraint, TNS 0.000,
  **0 failing endpoints** of 56 320, hold met (WHS +0.031 ns), zero routing
  errors and reset signoff PASS.
- **Area:** 48.17% LUTs, 18.60% registers, 22.86% BRAM, 24 DSPs.
- **Physical:** two warm boots on the Pynq-Z1 at **50 MHz** running
  `reduce_parallel`: checksum `0x5c808000` matches the independent oracle on
  both boots (232 958 cycles, `clock_hz=50000000`), clean STOPPED snapshot.

## Requirements

| # | Requirement | Evidence |
| ---: | --- | --- |
| 1 | Contract | `spec/v1.3.md` |
| 2 | Routed overlay | `fpga/` |
| 3 | Analysis | `analysis.md` |
| 4 | Clean check | `verification/make-check.log` |
| 5 | Frozen source | `source/source-state.json` |
| 6 | Physical acceptance | `physical/` |

## Audit

```
python3 scripts/audit_v13.py docs/results/v1.3/closeout-b3954ce --current
```
