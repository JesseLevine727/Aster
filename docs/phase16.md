# Phase 16: Full ASIC implementation and PPA

Status: **incomplete — 4 of 11 contract gates pass** (Phase 17 audit). The
`p16-f2` flow produced a GDS and post-route evidence for a reduced 16 KiB ROM /
16 KiB RAM cut. `scripts/audit_phase16.py` now evaluates every gate from the
retained evidence; it fails LVS, all-corner setup and hold, Tier 1 on the ASIC
configuration, Tier 2, SPEF binding, and the frozen 64 KiB + 64 KiB memory map.
See [Results](#results).
Baseline: pushed v1.3 closeout `b3954ce`, plus the Phase 15 SKY130 flow
infrastructure (`ba6c760`).
The [README roadmap](../README.md#phase-16--full-asic-implementation-and-ppa)
defines this phase as moving the frozen architecture through SKY130/OpenROAD and
collecting post-synthesis/post-layout area, timing and estimated power. This
document is the Phase 16 contract.

## Goal and principles

- **Implement the frozen v1.3 system, not a new design.** Phase 16 changes no
  address, register, ABI or instruction. The only intended differences are
  physical: the die, the memory macros and the achieved clock.
- **Reuse the Phase 15 flow.** `scripts/run_asic.py` (sv2v + LibreLane),
  `scripts/run_asic_gl_sim.py` (SDF gate-level simulation), the closeout builder
  and the audit all carry forward. Phase 15 proved the flow; Phase 16 scales it.
- **Measure, don't claim.** Area, Fmax and power come from the flow's reports,
  bound into the closeout bundle. Derived metrics (performance/mm², energy/op,
  GOPS/W) are computed from those and the AsterBench records.
- **Correctness before PPA.** A configuration that does not reproduce its
  AsterBench oracle in the gate-level simulation is not a valid result.

## Frozen decisions (proposed)

| Decision | Choice |
| --- | --- |
| Design | **Aster v1.3** all-engine coherent system (`aster_coherent_soc`) |
| Flow | **LibreLane 3.0** (current), pinned by container digest (as Phase 15) |
| PDK | `sky130A`, standard cells `sky130_fd_sc_hd` |
| RAM | `sky130_sram_2kbyte_1rw1r_32x512_8` macros, full v1.0 map (64 KiB) |
| ROM | SRAM macros loaded through the host boot port, full v1.0 map (64 KiB) |
| Harts | 2 (`HART_COUNT = 2`) |
| L1 | enabled, coherent D$ (`ENABLE_L1 = 1`) |
| DMA / DOT8 / NPU / IRQ | all enabled (`ENABLE_DMA/DOT8/NPU/IRQ = 1`) |
| NPU geometry | 4×4 default (`NPU_ROWS = NPU_COLS = 4`) |
| L2 | **off** (`ENABLE_L2 = 0`) by default; on as an optional PPA axis |
| Timing target | **20 ns (50 MHz) — the goal of the phase**, matching v1.3 |
| Host interface | the boot/stop/perf ports of `aster_coherent_soc`, not the Zynq AXI shell |

### Frozen decisions

1. **Memory sizing.** Start with the full v1.0 map: 64 KiB ROM, 32 KiB shared
   RAM and 16 KiB private per hart (up to ~64 OpenRAM macros). Milestone 3's
   floorplan is the feasibility gate; if placement/routing is infeasible the
   contract is amended with a documented reduced cut rather than silently
   shrinking the map.
2. **ROM mechanism.** Host-boot SRAM, loaded before RUN through the existing
   `HOST_BOOT` path. A mask-ROM variant is a stretch goal, not a gate.
3. **Clock.** **20 ns (50 MHz) is the target and the gate.** If the full
   critical path (NPU `rows`→engine, cache flush scan, coherence fabric) cannot
   close, the phase records the achieved period/Fmax and the contract is
   amended with an agreed fallback rather than quietly relaxing the constraint.
4. **PPA axes.** The default image (2 harts, L2 off, 4×4 NPU) is mandatory.
   `ENABLE_L2` on/off and NPU geometry (2×2/4×4/8×8) sweeps run as PPA axes if
   the schedule allows; they do not change the default signoff image.

## Configuration

The DUT is `aster_coherent_soc` wrapped in a new ASIC top
(`asic/sky130/aster_v1_asic.sv`) that exposes the board-facing surface: `clk`,
`resetn`, `host_run`, `stopped`/`stop_busy`, `uart_tx`/`uart_rx`, the host boot
port and the performance/retirement observation ports. The Linux/Zynq AXI shell
(`aster_pynq_linux`, `aster_linux_ip`) stays an FPGA concern.

`MEM_INIT_FILE` and `HOST_BOOT` are set so the same firmware oracle used in
Phase 1/2 and v1.3 (for example `reduce_parallel`) can be loaded and run.

## Flow and tooling

The stock LibreLane Classic flow from Phase 15:

```text
sv2v -> synthesis (Yosys) -> floorplan -> placement -> CTS -> routing -> STA
     -> RCX/SPEF -> DRC (Magic/KLayout) -> LVS (Netgen) -> GDSII
```

plus the Phase 15 post-layout SDF gate-level simulation. The Phase 15 lessons
apply: exclude `sky130_fd_sc_hd__clkdlybuf4s*` from design repair, enable the
post-global-route resizer, keep the ROM combinational (or boot-loaded), and run
the gate-level simulator with the `FUNCTIONAL` models and `-gspecify`.

## Verification strategy

Phase 16 verifies in three tiers. The **architecture** is proved by the RTL
suite; the **physical netlist** is proved by a per-engine gate-level slice; the
**manufacturability** is proved by static signoff. Every workload is measured;
only a representative subset is simulated at gate level.

### Tier 1 — full workload suite at RTL (all workloads)

Run the complete AsterBench v10 catalog and the phase experiments against the
ASIC-parameterised v1.3 RTL. This is the architectural oracle and the source of
the per-workload cycle/counter data used for the PPA table.

| Workload | Category | Engine(s) |
| --- | --- | --- |
| `strided`, `sort_search` | memory/cpu | CPU |
| `coremark`, `dhrystone` | cpu | CPU |
| `fft`, `conv2d` | dsp | CPU |
| `conv2d_dot8` | dsp | Xasterdot8 |
| `conv2d_npu` | dsp | NPU |
| `reduce_scalar`, `reduce_parallel` | cpu | 1 / 2 harts |
| `streaming_ecg` | system | CPU + DMA + DOT8 + NPU |
| `cifar_cnn` | ml | NPU |
| `phase11-infer` (MNIST) | ml | NPU |

Tier 1 is `make check` plus the workload targets; it is fast because it is RTL,
not gates.

### Tier 2 — gate-level SDF equivalence (named set)

Post-layout, back-annotated SDF simulation of the routed netlist on **one
oracle per compute path**. This proves synthesis/place/CTS/route did not change
behaviour; it does not re-prove the architecture. The mandatory set is:

| Workload | Path proved | Approx. cycles |
| --- | --- | ---: |
| `reduce_parallel` | scalar CPU + 2-hart coherence + perf counters + L1 | ~233 k |
| `coremark` | CPU core (fixed-iteration CoreMark CRC check, not a CoreMark score) | large |
| `fft` | CPU fixed-point DSP | small |
| `conv2d_dot8` | Xasterdot8 packed-INT8 ISA path | small |
| `conv2d_npu` | 4×4 NPU accelerator path (cross-engine equality) | ~5 M |
| `streaming_ecg` | DMA + heterogeneous CPU+DMA+DOT8+NPU system path | ~1.5 M |
| `cifar_cnn` | NPU ML inference path | large |
| `phase11-infer` (MNIST) | NPU quantized-ML inference path | large |

`conv2d`, `conv2d_dot8` and `conv2d_npu` share an output, so the gate-level
checksums must match across the three, which is the cross-engine equivalence
check. If `streaming_ecg` proves too slow in gate-level simulation, the DMA path
falls back to a smaller DMA-copied kernel and the substitution is recorded.

All eight workloads above are mandatory. Gate-level simulation is slow, so the
runs are parallelised and cached; the only permitted substitution is a
documented, equivalent smaller input for a workload whose full-size run does not
fit the simulation budget.

### Tier 3 — static signoff (whole design)

Synthesis checks, STA across every RC corner, RCX/SPEF, antenna, Magic/KLayout
DRC, Netgen LVS and `make freeze-interfaces` run on the entire chip with no
workload dependence.

### PPA

Area from synthesis and layout, Fmax from STA, estimated power from OpenROAD,
and per-workload cycle/counter data from Tier 1, combined into performance/mm²,
energy/op and GOPS/W.

## Deliverables

```text
asic/sky130/
  aster_v1_asic.sv          # thin top binding aster_coherent_soc to the pad ring
  config.v1.json            # LibreLane configuration for the full system
  constraints.v1.sdc        # frozen period, IO delays
  macro_placement.v1.cfg    # SRAM/ROM macro placement
  rom/                      # boot image(s) for the oracle workloads
scripts/
  run_asic.py               # unchanged driver, new config
  run_asic_gl_sim.py        # unchanged gate-level driver
  audit_phase16.py          # read-only closeout audit
docs/
  phase16.md                # this contract
  results/phase16/closeout-<rev>/   # self-contained evidence bundle
```

## Verification and acceptance gates

- [ ] Full v1.3 configuration frozen and documented; `make check` still green.
- [ ] The v1.3 interfaces are unchanged: `make freeze-interfaces` still green
      and no address, register, ABI or instruction differs.
- [ ] Yosys synthesis completes with no unmapped cells and no inferred latches.
- [ ] The flow reaches GDSII with zero DRC violations (Magic and KLayout) and a
      clean Netgen LVS.
- [ ] Antenna checks pass.
- [ ] Static timing analysis meets the frozen constraint (WNS ≥ 0) at every RC
      corner; the achieved Fmax is recorded.
- [ ] The full AsterBench v10 catalog and the phase experiments pass at RTL on
      the ASIC-parameterised configuration (Tier 1).
- [ ] Post-layout gate-level simulation with back-annotated SDF reproduces the
      oracle for every mandatory Tier 2 workload — `reduce_parallel`,
      `coremark`, `fft`, `conv2d_dot8`, `conv2d_npu`, `streaming_ecg`,
      `cifar_cnn`, `phase11-infer` — with matching cross-engine checksums.
- [ ] Area, Fmax and estimated power are recorded, and the derived PPA metrics
      are computed from them.
- [ ] The GDS, DEF, SPEF, timing/DRC/LVS reports and the tool/PDK identities are
      hash-bound and reproducible from a clean checkout.
- [ ] Self-contained closeout bundle and read-only audit (`audit_phase16.py`).

## Results

The v1.3 all-engine coherent RTL was taken through LibreLane 3.0 on SKY130 using
a **reduced 16 KiB ROM / 16 KiB RAM physical cut**. This proves that the flow can
produce a routed layout; it does not complete the original Phase 16 acceptance
contract. The 47 ns signoff point is about 21 MHz, and the worst `max_ss` setup
corner still fails. The full 64 KiB ROM / 64 KiB RAM configuration did not route
within the selected 20 mm² die. A 50 MHz or 100 MHz SKY130 target therefore
requires a redesigned/timed architecture and an explicit memory/die budget, not
an extrapolation from this run.

| Metric | Value |
| --- | --- |
| Die | 20 mm² (5000×4000 µm), 16 OpenRAM macros (reduced from the 64 KiB map) |
| Cells | 2.56 mm² stdcells, 4.55 mm² macros |
| Setup WNS `nom_tt` | **+10.74 ns** ✅ |
| Setup WNS `nom_ss` | **+0.35 ns** ✅ |
| Setup WNS `max_ss` (worst RC) | −1.15 ns ❌ (needs ~49 ns) |
| Hold WNS | −0.171 ns ❌ — fails at `nom_ff` (−0.026), `max_tt` (−0.133), and `max_ff` (−0.171) |
| Magic / KLayout DRC | **0 / 0** ✅ |
| Antenna | **0** ✅ |
| Route (TritonRoute) DRC | 106 ❌ |
| Max slew / max capacitance (`max_ss`) | 85,996 / 5,443 violations ❌ (the flow's `design__violations` summary reports 0) |
| LVS | fails ❌ — netlists do not match (device and net counts differ); the top-level `vccd1` pin resolves to the `vssd1` node and the SRAM macro supply is disconnected |
| Power (vectorless, 47 ns clock) | **62.1 mW** at `nom_tt`; the 70.1 mW `power__total` is the `max_ff` corner |
| STA Fmax at the 47 ns SDC | `nom_tt` 40.96 MHz, `nom_ss` 22.12 MHz, `max_ss` 20.77 MHz (fails at 47 ns) |

The flow used an **over-constrained 20 ns PnR SDC** and a **47 ns signoff SDC**.
The reduced memory cut (16 KiB ROM/RAM, 16 macros) is what made this run
routable. Remaining failures include `max_ss` setup, worst-corner hold, 106
TritonRoute DRC errors, and 13 LVS errors. Magic/KLayout DRC and antenna checks
are clean, but that does not make the result signoff-clean. The table above was
reconciled in Phase 17 (P17-C) from the retained `p16-f2` STA and power reports.
Earlier documents quoted "~43 MHz TT" (from a different run), "108 MHz" (the
closeout script subtracted the slack from 20 ns although STA ran at 47 ns),
"21.3 MHz signoff" (1/47 ns, although `max_ss` fails at that period) and
"70.1 mW TT" (the `max_ff` corner); those figures are superseded. The
perf-counter carry split is retained as a source change, not evidence that the
physical gates passed.

### PPA and research questions

The figures below are historical Phase 16 analysis, **not an accepted v2
performance baseline**. Workload records were not all captured on the ASIC top
with its synchronous SRAM and reduced memory capacity; the saved Fmax and power
derivations also need reconciliation. Phase 17 defines the re-baseline.

`docs/results/phase16/ppa/README.md` records the post-layout PPA and answers the
two research questions deferred by `docs/phase14-plan.md`. Headline: 7.11 mm²
core (20 mm² die), 21.3 MHz signoff, 70.1 mW; the 4×4 NPU is the best conv2d
engine (1.24× scalar, 2.11× Xasterdot8; 194.6 nJ/MAC whole-chip), and the
architectural rankings are node-independent versus the FPGA.

**Phase 17 correction.** Those headline figures are superseded. 7.11 mm² is the
instance area (2.56 mm² standard cells + 4.55 mm² macros), not a core area;
21.3 MHz and 70.1 mW are corrected in the table above; the 1.24×/2.11× ratios
compared a scalar run on `aster_minimal` with DOT8/NPU on the coherent top (on
one top the NPU is 2.06× and DOT8 0.96× — see the
[Phase 17 baseline](results/phase17/baseline-56067a15815a/README.md)); energy per MAC divided one vectorless
whole-chip power figure among workloads; the Tier 1 cycle counts were RTL runs
with zero-wait memory and the full 64 KiB map, not the ASIC's synchronous
memory and 16 KiB cut; and the "node-independent ranking" is circular because
the FPGA and SKY130 columns used the same RTL simulation cycles.

### FPGA power and energy comparison

Vivado `report_power` on the routed v1.3 checkpoint gives **1.767 W total**, of
which the Zynq PS7 (the Linux host) is 1.525 W and device static is 0.145 W. The
**Aster PL logic is ~0.097 W dynamic**. The comparison drawn from it earlier is
withdrawn: dynamic power rises with clock frequency, so scaling 70.1 mW at 21 MHz
*down* to ~41 mW at 50 MHz inverts the relationship; 70.1 mW is also the
`max_ff` corner; and the Vivado power report itself is not retained in the
repository.

### Physical-cleanup attempt

`docs/results/phase16/physical-cleanup/README.md` records a hold-margin cleanup
run (`p16-clean`). It fixes the hold residual (+0.239 ns at `max_ff`) but
regresses setup (−1.62 ns at `max_ss`) and routing (240 DRC), confirming the
remaining residuals are architectural (NPU/fabric paths need pipelining) rather
than a flow-tuning problem. `p16-f2` remains the balanced signoff point.

### Host memory and OOM containment

The full-chip signoff is memory-heavy (`magic-writelef` peaks at 32-54 GiB,
`klayout-drc` at 25 GiB) and the gate-level sims add ~10 GiB. Run the heavy jobs
through `scripts/memguard.sh` (or `scripts/run_asic.py`, which wraps it by
default): it serialises them with a `flock` and caps each in its own cgroup.
`docs/memory.md` has the full diagnosis, the `systemd-oomd` hardening and the
swap recommendation.

### Tier 2 gate-level simulation

`docs/results/phase16/tier2-gate-level/README.md` records the post-layout
gate-level harness and results. The immutable closeout log contains passing
`reduce_scalar` and `reduce_parallel` runs. Later notes add reduced-shape
scalar/NPU Conv2D equivalence, but do not complete the eight-workload Tier 2
contract. Of the eight mandatory workloads, only `reduce_parallel` passed at
full size, and `conv2d_npu` passed only at a reduced 16×16/K=3 shape;
`reduce_scalar` and `conv2d_scalar_coh` also passed but are not in the mandatory
set. CoreMark and ECG timed out; DOT8 Conv2D did not finish; FFT, CIFAR, and
MNIST were not run (the 16 KiB cut excludes several full workloads). SDF
annotation is optional in the testbench command and no retained log
demonstrates it, and the testbench clock (20 ns) is faster than the `nom_tt`
minimum period (24.42 ns). Tier 2 therefore remains incomplete.

## Milestones

1. **Contract and configuration.** Freeze the open decisions and the DUT
   parameters; add the ASIC top and configuration.
2. **Memory integration.** SRAM/ROM macros, boot path and the oracle image.
3. **Synthesis and floorplan.** Fully mapped netlist, macro placement, die size.
4. **Placement, CTS and routing.** Resolve congestion; reach zero route DRC.
5. **Signoff.** STA across corners, RCX/SPEF, antenna, DRC and LVS.
6. **GDSII and post-layout simulation.** Stream out and re-verify the oracle on
   the extracted netlist with SDF.
7. **PPA and closeout.** Collect area/timing/power, compute derived metrics,
   bundle the evidence and audit it.

## Explicit non-goals

- **No architecture changes.** Phase 16 implements v1.3 as frozen; new features
  are a v2 change.
- **No tapeout.** Fabrication, packaging and board bring-up are the optional
  Phase 23 of the [v2 plan](phase17-plus.md) (originally numbered Phase 17).
- **No L2 default change.** The L2 stays off unless a sweep justifies it.
- **No analog/mixed-signal blocks** and no custom SRAM generation.
