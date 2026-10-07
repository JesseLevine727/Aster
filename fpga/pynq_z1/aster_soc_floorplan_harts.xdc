# The Phase 20 SoC's floorplan, harts only (milestone 20.2's margin work; a
# cycle-preserving lever): each hart (its core and both caches) kept together
# in the top band (slice rows 90-149), hart 0 on the left, hart 1 on the right;
# the fabric, the NPU and the rest are left to the placer. Site ranges as in
# aster_soc_floorplan.xdc.

create_pblock pb_hart0
resize_pblock pb_hart0 -add {SLICE_X26Y90:SLICE_X69Y149 RAMB36_X2Y18:RAMB36_X3Y29 RAMB18_X2Y36:RAMB18_X3Y59 DSP48_X2Y36:DSP48_X2Y59}
add_cells_to_pblock pb_hart0 [get_cells {aster_soc_board_i/aster/inst/implementation/g_hart\[0\].core aster_soc_board_i/aster/inst/implementation/g_hart\[0\].icache aster_soc_board_i/aster/inst/implementation/g_hart\[0\].dcache}]

create_pblock pb_hart1
resize_pblock pb_hart1 -add {SLICE_X70Y90:SLICE_X113Y149 RAMB36_X4Y18:RAMB36_X5Y29 RAMB18_X4Y36:RAMB18_X5Y59 DSP48_X3Y36:DSP48_X4Y59}
add_cells_to_pblock pb_hart1 [get_cells {aster_soc_board_i/aster/inst/implementation/g_hart\[1\].core aster_soc_board_i/aster/inst/implementation/g_hart\[1\].icache aster_soc_board_i/aster/inst/implementation/g_hart\[1\].dcache}]
