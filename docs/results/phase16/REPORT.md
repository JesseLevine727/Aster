# Aster: an open heterogeneous RISC-V SoC from RTL to a SKY130 layout flow

**Final report — Phase 16 (v1.3 full ASIC implementation)**

> **Review status:** this historical report documents a completed physical-design
> run, not clean ASIC signoff. The recorded run uses a reduced memory cut and has
> worst-corner timing, routing DRC, LVS, and Tier 2 gaps. Its performance and
> energy comparisons are provisional because the workload records do not all use
> the ASIC top/configuration and some counter/power derivations need correction.
> See [Phase 16 status](../../phase16.md) and the [Phase 17+ re-baseline plan](../../phase17-plus.md).
>
> **Phase 17 corrections (P17-C).** Several figures in this document are
> superseded — timing per corner, Fmax, power corner, LVS, electrical
> violations, Tier 2 coverage, and the mixed-top engine ratios. The reconciled
> values are in [`docs/phase16.md` Results](../../phase16.md#results); the
> same-top engine comparison is the [Phase 17 baseline](../phase17/baseline-56067a15815a/README.md).

Aster is an open, from-scratch heterogeneous RISC-V system-on-chip built to answer
one question: *when should a workload run on a scalar CPU, across multiple coherent
cores, through an ISA-level accelerator, or on a dedicated hardware accelerator?*
It is implemented as a frozen v1.3 architecture, verified at RTL and on an FPGA,
and taken through the complete SKY130 RTL-to-GDSII physical-design flow.

## 1. Architecture

Aster v1.3 (`rtl/`, frozen at `b3954ce`) integrates:

- **2 × PicoRV32 RV32IMA harts** with coherent L1 data caches and an atomic
  (LR/SC + AMO) fabric;
- a **coherent DMA engine**, a machine timer and a per-hart interrupt controller;
- **XasterDOT8**, a packed-INT8 ISA-level GEMM instruction, and a **4×4 INT8 NPU**
  accelerator with its own request/status interface;
- an optional memory-side L2 (default off);
- a unified ROM/RAM memory map and a performance-counter block (CPU ABI 4).

The interfaces are frozen and machine-checked: `make freeze-interfaces` verifies
23 v1.0 interfaces, and `make check` runs **201 host/Verilator tests**.

## 2. Three-tier verification

| Tier | Method | Scope | Result |
|------|--------|-------|--------|
| 1 | RTL simulation | full AsterBench v10 catalog (12 workloads) + phase experiments | **all pass** |
| 2 | Post-layout gate-level, SDF-annotated | routed SKY130 netlist, one oracle per compute path | **4 of 8 mandatory pass** |
| 3 | Static signoff | whole chip: STA, RCX/SPEF, antenna, DRC, LVS | **DRC 0/0, antenna 0**; documented residuals |

### Tier 1 — AsterBench v10 (RTL)

All twelve catalog workloads produce reproducible records (cycles, retired
instructions, memory transactions, cache accesses/misses, DMA bytes, accelerator
cycles): `strided`, `sort_search`, `coremark`, `dhrystone`, `fft`, `conv2d`,
`conv2d_dot8`, `conv2d_npu`, `reduce_scalar`, `reduce_parallel`, `streaming_ecg`,
`cifar_cnn`.

### Tier 2 — gate-level SDF equivalence

The routed `aster_v1_asic` netlist is simulated with the `nom_tt_025C_1v80` SDF.
Passing: `reduce_scalar`, `reduce_parallel`, `conv2d_scalar_coh`, `conv2d_npu`.
The two-hart `reduce_parallel` result is **232,958 cycles — identical to the
physical PYNQ-Z1 board measurement**, closing the loop across RTL, gates and
the FPGA board.

## 3. The headline result: cross-engine equivalence

The same signed-INT8 2D convolution is executed on three engines and must agree
bit-for-bit. It does:

| Engine | 32×32, K=5 (RTL + FPGA) | 16×16, K=3 (gate level) |
|--------|:-----------------------:|:-----------------------:|
| scalar CPU | `0x07df8000` | `0xb4ad9800` |
| XasterDOT8 (ISA ext.) | `0x07df8000` | (RTL) `0xb4ad9800` |
| 4×4 NPU | `0x07df8000` | `0xb4ad9800` |

The scalar CPU and the dedicated NPU produce **identical checksums on the routed
SKY130 netlist**, and all three agree at RTL.

### And the counterintuitive ranking

| Engine | Cycles (32×32) | vs scalar |
|--------|---------------:|----------:|
| NPU (4×4) | 4,637,733 | **1.24× faster** |
| scalar CPU | 5,764,692 | — |
| XasterDOT8 | 9,786,108 | **1.70× slower** |

For this shape the **custom ISA instruction loses to the plain scalar CPU**,
because im2col feed cost dominates at a 4×4 tile; the dedicated NPU is the only
engine that pays off. The ranking is **identical on FPGA and SKY130** — the
architectural conclusion is node-independent.

## 4. Metrics and PPA

### ASIC (SKY130, LibreLane 3.0, `runs/p16-f2`)

| Metric | Value |
|--------|-------|
| Core area | 7.11 mm² (2.56 mm² stdcell + 4.55 mm² SRAM) |
| Die | 20 mm² (16 OpenRAM macros; documented 16 KiB ROM/RAM cut) |
| Signoff clock | 47 ns → **21.3 MHz** (`max_ss`); ~43 MHz `nom_tt` |
| Setup WNS `nom_tt` / `nom_ss` | +10.74 / +0.35 ns ✅ |
| Power (TT, 1.80 V) | **70.1 mW** |
| Magic / KLayout DRC | **0 / 0** ✅ |
| Antenna | **0** ✅ |
| Route (TritonRoute) DRC | 106 ❌ |
| LVS | 13 (macro `vccd1`/`vssd1` power-pin short) ❌ |

### FPGA (Pynq-Z1, v1.3)

| Metric | Value |
|--------|-------|
| Clock | 50 MHz |
| Resources | 25,628 LUT (48.2%), 19,794 FF, 32 BRAM (22.9%), 24 DSP (10.9%) |
| Power (Vivado `report_power`) | 1.767 W total — 1.525 W PS7 + 0.145 W static + **~0.097 W Aster PL** |

### Throughput and energy (conv2d, whole-chip at signoff clock)

| Engine | GOPS | TOPS/W | nJ/MAC |
|--------|-----:|-------:|-------:|
| scalar CPU | 5.79e-4 | 8.26e-6 | 242.0 |
| XasterDOT8 | 3.41e-4 | 4.87e-6 | 410.8 |
| NPU (4×4) | 7.20e-4 | 1.03e-5 | **194.6** |

(AsterBench records microarchitectural counters; GOPS/TOPS/W and nJ/MAC are
derived from the known MAC count, the measured cycles and the OpenROAD power.
The absolute numbers are modest because the whole 7.11 mm² chip runs at the
21 MHz signoff clock; the accelerator's own `accelerator_cycles` are a small
fraction of the workload.)

## 5. Residuals and Phase 17 handoff

The following are **not** met and are documented, not hidden:

1. **`max_ss` setup −1.15 ns** and **hold −0.17 ns** (`max_ff`). The critical
   paths are a high-fanout register (`_237896_/Q` → ~20 endpoints) and the SRAM
   read (`dout0[5]`). Fixing them needs the fabric/memory paths **pipelined** —
   an RTL architecture change, i.e. Phase 17.
2. **Route DRC 106** and **LVS 13** (macro power-pin short). Two cleanup runs
   (hold-margin; density/DRT) both regressed — `p16-f2` is a local optimum. The
   LVS is a PDN/macro-abutment geometry issue.
3. **Tier 2 is 4/8 mandatory.** The remaining four either exceed the 16 KiB
   memory cut (`cifar_cnn`, `phase11-infer`) or the gate-level time budget
   (`conv2d_dot8` at >4.5 M cycles, `fft` on a different netlist).
4. **64 KiB frozen memory map** was unroutable; the 16 KiB cut is what makes the
   design close.

Closing (1)–(4) is a **v2 architecture revision**: pipeline the fabric/NPU/memory,
restore the full memory map, and re-run. Fabrication (tapeout) remains an
explicit stretch goal and is not required for success.

## 6. Reproducibility

Every claim is backed by a hash-bound artifact: the Phase 16 closeout bundle
(`docs/results/phase16/closeout-5b1d0d8d9d6d/`) with `audit_phase16.py` PASS,
the PPA analysis (`docs/results/phase16/ppa/`), the physical-cleanup record
(`docs/results/phase16/physical-cleanup/`), the Tier 2 harness and results
(`docs/results/phase16/tier2-gate-level/`), and the frozen-source audit
(`make freeze-interfaces`, 23 interfaces). Heavy signoff steps are serialised
and cgroup-capped via `scripts/memguard.sh` (`docs/memory.md`).
