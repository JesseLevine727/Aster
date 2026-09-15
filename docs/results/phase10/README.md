# Phase 10: CPU vs multicore vs ISA vs NPU — simulation results

Status: **primary simulation study complete; fresh repeats and board-backed
results remain.** The contract is [`docs/phase10.md`](../../phase10.md).

This directory retains the first full AsterBench v8 primary study: 288 captures
covering three signed-INT8 kernels (dot, FIR, GEMM) across four execution paths
(scalar CPU, two coherent harts, Xasterdot8, and the 4×4 INT8 NPU), two byte
placements (aligned, `a_plus1`) and both cache modes. Every record was checked
by the independent [`asterbench_v8.py`](../../../scripts/asterbench_v8.py)
oracle, which recomputes all signed arithmetic, allocations, guards, both CPU
counter banks, the DMA and DOT8 banks and the NPU counters.

## Reproduce

```sh
make xe-matrix
python3 scripts/xe_study.py capture --output build/xe_study
python3 scripts/xe_study.py audit build/xe_study/study.json
```

`primary-summary.json` and `primary-ratios.csv` are derived from the retained
study. `scripts/xe_study.py audit` recomputes every row and ratio from the raw
records and rejects any disagreement.

## Best path by kernel and shape (aligned, cache on)

`scalar_over_method > 1` means the method beats the scalar baseline; a value
below 1 is a retained slowdown, not a discarded result.

| Kernel | Shape | Best path | scalar/method |
| --- | --- | --- | ---: |
| dot | 1×1×1 | scalar | 1.000 |
| dot | 1×1×4 | scalar | 1.000 |
| dot | 1×1×7 | scalar | 1.000 |
| dot | 1×1×16 | dot8 | 1.090 |
| dot | 1×1×64 | dot8 | 1.653 |
| dot | 1×1×256 | dot8 | 2.199 |
| dot | 1×1×1024 | npu | 2.778 |
| fir | 8×1×4 | multicore | 1.174 |
| fir | 8×1×16 | multicore | 1.508 |
| fir | 8×1×64 | multicore | 1.790 |
| fir | 8×1×256 | multicore | 1.920 |
| gemm | 1×1×1 | scalar | 1.000 |
| gemm | 1×3×4 | scalar | 1.000 |
| gemm | 3×5×8 | npu | 2.546 |
| gemm | 4×4×16 | npu | 4.179 |
| gemm | 8×8×31 | npu | 9.465 |
| gemm | 16×16×64 | npu | 13.269 |
| gemm | 32×32×32 | npu | 12.106 |

## Observations

- **Placement is a real cost.** For dot, the scalar path is fastest until
  `K ≈ 16`; DOT8 overtakes it from `K = 16` and peaks near 2.2×; the NPU only
  wins at `K = 1024`, where its setup and descriptor overhead are amortized.
- **FIR favors the CPU paths.** Two coherent harts scale to about 1.9×; DOT8
  reaches about 1.7×. The NPU FIR path must materialize a `TAPS × K` Toeplitz
  input (the descriptor requires `A_STRIDE >= K`), so it falls behind as `K`
  grows and is the slowest path at `K = 256`.
- **GEMM is where the accelerator pays.** The NPU wins from `3×5×8` upward and
  reaches 13.3× at `16×16×64`; two coherent harts scale to roughly 2×, and DOT8
  is competitive only at small `K`. These are simulated cycle ratios at the
  configured 31.25 MHz fabric clock, not physical speedups.
- Values below one (for example, multicore and NPU on tiny dot/GEMM shapes) are
  retained slowdowns caused by dispatch/join and descriptor/setup overhead.

## Fresh repeats

Two independently fresh captures of the complete 288-capture plan reproduce the
primary study exactly: `scripts/xe_study.py compare` reports identical rows and
ratios for both repeats, and `scripts/xe_study.py audit` re-validates every
record. Because the firmware, seeds and event-level simulator are deterministic,
the retained cycle counts and ratio rows are bit-identical across the three
independent runs.

## FPGA resource and clock results

See [resources.md](resources.md). The full all-engine image uses 25,121 LUTs
(47.2%), 19,198 FFs (18.0%), 32 BRAM tiles (22.9%) and 24 DSPs (10.9%), with
WNS 4.177 ns / WHS 0.051 ns at 31.25 MHz (implied maximum ≈ 35.9 MHz), zero
failing endpoints, zero routing errors and zero DRC/methodology findings. The
per-engine marginal synthesis cost is DMA +1,488 LUT, Xasterdot8 +1,220 LUT and
NPU +4,543 LUT / +24 DSP.

## Physical acceptance

See [physical/](physical/README.md). Six captures ran on the Pynq-Z1 at
31.25 MHz through the all-engine overlay, each validated by the independent v8
oracle on the board. Physical GEMM 4×4×16 ratios (scalar 27,821 cycles):
multicore 1.628×, dot8 1.476×, NPU 3.974× — closely matching simulation.

## Provenance and limitations

Each simulation capture was produced by `scripts/xe_study.py`; each physical
capture by `scripts/run_xe_mem.py`. The simulation cycles are the
RTL-configured fabric clock; the FPGA numbers in [resources.md](resources.md)
are real post-synthesis/post-route Vivado results and the physical numbers are
real 31.25 MHz silicon measurements. The physical transport is the FPGA UART
transmitter-to-receiver serial loopback read over AXI/Linux, not an external
Pmod electrical-loopback test.
