# Phase 21: 100 MHz closure

Status: **complete (12 October 2026).** The final image of Phase 20 meets phase17-plus.md §6's Phase 21 exit
(§2 below). The evidence is in [`results/phase21/closure-21/`](results/phase21/closure-21/README.md).

## 1. The owner's decisions, 12 October 2026

1. **The plan approved,** every decision as recommended ("I agree with all!! lets get this wrapped up"). It
   covered:
   - the formal closure record;
   - three cold boots;
   - energy per operation from a SAIF dynamic-power estimate of the final image.

   The plan as approved is in git: 3c7b0d8, approved in 65a238d.
2. **The energy estimate scrapped** ("lets scrap SAIF and power estimates. its fine"). Step 1 had measured that it
   would take about a day of simulation (§3). The frozen energy target is dropped, as SKY130's was
   (phase17-plus.md §2).
3. **No power cycles** ("no turning the board off n on"): the plan's D8, third option, "none, keeping 20.5's
   loads as the repeated boots". Each is a separate programming of the FPGA (§2).

So Phase 21 returns to tuning.md's decision 9 of 10 October 2026: a short phase, whose acceptance subset is 20.5's
board set.

## 2. The closure record

phase17-plus.md §6's exit for Phase 21, item by item. Every item is the final image's: step 4 of 20.5, the
o5asm-mgi build, whose bitstream is 24a41d81… and whose configuration data is 0bf4b4c3….

| The exit asks for | The evidence |
| --- | --- |
| All-engine routed timing at 10 ns: setup and hold, no failing endpoint | Setup +0.306 ns and hold +0.040 ns, no failing endpoint of 75,552. Pulse width +3.750 ns, no failing endpoint of 26,150. One clock, clk_fpga_0 at 10.000 ns, and no check_timing finding. |
| Routing and methodology checks pass | All 61,609 routable nets routed, none with an error. No methodology finding. No DRC error; 62 DRC warnings, all DSP pipelining advisories (DPIP-1, DPOP-1, DPOP-2). |
| Resource limits respected (80%) | LUTs 72.75%, block RAM 60.00%, DSPs 12.27%. Slices are 92.3% occupied; the limit does not cover them. |
| The acceptance subset, a valid CoreMark included, physically at that clock on repeated boots | 20.5's board set: all 5,254 R programs of the final capture end on the PYNQ-Z1 exactly as simulated on each of two separate loads. FCLK0 measured 100.0001 and 99.9991 MHz. CoreMark's performance and validation runs, both validated by CoreMark's own CRCs and verdict, match on two further loads. The performance run reports "CoreMark 1.0 : 218.737829", 2.19 CoreMark/MHz. |
| No lower-frequency image reported as 100 MHz success | The board's FCLK0 is set to and measured at 100 MHz on every load, and each program's cycle counts are the simulation's. |

**What "exactly as simulated" means** (20.5's board run):
- the same status;
- tohost written at the same cycle, with the same instructions retired by hart 0;
- the window's cycles and instructions;
- the console byte for byte, so every record and counter in it.

**The repeated boots** are separate programmings of the FPGA through the PYNQ's FPGA manager, with the board left
on. Six are counted:
- **audited (four):** the two full loads, and two loads of CoreMark's two runs;
- **not audited (two):** two earlier loads of CoreMark's performance run alone.

A seventh load was cut off at 3,666 programs, all as simulated, and is not counted. By the owner's decision there
was no power-cycled boot.

**Re-checked for this record:**
- **The routed checkpoint is the image.** A bitstream written from `aster_soc_routed.dcp` has the configuration data
  of record, 0bf4b4c3…: 4,045,516 bytes from the sync word (`results/phase21/closure-21/checkpoint.txt`).
- **Phase 20's closeout audit,** re-run at 65a238d, says PASS on every check
  (`results/phase21/closure-21/audit.txt`). It checks:
  - the final build's timing, routing, DRC, methodology and utilization reports;
  - both board loads against the bundle's archived consoles and the board reference;
  - CoreMark's two runs on two loads.

## 3. What step 1 of the energy estimate found

Recorded because these figures were before the owner when they chose, and for anyone who takes energy up again.
The logs are in build/energy, not retained. The tooling's comments cite the plan's sections as they were at
65a238d.
- **The method worked.**
  - The SoC cell's functional netlist, simulated in xsim (verification/energy/tb_energy.sv), reproduced 20.5's board
    reference exactly on the one program run to its end: the NPU's 64-element dot product, three times, matching
    the tohost cycle, hart 0's retired count, the window and the console.
  - ECG's adopted pipeline was stopped after its interval. The other smoke runs were stopped before they started:
    a fault in how they were launched (below).
  - Its switching activity over each counting interval (energy_run.tcl, watched on the netlist's own counting
    flip-flop) matched the records' cycles exactly. For example, ECG's adopted pipeline's interval was 33,023
    cycles, its record's.
  - Read into the routed checkpoint, the activity matched 98% of the design's nets, with Vivado's confidence high.
    One window, the 64-element dot product on the NPU (190 cycles), at Vivado's default conditions (typical
    process, 25 °C ambient; junction 47.1 °C):
    - the SoC's dynamic power was 0.229 W;
    - the clock network inside it was 0.102 W;
    - the NPU was 0.116 W, its own clock share (0.044 W) included.

    That figure is a smoke test, not a result.
- **It is slow:** the routed design simulates at about 210 to 270 cycles a second, and about 120 while recording
  activity.
  - The energy set (117 programs, 103 million cycles) would have taken about a day of wall time with 24 runs in
    parallel, bounded by its longest programs (MNIST scalar, 9.7 million cycles). That assumes the launch fault
    below is fixed. The machine has 16 cores, 32 threads.
  - Each of CIFAR's CPU paths alone would have taken three to five and a half days.
- **What xsim and Vivado need:**
  - xsim garbles 55 escaped net names to "(null)", and Vivado reads escaped names only in another form, so the
    merge rewrites them (scripts/energy_saif.py);
  - a recursive object listing takes about 13 minutes per run.
- **The tooling was removed when the repository was cleaned to v2** (12 October 2026). It is in the `v2.0-full` tag:
  - verification/energy/;
  - scripts/energy_run.py and scripts/energy_saif.py;
  - the idle programs (software/matrix/idle.c, the energy_idle family).
- **Its known faults, not fixed:**
  - parallel runs of energy_run.py share xsim's snapshot directory, so they must not run at once;
  - the testbench's console reads are not gated inside a window;
  - each interval's opening and closing should wait out the clock edge's combinational settling.
