# Phase 15: Learn SKY130 on a minimal configuration

Status: **complete** — all acceptance gates pass. The routed GDS, the signoff
SDF and the post-layout gate-level simulation are recorded under
`docs/results/phase15/`.
Baseline: pushed v1.3 closeout `5272f35`.
The [README roadmap](../README.md#phase-15--learn-sky130-on-a-minimal-configuration)
defines this phase as taking a tiny Aster configuration through the complete
open ASIC flow first:

```text
RTL -> Yosys -> SKY130 -> floorplan -> placement -> CTS -> routing -> STA -> DRC/LVS -> GDSII
```

Phase 15 adds **no new architecture**. It exists to learn the SKY130/OpenROAD
flow on a small, fully understood block before the frozen v1 system is attempted
in Phase 16. This document is the Phase 15 contract.

## Goal and principles

- **Smallest configuration that still executes real Aster firmware.** The
  milestone is a *complete, signed-off flow*, not a large design.
- **Reuse the verified RTL.** The Phase 15 netlist is the same `aster_minimal`
  SoC (single hart, ROM, RAM, UART, performance counters) that Phase 1/2
  verified in simulation and on the PYNQ-Z1, with smaller memories and no L1.
  No behaviour is re-specified.
- **Deterministic and reproducible.** The flow is pinned by container digest,
  the PDK by ciel revision, and the GDS/reports are hash-bound.
- **Correctness first, then the flow.** A block that does not pass its existing
  simulation oracle is not a valid ASIC milestone.

## Frozen decisions

| Decision | Choice |
| --- | --- |
| Flow | **LibreLane 3.0** (current), pinned by container digest |
| RAM | **OpenRAM SRAM macro** shipped with the PDK |
| ROM | synthesized logic (an SRAM macro is volatile and cannot hold firmware) |
| Block | single hart + ROM/RAM + UART + performance counters |
| Timing target | 20 ns (50 MHz) |

## Minimal configuration

| Knob | Value |
| --- | --- |
| Harts | 1 (`aster_hart`, PicoRV32 RV32IM) |
| L1 cache | disabled (`ENABLE_L1 = 0`) |
| ROM | 2 KiB (512 × 32), synthesized, `$readmemh`-initialized |
| RAM | 2 KiB = `sky130_sram_2kbyte_1rw1r_32x512_8` (512 × 32, byte masks) |
| Memory read | synchronous |
| Peripherals | UART + Phase 3 performance counters |
| Clock | single `clk`, active-low `rst_n` |
| Target period | 20 ns (50 MHz) — matching the v1.3 FPGA operating point |
| Standard cells | `sky130_fd_sc_hd` |
| PDK | `sky130A` (ciel `f3c505b`) |

`aster_minimal` currently hard-codes 64 KiB ROM/RAM in its address decode and
instantiates the memories at their default depth. Phase 15 adds
`ROM_WORDS`/`RAM_WORDS` parameters (default unchanged at 16 384) and derives the
decode from them, so the existing FPGA/simulation configurations stay
bit-identical.

### SRAM macro integration

The RAM is wrapped so the single-port `aster_ram` interface maps onto the
macro's `1rw` port:

- `csb0 = !select`, `web0 = !we`, `wmask0 = wstrb`, `addr0 = word_index`,
  `din0 = wdata`, read data from `dout0`;
- the second (`1r`) port is tied off (`csb1 = 1`);
- the wrapper is a black box to Yosys and carries the macro LEF/LIB/GDS into
  LibreLane as a macro instance with its own power pins (`VPWR`/`VGND`).

The macro is 683.1 × 416.54 µm (~0.28 mm²) and reads synchronously, matching
`aster_ram`'s `SYNC_READ = 1` mode. The ROM stays register-based so the boot
image is present at power-up.

## Flow and tooling

The flow runs **LibreLane 3.0** (the current OpenLane successor; released
March 2026) against the ciel-installed `sky130A` PDK, whose
`libs.tech/librelane/` configuration the release supports. The stages are the
stock Classic flow:

```text
synthesis (Yosys) -> floorplan -> placement -> CTS -> routing -> STA
                  -> RCX/SPEF -> DRC (Magic/KLayout) -> LVS (Netgen) -> GDSII
```

SystemVerilog is converted with `sv2v` before synthesis, as in the author's
prior SKY130 work. LibreLane is invoked through its pinned container (or the
Nix lock) so the toolchain identity is reproducible.

## Deliverables

```text
rtl/memory/
  aster_sram_macro.sv                          # single-port adapter to the macro
  sky130_sram_2kbyte_1rw1r_32x512_8.sv         # behavioural model (black box at PnR)
asic/sky130/
  README.md            # how to run the flow
  config.json          # LibreLane configuration (minimal config above)
  pin_order.cfg        # IO placement
  constraints.sdc      # 20 ns clock, false paths, IO delays
  aster_asic.sv        # thin top binding aster_minimal to the pad ring
scripts/
  run_asic.py          # deterministic flow driver + report capture
  audit_phase15.py     # read-only closeout audit
docs/results/phase15/closeout-<rev>/   # self-contained evidence bundle
```

The wrapper lives with the other memory RTL so every Verilator target picks it
up; the physical macro (LEF/LIB/GDS) is supplied by the PDK at place and route.

## Verification and acceptance gates

- [x] Minimal configuration frozen and documented; `make check` still green
      (201 host/Verilator tests, 0 failures).
- [x] `aster_minimal` ROM/RAM parameterization keeps every existing
      simulation/FPGA configuration bit-identical.
- [x] Yosys synthesis completes with no unmapped cells and no inferred latches.
- [x] The flow reaches GDSII with zero DRC violations (Magic and KLayout) and a
      clean Netgen LVS.
- [x] Antenna checks pass (0 violating nets, 0 violating pins).
- [x] Static timing analysis meets the 20 ns constraint (WNS ≥ 0) at the
      `nom_tt_025C_1v80` corner, with hold met at `nom_ss_100C_1v60`; the
      achieved Fmax is recorded.
- [x] Post-layout gate-level simulation (with back-annotated SDF) reproduces the
      firmware oracle on the same `hello`/benchmark program used in Phase 1/2.
- [x] The GDS, DEF, SPEF, timing/DRC/LVS reports and the tool/PDK identities are
      hash-bound and reproducible from a clean checkout.
- [x] Self-contained closeout bundle and read-only audit (`audit_phase15.py`).

### Signoff results

| Metric | Value |
| --- | --- |
| Setup WNS, worst corner (`max_ss_100C_1v60`) | **+1.679 ns** |
| Setup WNS, `nom_ss_100C_1v60` | +2.559 ns |
| Setup WNS, `nom_tt_025C_1v80` | +6.303 ns |
| Hold WNS, worst corner | +0.270 ns |
| Hold WNS, `nom_ss_100C_1v60` | +0.821 ns |
| Achieved Fmax (`nom_tt_025C_1v80`) | ~73 MHz |
| Magic DRC / KLayout DRC / XOR | 0 / 0 / 0 |
| Netgen LVS errors / unmatched nets / pins | 0 / 0 / 0 |
| Route DRC / antenna violations | 0 / 0 |
| Post-layout gate-level sim (SDF, `nom_tt`) | PASS — `Hello from Aster` |

### Flow lessons

- **The ROM must be combinational.** A `$readmemh`-initialised `reg` array is
  mapped by Yosys to power-up-initialised flops. SKY130 flops have no
  power-up value and the initialiser is dropped when the memory passes through
  ABC, so the netlist booted with an empty ROM. `scripts/run_asic.py` now bakes
  the firmware into a combinational `aster_asic_rom_image` case.
- **Gate-level simulation needs `-DFUNCTIONAL`.** The sky130 cell models are
  timing-only stubs unless `FUNCTIONAL` is defined; without it every flop stays
  `X`. Icarus also needs `-gspecify` to honour `$sdf_annotate`, and cannot parse
  the escaped SRAM-macro instance name in the SDF, so the driver strips that one
  CELL block (the macro's access time is not needed for the cycle oracle).
- **`repair_design` must not use clock-delay buffers as data buffers.** The
  default `clkdlybuf4s*` cells produced ~7 ns of delay on the high-fanout
  PicoRV32 `latched_stalu` net. Excluding them, plus the post-global-route
  resizer, closed setup at every corner.

## Milestones

1. **Synthesis.** Parameterize the memories, wrap the SRAM macro, convert with
   `sv2v`, and get a fully mapped gate-level netlist.
2. **Floorplan + placement.** Pin the pad ring, place the macro and core, and
   resolve any congestion.
3. **CTS + routing.** Build the clock tree and route to zero DRC.
4. **Signoff.** STA across corners, RCX/SPEF, antenna, DRC and LVS.
5. **GDSII + post-layout simulation.** Stream out and re-verify the oracle on
   the extracted netlist.
6. **Closeout.** Bundle the evidence and the audit.

## Explicit non-goals

- **No frozen v1 system.** Caches, coherence, DMA, Xasterdot8, the NPU,
  multicore and interrupts are Phase 16.
- **No tapeout.** Phase 17.
- **No PPA optimization.** Area/power/performance analysis is Phase 16; Phase 15
  only needs a correct, clean, closed flow.
- **No custom SRAM generation** (the PDK macros are used as-is) and no
  analog/mixed-signal blocks.
- **No changes to any frozen v1.0 address, register, ABI or instruction.**
