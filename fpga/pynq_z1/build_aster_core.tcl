# The Aster core's board design for the PYNQ-Z1 (milestone 18.7's feasibility
# run): the Zynq PS (FCLK0 at 100 MHz, M_AXI_GP0) and the core with its L1
# caches, block-RAM memory and register page (rtl/soc/aster_core_pynq.sv,
# through the module-reference shim aster_core_pynq_ip.v), its AXI4-Lite port
# at 0x4000_0000 (256 KiB). Implemented in context — the whole device, the PS
# interface and its clock — with the v1 Linux overlay's directives, signed off
# by fpga/pynq_z1/signoff.tcl (no negative setup or hold slack, no DRC
# errors), and written as a bitstream and its hardware handoff.
#
#   vivado -mode batch -source build_aster_core.tcl -tclargs <repo-root> <output-dir> ?fclk-mhz?
if {$argc < 2 || $argc > 3} { error "usage: build_aster_core.tcl <repo-root> <output-dir> ?fclk-mhz?" }
set repo_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set fclk 100
if {$argc >= 3} { set fclk [lindex $argv 2] }
# The shim's clock interface carries FREQ_HZ 100000000 as a fixed attribute
# (the block design will not let this script override it).
if {$fclk != 100} { error "the shim's FREQ_HZ is 100 MHz; change aster_core_pynq_ip.v with the clock" }
set part xc7z020clg400-1
file mkdir $output_dir
create_project aster_core_board $output_dir -part $part -force
set rtl_files [list rtl/aster_core/aster_core_pkg.sv rtl/aster_core/aster_core_fetch.sv rtl/aster_core/aster_core.sv \
    rtl/aster_core/aster_l1_ram.sv rtl/aster_core/aster_l1i.sv rtl/aster_core/aster_l1d.sv rtl/soc/aster_core_pynq.sv]
foreach relative $rtl_files { read_verilog -sv [file join $repo_root $relative] }
read_verilog [file join $repo_root rtl/soc/aster_core_pynq_ip.v]

# Not "aster_core": that is the core's RTL module, which the wrapper would pick up.
create_bd_design aster_core_board
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $fclk] [get_bd_cells ps7]
make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]

create_bd_cell -type module -reference aster_core_pynq_ip aster
set_property CONFIG.CLK_HZ [expr {round($fclk * 1000000)}] [get_bd_cells aster]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 fabric
set_property CONFIG.NUM_MI 1 [get_bd_cells fabric]
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 reset
# PS reset polarity is propagated from FCLK_RESET0_N (read-only in Vivado).
# The unused auxiliary input is tied low and must be active-high. Leaving
# its default active-low holds the entire AXI bus reset.
set_property CONFIG.C_AUX_RESET_HIGH {1} [get_bd_cells reset]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 one
set_property CONFIG.CONST_VAL 1 [get_bd_cells one]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property CONFIG.CONST_VAL 0 [get_bd_cells zero]

connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins fabric/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins fabric/M00_AXI] [get_bd_intf_pins aster/s_axi]
connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins fabric/ACLK] [get_bd_pins fabric/S00_ACLK] [get_bd_pins fabric/M00_ACLK] \
    [get_bd_pins reset/slowest_sync_clk] [get_bd_pins aster/aclk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins reset/ext_reset_in]
connect_bd_net [get_bd_pins one/dout] [get_bd_pins reset/dcm_locked]
connect_bd_net [get_bd_pins zero/dout] [get_bd_pins reset/aux_reset_in] [get_bd_pins reset/mb_debug_sys_rst]
connect_bd_net [get_bd_pins reset/interconnect_aresetn] [get_bd_pins fabric/ARESETN]
connect_bd_net [get_bd_pins reset/peripheral_aresetn] [get_bd_pins fabric/S00_ARESETN] \
    [get_bd_pins fabric/M00_ARESETN] [get_bd_pins aster/aresetn]
assign_bd_address -offset 0x40000000 -range 0x00040000 \
    -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs aster/s_axi/reg0]
validate_bd_design
save_bd_design

set bd [get_files */aster_core_board.bd]
generate_target all $bd
set handoff [file join $output_dir aster_core_board.gen sources_1 bd aster_core_board hw_handoff aster_core_board.hwh]
add_files [make_wrapper -files $bd -top]
set_property top aster_core_board_wrapper [current_fileset]
update_compile_order -fileset sources_1
# Project runs include the generated module-reference wrapper and synthesize
# the block design's out-of-context IP; a bare synth_design skips those runs.
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
open_run synth_1
write_checkpoint -force [file join $output_dir aster_core_synth.dcp]
report_utilization -file [file join $output_dir utilization_synth.rpt]
opt_design
place_design -directive Explore
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
phys_opt_design -directive AggressiveExplore
# The routed checkpoint before signoff, so a failing build can be inspected.
write_checkpoint -force [file join $output_dir aster_core_routed.dcp]
source [file join $repo_root fpga/pynq_z1/signoff.tcl]
aster_signoff $output_dir
set worst [get_timing_paths -setup -max_paths 1 -nworst 1]
set fp [open [file join $output_dir summary.txt] w]
puts $fp "SUMMARY top=aster_core_board_wrapper fclk_mhz=$fclk wns_ns=[get_property SLACK $worst] whs_ns=[get_property SLACK [get_timing_paths -hold -max_paths 1 -nworst 1]] from=[get_property STARTPOINT_PIN $worst] to=[get_property ENDPOINT_PIN $worst]"
close $fp
write_bitstream -force [file join $output_dir aster_core.bit]
file copy -force $handoff [file join $output_dir aster_core.hwh]
puts "ASTER_CORE_BUILD complete: [file join $output_dir aster_core.bit]"
