# v1.1 shared-L2 analysis

All numbers are validated AsterBench v10 records; every checksum matches its
independent oracle (`reduce_parallel` `0x5c808000`, `conv2d_npu` `0x07df8000`),
so the L2 is coherent in every configuration.

## The L2 is a latency-hiding structure

`ENABLE_L2` and `MEMORY_WAIT_CYCLES` sweep, 4 KiB L2 (`L2_LINE_COUNT=256`),
cycles (ratio vs the matching L2-off configuration):

| Memory latency | `reduce_parallel` L2 off | L2 on | `conv2d_npu` L2 off | L2 on |
| --- | ---: | ---: | ---: | ---: |
| wait0 | 222 374 | 237 188 (**0.94×**) | 4 636 733 | 4 920 265 (**0.94×**) |
| wait16 | 419 644 | 307 700 (**1.36×**) | 8 249 901 | 6 741 765 (**1.22×**) |
| wait64 | 1 028 517 | 519 517 (**1.98×**) | 18 613 097 | 12 149 573 (**1.53×**) |

**Finding:** with a fast backing memory the L2 is a 6% *slowdown* — its hit and
refill bookkeeping costs more than the memory it replaces. As memory latency
rises the L2 becomes a large win, reaching **1.98×** on the reduction and
**1.53×** on the convolution at `wait64`. The crossover sits between `wait0`
and `wait16`. This is the mechanism the Phase 14 memory-latency result predicted.

## L2 size matters, up to the working set

`L2_LINE_COUNT` sweep at `wait64`:

| L2 size | `reduce_parallel` | vs off | `conv2d_npu` | vs off |
| --- | ---: | ---: | ---: | ---: |
| off | 1 028 517 | 1.00× | 18 613 097 | 1.00× |
| 64 (1 KiB) | 1 047 429 | 0.98× | 12 999 909 | 1.43× |
| 256 (4 KiB) | 519 517 | 1.98× | 12 149 573 | 1.53× |
| 1024 (16 KiB) | 515 122 | 2.00× | 10 392 073 | 1.79× |

**Finding:** a 1 KiB L2 already captures most of the convolution's reuse
(1.43×) but does nothing for the reduction, whose working set is larger. 4 KiB
captures both; 16 KiB still improves the convolution (1.79×) with sharply
diminishing returns on the reduction. The knee is where the working set fits.

## Answers to the research questions

- **"How much do L1/L2 cache sizes affect real workloads?"** Now fully
  answered. The L1 result (Phase 14) showed the L1 alone is a net slowdown on
  the minimal SoC; this phase shows the L2 recovers and then dominates once the
  backing memory is slow. Cache benefit is a function of *latency*, not size
  alone.
- **"When does memory bandwidth become the bottleneck?"** The L2 crossover is
  the same phenomenon: the design is latency-bound, and the L2 is what breaks
  the bottleneck.

## Design note

The L2 defaults to `ENABLE_L2=0`, so the frozen v1.0 baseline is bit-identical
(`ENABLE_L2=0` reproduces the v1.0 `make check` cycle counts exactly). It is a
deliberate configuration, not a default, precisely because it loses at the
default zero-latency memory model.

## Routed overlay and physical acceptance

The L2-enabled all-engine overlay routes with **WNS +3.158 ns** — statistically
unchanged from the non-L2 overlay's +3.164 ns — so the combinational array read
does not cost timing. LUT usage rises to 56% and BRAM to 23% (the 1 KiB L2
array). Reset signoff passes with zero routing errors.

Two warm boots on the Pynq-Z1 at 31.25 MHz run `reduce_parallel` with the L2
enabled and report checksum `0x5c808000`, matching the independent oracle on
both boots (254 368 cycles each), with a clean STOPPED snapshot. The L2 is
coherent on hardware.

## Remaining for v1.1

- A write-back or inclusive L2, and L2/L1 inclusion policy, are explicitly out
  of scope.
