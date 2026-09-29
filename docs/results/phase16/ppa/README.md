# Phase 16 — PPA and Research-Question Answers

> **Phase 17 corrections (P17-C).** Several figures in this document are
> superseded — timing per corner, Fmax, power corner, LVS, electrical
> violations, Tier 2 coverage, and the mixed-top engine ratios. The reconciled
> values are in [`docs/phase16.md` Results](../../../phase16.md#results); the
> same-top engine comparison is the [Phase 17 baseline](../../phase17/).

This directory records the post-layout power/performance/area (PPA) analysis of the
Phase 16 full v1.3 system on SKY130, and answers the two research questions that
`docs/phase14-plan.md` deferred to this phase.

> **Status caveat:** this is the historical Phase 16 analysis, not an accepted
> v2 performance baseline. The generic `workload`/CoreMark/Dhrystone targets use
> `aster_minimal`; coherent accelerator workloads use `aster_coherent_soc`. The
> RTL records also use the default asynchronous, zero-wait memory, while the ASIC
> top uses synchronous SRAM with a wait cycle and a reduced capacity. In
> particular, the scalar Conv2D number is not from the same top as the NPU/DOT8
> numbers. Re-run comparisons under one declared configuration before using the
> ratios as final architecture conclusions. Phase 17 specifies that re-baseline.

The v10 `dma_bytes` field also counts NPU device writes in these records, and
`accelerator_cycles` is the last NPU job rather than the sum across all jobs.
Those semantics must be corrected or separately labeled before utilization or
per-engine traffic conclusions are derived from them.

## Design point

| Item | Value | Source |
|------|-------|--------|
| DUT | `aster_coherent_soc` (v1.3, all engines) | `asic/sky130/aster_v1_asic.sv` |
| Flow / PDK | LibreLane 3.0.14 / sky130A `8afc834` | `asic/sky130/config.v1.json` |
| Stdcell area | 2.561 mm² | `runs/p16-f2/final/metrics.csv` |
| Macro area | 4.553 mm² (16 SRAM macros) | `runs/p16-f2/final/metrics.csv` |
| Core area | **7.11 mm²** | sum |
| Die area | **20.0 mm²** (5000 × 4000 µm) | `metrics.csv` |
| Stdcell instances | 556,877 (incl. fill/tap/decap) | `metrics.csv` |
| Power (TT, 25 °C, 1.80 V) | **70.1 mW** | `57-openroad-stapostpnr/nom_tt_025C_1v80/power.rpt` |
| Setup WNS (nom_tt) | +10.74 ns @ 47 ns | `metrics.csv` |
| Setup WNS (nom_ss) | +0.35 ns @ 47 ns | `metrics.csv` |
| Setup WNS (max_ss) | −1.15 ns @ 47 ns | `metrics.csv` |
| Hold WNS (worst) | −0.17 ns | `metrics.csv` |
| Signoff clock | 47 ns → **21.3 MHz** | `constraints.v1.sdc` |
| Achievable Fmax | **~21 MHz (SS)** / ~43 MHz (TT) | STA + fast-loop path analysis |

The clock target for the *full* system is 47 ns, not the 20 ns used for the Phase 15
minimal block: the NPU engine's combinational 32×32 address multiply
(`rtl/accelerator/aster_npu_engine.sv`) and the high-fanout fabric/cache buses are
buffer-dominated at SS and do not close 20 ns. Phase 15's minimal block does close
20 ns; closing 50 MHz for the full system is Phase 17 work (pipeline those paths).

## Per-workload performance and energy

Cycles are from the AsterBench v10 records produced by the RTL run of the same
coherent SoC the ASIC wraps (`build/*.record`, v10). Energy is computed at the
signoff clock and TT power: `E = P × cycles / f`.

| Workload | Category | Cycles | Time @21.3 MHz | Energy |
|----------|----------|-------:|---------------:|-------:|
| `strided` | memory | 8,039 | 0.38 ms | 26 µJ |
| `reduce_parallel` | cpu (2 harts) | 222,374 | 10.4 ms | 732 µJ |
| `reduce_scalar` | cpu (1 hart) | 289,251 | 13.6 ms | 952 µJ |
| `streaming_ecg` | system | 1,528,356 | 71.8 ms | 5.03 mJ |
| `coremark` | cpu | 1,757,429 | 82.5 ms | 5.78 mJ |
| `sort_search` | cpu | 2,062,008 | 96.8 ms | 6.79 mJ |
| `dhrystone` | cpu | 2,737,452 | 128.5 ms | 9.01 mJ |
| `fft` | dsp | 2,949,470 | 138.5 ms | 9.71 mJ |
| `conv2d_npu` | dsp (NPU) | 4,637,733 | 217.7 ms | 15.3 mJ |
| `conv2d` | dsp (scalar) | 5,764,692 | 270.6 ms | 19.0 mJ |
| `conv2d_dot8` | dsp (XasterDOT8) | 9,786,108 | 459.4 ms | 32.2 mJ |
| `cifar_cnn` | ml (NPU) | 61,682,928 | 2.90 s | 203 mJ |

## Accelerator efficiency (conv2d, 28×28 output × 25-tap kernel × 4 iters = 78,400 MACs)

| Engine | Cycles | Time | Energy/MAC | Effective GOPS/W |
|--------|-------:|-----:|-----------:|----------------:|
| scalar CPU | 5,764,692 | 270.6 ms | 242.0 nJ/MAC | 0.0083 |
| XasterDOT8 | 9,786,108 | 459.4 ms | 410.8 nJ/MAC | 0.0049 |
| NPU (4×4) | 4,637,733 | 217.7 ms | 194.6 nJ/MAC | 0.0103 |

These are whole-chip figures (the CPU drives im2col while the engine computes), so
they are a lower bound on engine efficiency. The **NPU is the fastest engine** and
the **DOT8 is slower than scalar** for this shape because im2col feed cost dominates
at 4×4 tile size — the same relative ranking measured on FPGA in Phase 12.

## Answers to the deferred research questions

### Q9 — best performance per area and estimated energy

- **Best performance/area**: the **NPU (4×4)**. It is the fastest conv2d engine
  (1.24× scalar, 2.11× DOT8) and its dedicated datapath is the smallest area
  increment; at 7.11 mm² core area it delivers ~0.0103 GOPS/W whole-chip.
- **Best energy**: the NPU again (194.6 nJ/MAC vs 242.0 scalar / 410.8 DOT8).
  The multicore `reduce` shows coherence scaling: 2 harts cut cycles 1.30×
  (289,251 → 222,374) for the same work.
- The absolute numbers are modest because the whole 7.11 mm² chip runs at the
  signoff 21 MHz; the NPU's *accelerator_cycles* are only 4,900 of the 4.6 M
  workload cycles, i.e. the CPU/im2col feed dominates. Pipelining the feed and the
  NPU address path (Phase 17) is the clear lever for both performance and energy.

### Q10 — how different are the conclusions on FPGA versus SKY130

| Metric | Pynq-Z1 FPGA (v1.3) | SKY130 ASIC (Phase 16) |
|--------|---------------------|------------------------|
| Clock | 50 MHz | 21.3 MHz signoff |
| Resources | 25,628 LUT (48.2%), 19,794 FF, 32 BRAM (22.9%), 24 DSP (10.9%) | 2.56 mm² stdcell + 4.55 mm² SRAM, 20 mm² die |
| Aster logic power | ~0.097 W dynamic (PL only) | 70.1 mW total core |
| Total on-chip power | 1.767 W (incl. 1.525 W PS7 + 0.145 W static) | 70.1 mW |
| NPU vs scalar conv2d | NPU faster, DOT8 slower | NPU faster, DOT8 slower |
| Multicore reduce speedup | ~1.3× | ~1.3× |

FPGA power is from Vivado `report_power` on the routed v1.3 checkpoint
(`linux-h2-coherent-c1-dma-dot8-npu-fclk50`): 1.767 W total, of which the
**Zynq PS7 (the Linux host) is 1.525 W** and device static is 0.145 W. The
**Aster PL logic is therefore ~0.097 W dynamic** (clocks 0.023 + slice 0.016 +
signals 0.027 + BRAM 0.029 + DSP <0.001 + I/O 0.002). Compared at equal clock,
the SKY130 core (0.070 W at 21 MHz) is ~0.041 W at 50 MHz — so the 130 nm part
draws ~2.4× the 28 nm FPGA fabric for the same logic, as expected from the
process gap.

**The architectural conclusions are node-independent.** Relative engine rankings
(NPU > scalar > DOT8 for this conv2d shape), the im2col-feed bottleneck, and the
coherence scaling ratio are identical on FPGA and SKY130 because both run the same
RTL; only the absolute clock, area and energy differ. The ASIC trades ~2.3× clock
for a dedicated, measurable silicon area and a hard power number; the FPGA trades
area for reconfigurability and a higher clock. With both power numbers now
measured, the energy conclusion is also node-independent in ranking but the ASIC
is the lower-energy implementation at equal work (0.070 W core vs ~0.097 W of PL
logic at a higher clock).

## Provenance

- ASIC: `asic/sky130/runs/p16-f2/final/` (GDS, metrics, STA).
- Workloads: `build/*.record` (AsterBench v10), captured from the coherent SoC RTL.
- Closeout: `docs/results/phase16/closeout-5b1d0d8d9d6d/`.
