# The basis of soc.md §2's area estimate

`utilization_hierarchical_19.5.rpt`: `report_utilization -hierarchical
-hierarchical_depth 5` on the 19.5 SoC's routed checkpoint (the adopted
8×8 SoC, `make fpga-aster-npu`, bitstream `d3d7454f7f47…`; the same build as
`../../phase19/npu-19.5/fpga/`), Vivado 2025.1, 7 October 2026. The blocks
soc.md §2 uses: the core 3,167 LUTs and 8 DSPs, the data cache 2,373 LUTs and
1 block RAM, the instruction cache 620 LUTs and 1 block RAM, the NPU 14,131
LUTs, 40 block RAMs and 11 DSPs, of 21,520 LUTs, 79 block RAMs and 19 DSPs in
all.
