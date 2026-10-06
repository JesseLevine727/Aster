# The Aster core's board design for the PYNQ-Z1 (milestone 18.7's feasibility
# run): the Zynq PS (FCLK0 at 100 MHz, M_AXI_GP0) and the core with its L1
# caches, block-RAM memory and register page (rtl/soc/aster_core_pynq.sv,
# through the module-reference shim aster_core_pynq_ip.v), its AXI4-Lite port
# at 0x4000_0000 (256 KiB). Implemented in context — the whole device, the PS
# interface and its clock — with the v1 Linux overlay's directives, signed off
# by fpga/pynq_z1/signoff.tcl (no negative setup or hold slack, no DRC
# errors), and written as a bitstream and its hardware handoff.
# Design npu (milestone 19.4): the Phase 19 SoC instead — the core, its caches
# and the v2 NPU on 96 KiB of main memory (rtl/soc/aster_npu_soc.sv, through
# aster_npu_soc_ip.v), the same port and window; its outputs named aster_npu.
#
# With design npu, an optional fifth argument sets the SoC's parameters
# ("NAME=VALUE;...", 19.5's options, e.g. NPU_A_STRIPS=2): the shim is read
# from a copy in the output directory with those parameters' defaults set.
#
#   vivado -mode batch -source build_aster_core.tcl -tclargs <repo-root> <output-dir> ?fclk-mhz? ?core|npu? ?params?
if {$argc < 2 || $argc > 5} { error "usage: build_aster_core.tcl <repo-root> <output-dir> ?fclk-mhz? ?core|npu? ?params?" }
set repo_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set fclk 100
if {$argc >= 3} { set fclk [lindex $argv 2] }
set design core
if {$argc >= 4} { set design [lindex $argv 3] }
if {$design ni {core npu}} { error "design must be core or npu" }
set params {}
if {$argc >= 5} { set params [lindex $argv 4] }
# The shim's clock interface carries FREQ_HZ 100000000 as a fixed attribute
# (the block design will not let this script override it).
if {$fclk != 100} { error "the shim's FREQ_HZ is 100 MHz; change the shim (rtl/soc/*_ip.v) with the clock" }
set part xc7z020clg400-1
file mkdir $output_dir
if {$design eq "core"} {
    set board aster_core_board; set name aster_core; set shim aster_core_pynq_ip
    set top_rtl [list rtl/soc/aster_core_pynq.sv]
} else {
    set board aster_npu_board; set name aster_npu; set shim aster_npu_soc_ip
    set top_rtl [list rtl/accelerator/aster_npu2_ram.sv rtl/accelerator/aster_npu2_engine.sv \
        rtl/accelerator/aster_npu2.sv rtl/soc/aster_npu_soc.sv]
}
create_project $board $output_dir -part $part -force
set rtl_files [concat [list rtl/aster_core/aster_core_pkg.sv rtl/aster_core/aster_core_fetch.sv rtl/aster_core/aster_core.sv \
    rtl/aster_core/aster_l1_ram.sv rtl/aster_core/aster_l1i.sv rtl/aster_core/aster_l1d.sv] $top_rtl]
foreach relative $rtl_files { read_verilog -sv [file join $repo_root $relative] }
set shim_file [file join $repo_root rtl/soc/$shim.v]
if {$params ne ""} {
    set fp [open $shim_file r]; set text [read $fp]; close $fp
    foreach param [split $params ";"] {
        if {$param eq ""} continue
        lassign [split $param "="] pname pvalue
        if {![regsub "(parameter integer $pname = )\\d+" $text "\\1$pvalue" text]} {
            error "the shim has no parameter $pname"
        }
    }
    set shim_file [file join $output_dir $shim.v]
    set fp [open $shim_file w]; puts -nonewline $fp $text; close $fp
}
read_verilog $shim_file

# Not "aster_core": that is the core's RTL module, which the wrapper would pick up.
create_bd_design $board
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $fclk] [get_bd_cells ps7]
make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]

create_bd_cell -type module -reference $shim aster
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

set bd [get_files */$board.bd]
generate_target all $bd
set handoff [file join $output_dir $board.gen sources_1 bd $board hw_handoff $board.hwh]
add_files [make_wrapper -files $bd -top]
set_property top ${board}_wrapper [current_fileset]
update_compile_order -fileset sources_1
# Project runs include the generated module-reference wrapper and synthesize
# the block design's out-of-context IP; a bare synth_design skips those runs.
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
open_run synth_1
write_checkpoint -force [file join $output_dir ${name}_synth.dcp]
report_utilization -file [file join $output_dir utilization_synth.rpt]
opt_design
place_design -directive Explore
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
phys_opt_design -directive AggressiveExplore
# The routed checkpoint before signoff, so a failing build can be inspected.
write_checkpoint -force [file join $output_dir ${name}_routed.dcp]
source [file join $repo_root fpga/pynq_z1/signoff.tcl]
aster_signoff $output_dir
set worst [get_timing_paths -setup -max_paths 1 -nworst 1]
set fp [open [file join $output_dir summary.txt] w]
puts $fp "SUMMARY top=${board}_wrapper fclk_mhz=$fclk wns_ns=[get_property SLACK $worst] whs_ns=[get_property SLACK [get_timing_paths -hold -max_paths 1 -nworst 1]] from=[get_property STARTPOINT_PIN $worst] to=[get_property ENDPOINT_PIN $worst]"
close $fp
write_bitstream -force [file join $output_dir $name.bit]
file copy -force $handoff [file join $output_dir $name.hwh]
puts "ASTER_CORE_BUILD complete: [file join $output_dir $name.bit]"
