# The Phase 20 SoC's floorplan (milestone 20.2's margin work; a cycle-preserving
# lever): the xc7z020's programmable logic in three bands, so that each
# requester sits beside the fabric it meets every cycle.
# - The NPU in the bottom band (slice rows 0-44, the whole width): its 40
#   block RAMs and 11 DSPs are there (54 RAMB36 sites).
# - The fabric and its four banks in the middle band (rows 45-89, right of the
#   processing system): 36 RAMB36 sites for its 32.
# - Each hart (its core and both caches) in the top band (rows 90-149): hart 0
#   on the left, hart 1 on the right, each beside the fabric's band.
# The devices, the console and the AXI side are left to the placer.
# Block-RAM rows are a fifth of the slice rows (RAMB18 two-fifths), DSP rows
# two-fifths. RAMB36 columns X0 and X1 and DSP columns X0 and X1 exist below
# slice row 50 only (the processing system is above them): 140 RAMB36 sites in
# all.

create_pblock pb_npu
resize_pblock pb_npu -add {SLICE_X0Y0:SLICE_X113Y44 RAMB36_X0Y0:RAMB36_X5Y8 RAMB18_X0Y0:RAMB18_X5Y17 DSP48_X0Y0:DSP48_X4Y17}
add_cells_to_pblock pb_npu [get_cells {aster_soc_board_i/aster/inst/implementation/npu}]

create_pblock pb_fabric
resize_pblock pb_fabric -add {SLICE_X26Y45:SLICE_X113Y89 RAMB36_X2Y9:RAMB36_X5Y17 RAMB18_X2Y18:RAMB18_X5Y35}
add_cells_to_pblock pb_fabric [get_cells {aster_soc_board_i/aster/inst/implementation/fabric}]

create_pblock pb_hart0
resize_pblock pb_hart0 -add {SLICE_X26Y90:SLICE_X69Y149 RAMB36_X2Y18:RAMB36_X3Y29 RAMB18_X2Y36:RAMB18_X3Y59 DSP48_X2Y36:DSP48_X2Y59}
add_cells_to_pblock pb_hart0 [get_cells {aster_soc_board_i/aster/inst/implementation/g_hart\[0\].core aster_soc_board_i/aster/inst/implementation/g_hart\[0\].icache aster_soc_board_i/aster/inst/implementation/g_hart\[0\].dcache}]

create_pblock pb_hart1
resize_pblock pb_hart1 -add {SLICE_X70Y90:SLICE_X113Y149 RAMB36_X4Y18:RAMB36_X5Y29 RAMB18_X4Y36:RAMB18_X5Y59 DSP48_X3Y36:DSP48_X4Y59}
add_cells_to_pblock pb_hart1 [get_cells {aster_soc_board_i/aster/inst/implementation/g_hart\[1\].core aster_soc_board_i/aster/inst/implementation/g_hart\[1\].icache aster_soc_board_i/aster/inst/implementation/g_hart\[1\].dcache}]
