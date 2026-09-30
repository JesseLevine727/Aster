# Aster core, milestone 18.1 — first timing report

The seven-stage RV32I Aster core (`rtl/aster_core/`) at source revision
`9971ea1` (the RTL and the timing wrappers are unchanged since; the runs
started after their last edit). Captured on 30 September 2026. `SHA256SUMS`
covers every file here (`scripts/timing/retain.py`); the text of record is
the "Milestone 18.1" section of [`../../../phase18.md`](../../../phase18.md).

## Settings

- **FPGA:** Vivado 2025.1, `xc7z020clg400-1`, out of context, 10 ns,
  register to register (`make timing-fpga-aster`). Three tops:
  - `fpga/aster` — the core alone (`verification/core/timing_aster.sv`);
  - `fpga/aster_bram` — the core with a two-cycle 128 KiB true dual-port block
    RAM behind its ports in the form docs/cpu.md §5 describes (the block RAM
    samples the request; its output register is the second cycle), with an
    aligned-region address decode (`timing_aster_bram.sv`);
  - `fpga/aster_bram_reqreg` — the same memory with the request registered
    first and the block RAM read in the second cycle
    (`timing_aster_bram_reqreg.sv`; the same two-cycle latency).

  `named_paths.rpt` holds the paths §4 names, from the register behind
  `d_rsp_valid` and the one behind `d_rsp_error` to whatever samples
  `d_req_valid` (the block RAMs' write enables and `d_v1` in the §5 form, the
  request register `d_en` and `d_v1` otherwise), and the worst paths from those
  two registers to anywhere (`OOC_NAMED_PATHS`).
- **SKY130:** LibreLane 3.0.14, `hd` cells, 10 ns, the PicoRV32 baseline v2
  flow and constraints (`asic/sky130/config.core_aster.json`,
  `constraints.core.sdc`: 2 ns input and output delays, 0.25 ns uncertainty,
  5% derate, `SYNTH_STRATEGY "DELAY 1"`, post-global-route *timing* repair,
  `RUN_POST_GRT_RESIZER_TIMING`; design repair after global routing off, the
  default). Two runs:
  - `asic/aster` — run `p18-aster`, the default flow; its signoff STA also
    reports §4's two port paths (`asic/sky130/sta_named_paths.tcl`, per corner
    in `named_paths.rpt`). Step 57 is a signoff-only re-run of step 56 on the
    same routed design, adding that report: its `summary.rpt` is identical,
    and its configuration differs only in `STA_EXTRA_CORNER_TCL_FILE`;
  - `asic/aster_pnr_margin` — run `p18-aster-pnr-margin`, placed and routed
    against `constraints.core.pnr_margin.sdc` (3 ns extra setup uncertainty)
    and signed off at 10 ns, the experiment that gained PicoRV32 11 MHz.

## Results

| Target | Worst setup slack (register to register) | Implied fmax | Area |
| --- | ---: | ---: | --- |
| FPGA, core alone | +0.196 ns | 102.0 MHz | 1,688 LUTs, 767 FFs |
| FPGA, core + block RAM, §5 form | −0.440 ns | 95.8 MHz | 1,820 LUTs, 869 FFs, 32 BRAM36 |
| FPGA, core + block RAM, request registered | +0.364 ns | 103.8 MHz | 1,791 LUTs, 999 FFs, 32 BRAM36 |
| SKY130 `nom_tt_025C_1v80`, default flow | +2.538 ns | 134.0 MHz | 163,412 µm², 18,891 cells |
| SKY130 `max_ss_100C_1v60`, default flow | −4.874 ns | 67.2 MHz | (576 failing endpoints) |
| SKY130 `max_ss_100C_1v60`, with the PnR margin | −5.462 ns | 64.7 MHz | 164,972 µm² |

§4's named paths: on the FPGA, to the request, +3.711 ns (`d_rsp_valid`) and
+3.168 ns (`d_rsp_error`) in the §5 form, +7.059 and +6.192 ns with the request
registered; from those registers to anywhere (the forwarding selects),
+2.022 and +1.186 ns, and +2.236 and +1.942 ns; on SKY130 (input port to the `d_req_valid` output, within the
2 ns input and output delays), +1.035 and −0.238 ns at `max_ss`, +3.379 and
+2.773 ns at `nom_tt`.

The FPGA's remaining miss in the §5 form is on the core-to-memory path (the
forwarded base register, the address adder, the region decode and the write
enables of 32 block RAMs). SKY130's slow-corner miss is broad and dominated by
chains of minimum-size buffers; its worst path is the register file's
same-cycle write-through compare into Execute's operand register
(`asic/aster/57-openroad-stapostpnr/worst_paths.txt`). The proposed fixes are
in phase18.md.
