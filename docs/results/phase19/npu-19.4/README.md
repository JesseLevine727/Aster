# The Phase 19 SoC, milestone 19.4 — the gates in simulation, 10 ns in context

The text of record is the "Milestone 19.4" section of
[`../../../phase19.md`](../../../phase19.md); `SHA256SUMS` covers every file
here.

**The gates** (`soc.log`, from `make npu-soc-tests`): the gate programs
(`software/npu2`) on the SoC's simulation (`verification/npu/tb_npu_soc.cpp`
around `rtl/soc/aster_npu_soc.sv`). Every NPU job, write and snoop, and each
CPU GEMM, is checked against the independent reference. The gates:
- GEMM 64×64×64, 96×96×96 and 128×64×128: utilization 86.8%, 91.3% and 90.4%
  (gate 50%); end-to-end speedup over the best CPU code (DOT8) 11.57×, 11.78×
  and 11.74× (gate 5×);
- the MNIST MLP: 3.53× on the worst of its 32 images (gate 2×);
- N = 1 utilization, measured: 19.2% (32×1×784) and 12.7% (784×1×25);
- the coherence program's 160 jobs, 2 error jobs, 2 aborts, AMOs racing
  the jobs and the reservation test;
- the fault program's 7 traps (sub-word and atomic accesses to the NPU's
  registers);
- every main-memory load the data cache answered, checked against the
  memory as it was (1.08 million in the coherence program).

`soc-board.log` holds 18.7's board programs on the same SoC. 99 of them end
as in the CPU shell, cycle for cycle, every RVFI record as the shell's;
`traps/m1_traps` is not run, because its "outside memory" is the NPU's
register page here.

**Timing, in context** (`fpga/`, from `make fpga-aster-npu`): Vivado 2025.1,
the whole device with the Zynq PS and its interface, 18.7's flow
(`fpga/pynq_z1/build_aster_core.tcl`, design `npu`), signed off by
`fpga/pynq_z1/signoff.tcl`.
- FCLK0 constrained at 10.000 ns;
- worst setup slack **+0.267 ns**, worst hold slack +0.028 ns;
- no failing endpoint among 30,601;
- all 21,420 routable nets routed; no DRC error.

The worst path is inside the core and its data cache, 16 logic levels:
- the data cache's stage-1 address, through its window decode (two I/O
  windows, one word-only), into its same-cycle error answer;
- then the core's M1 trap logic and Execute's advance;
- ending at Execute's forwarding select (`dcache/s1_addr_reg` →
  `core/fsel1_n_reg`).

Area, all within npu.md §7's 80%:

| Resource | Used | Of the device |
| --- | ---: | ---: |
| LUTs | 12,625 | 23.7% |
| Flip-flops | 8,982 | 8.4% |
| Block-RAM tiles | 47 | 33.6% |
| DSPs | 19 | 8.6% |

The 4 MB bitstream is not kept. `fpga/bitstream.sha256` holds its hash, and
`fpga/aster_npu.hwh` its hardware handoff; 19.5's board run uses them. An
earlier build of the design before the review's fixes (not retained) met
+0.074 ns on the same path family; placement moves it.
