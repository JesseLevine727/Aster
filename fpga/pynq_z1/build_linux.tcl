# AXI host bridge and real serial loopback, loaded through PYNQ Linux/PCAP.
if {$argc < 2 || $argc > 13} { error "usage: build_linux.tcl <repo-root> <output-dir> ?harts? ?coherent? ?caches? ?dma? ?dot8? ?npu? ?l2? ?impl? ?npu-rows? ?npu-cols? ?fclk-mhz?" }
set repo_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set harts 0
if {$argc >= 3} { set harts [lindex $argv 2] }
if {$harts ni {0 1 2}} { error "invalid Linux hart configuration" }
set coherent 0
set caches 1
set dma 0
set dot8 0
if {$argc >= 4} { set coherent [lindex $argv 3] }
if {$argc >= 5} { set caches [lindex $argv 4] }
if {$argc >= 6} { set dma [lindex $argv 5] }
if {$argc >= 7} { set dot8 [lindex $argv 6] }
set npu 0
if {$argc >= 8} { set npu [lindex $argv 7] }
set l2 0
if {$argc >= 9} { set l2 [lindex $argv 8] }
set impl 1
if {$argc >= 10} { set impl [lindex $argv 9] }
set npu_rows 4
if {$argc >= 11} { set npu_rows [lindex $argv 10] }
set npu_cols 4
if {$argc >= 12} { set npu_cols [lindex $argv 11] }
set fclk 31.25
if {$argc >= 13} { set fclk [lindex $argv 12] }
# Non-default FCLK0 also requires the matching FREQ_HZ in aster_pynq_linux.sv and
# aster_linux_ip.v, because the module-reference clock interface is a fixed
# string attribute that the BD will not let the tcl override.
if {$coherent ni {0 1} || $caches ni {0 1} || $dma ni {0 1} || ($coherent && !$harts) || (!$coherent && (!$caches || $dma))} {
    error "invalid coherent Linux configuration"
}
if {$dot8 ni {0 1} || ($dot8 && (!$coherent || !$dma))} { error "dot8 Linux requires coherence and DMA" }
if {$npu ni {0 1} || ($npu && (!$coherent || !$dma))} { error "NPU Linux requires coherence and DMA" }
if {$l2 ni {0 1} || ($l2 && (!$coherent || !$dma))} { error "L2 Linux requires coherence and DMA" }
if {$npu_rows ni {2 4 8} || $npu_cols ni {2 4 8}} { error "NPU geometry must be 2, 4 or 8" }
if {($npu_rows != 4 || $npu_cols != 4) && !$npu} { error "non-default NPU geometry requires the NPU" }
set part xc7z020clg400-1
file mkdir $output_dir
create_project aster_linux $output_dir -part $part -force
set_property verilog_define {RISCV_FORMAL} [current_fileset]
set rtl_files [list rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v \
    rtl/cache/aster_l1_cache.sv rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv \
    rtl/peripherals/aster_uart.sv rtl/peripherals/aster_uart_tx.sv \
    rtl/peripherals/aster_uart_rx.sv rtl/peripherals/aster_perf_counters.sv \
    rtl/core/aster_hart.sv rtl/soc/aster_minimal.sv rtl/soc/aster_pynq_linux.sv \
    rtl/interconnect/aster_arbiter2.sv rtl/soc/aster_shared_fabric.sv rtl/soc/aster_multicore.sv \
    rtl/core/aster_pcpi_atomic.sv rtl/core/aster_pcpi_dot8.sv rtl/core/aster_atomic_hart.sv rtl/interconnect/aster_atomic_fabric.sv \
    rtl/cache/aster_coherent_cache.sv rtl/cache/aster_l2_cache.sv rtl/soc/aster_warm_stop.sv \
    rtl/interconnect/aster_device_arbiter.sv rtl/accelerator/aster_int8_pe.sv \
    rtl/accelerator/aster_int8_array.sv rtl/accelerator/aster_npu_engine.sv \
    rtl/accelerator/aster_npu_regs.sv \
    rtl/peripherals/aster_coherent_perf.sv rtl/dma/aster_dma_engine.sv \
    rtl/interconnect/aster_dma_arbiter.sv rtl/peripherals/aster_dma_perf.sv \
    rtl/peripherals/aster_dot8_perf.sv rtl/peripherals/aster_timer.sv rtl/peripherals/aster_interrupt_controller.sv rtl/soc/aster_coherent_soc.sv]
foreach relative $rtl_files { read_verilog -sv [file join $repo_root $relative] }
read_verilog [file join $repo_root rtl/soc/aster_linux_ip.v]

create_bd_design aster_linux
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps7
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 {1} \
    CONFIG.PCW_EN_CLK0_PORT {1} CONFIG.PCW_EN_RST0_PORT {1} \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ $fclk] [get_bd_cells ps7]
make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR]
make_bd_intf_pins_external [get_bd_intf_pins ps7/FIXED_IO]

create_bd_cell -type module -reference aster_linux_ip aster
set_property CONFIG.HART_COUNT $harts [get_bd_cells aster]
set_property CONFIG.ENABLE_COHERENCE $coherent [get_bd_cells aster]
set_property CONFIG.COHERENT_L1 $caches [get_bd_cells aster]
set_property CONFIG.ENABLE_DMA $dma [get_bd_cells aster]
set_property CONFIG.ENABLE_DOT8 $dot8 [get_bd_cells aster]
set_property CONFIG.ENABLE_NPU $npu [get_bd_cells aster]
set_property CONFIG.ENABLE_L2 $l2 [get_bd_cells aster]
set_property CONFIG.NPU_ROWS $npu_rows [get_bd_cells aster]
set_property CONFIG.NPU_COLS $npu_cols [get_bd_cells aster]
set_property CONFIG.FREQ_HZ [expr {round($fclk * 1000000)}] [get_bd_cells aster]
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
create_bd_port -dir O uart_tx
create_bd_port -dir O -from 3 -to 0 led
connect_bd_net [get_bd_pins aster/uart_tx] [get_bd_ports uart_tx]
connect_bd_net [get_bd_pins aster/led] [get_bd_ports led]
assign_bd_address -offset 0x40000000 -range 0x00040000 \
    -target_address_space [get_bd_addr_spaces ps7/Data] [get_bd_addr_segs aster/s_axi/reg0]
validate_bd_design
save_bd_design

set bd [get_files */aster_linux.bd]
generate_target all $bd
set handoff [file join $output_dir aster_linux.gen sources_1 bd aster_linux hw_handoff aster_linux.hwh]
set handoff_check [list python3 [file join $repo_root scripts/pynq_handoff.py] $handoff --harts $harts]
if {$coherent} { lappend handoff_check --coherent }
if {!$caches} { lappend handoff_check --no-cache }
if {$dma} { lappend handoff_check --dma }
if {$dot8} { lappend handoff_check --dot8 }
if {$npu} { lappend handoff_check --npu }
puts [exec {*}$handoff_check]
add_files [make_wrapper -files $bd -top]
set_property top aster_linux_wrapper [current_fileset]
read_xdc [file join $repo_root fpga/pynq_z1/aster_linux.xdc]
update_compile_order -fileset sources_1
# Project runs include generated module-reference wrappers and synthesize/link
# the block design's out-of-context IP. A bare synth_design skips those runs.
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    error "Linux overlay synthesis failed: [get_property STATUS [get_runs synth_1]]"
}
open_run synth_1
# Test the real generated vendor reset netlist, including startup, warm reset,
# loss of lock and auxiliary/debug polarity. No substitute reset model.
set reset_netlist [file join $output_dir aster_linux.gen sources_1 bd aster_linux \
    ip aster_linux_reset_0 aster_linux_reset_0_sim_netlist.v]
puts [exec python3 [file join $repo_root scripts/check_pynq_reset.py] \
    --netlist $reset_netlist --output-dir [file join $output_dir reset_sim]]
write_checkpoint -force [file join $output_dir aster_linux_synth.dcp]
report_utilization -file [file join $output_dir utilization_synth.rpt]
if {!$impl} {
    puts "ASTER_LINUX_UTIL complete: [file join $output_dir utilization_synth.rpt]"
    exit 0
}
opt_design
place_design -directive Explore
phys_opt_design -directive AggressiveExplore
route_design -directive Explore
phys_opt_design -directive AggressiveExplore
# Write the routed checkpoint before signoff so a failing build is still
# inspectable for its critical paths.
write_checkpoint -force [file join $output_dir aster_linux_routed.dcp]
source [file join $repo_root fpga/pynq_z1/signoff.tcl]
aster_signoff $output_dir
write_bitstream -force [file join $output_dir aster_linux.bit]
file copy -force $handoff [file join $output_dir aster_linux.hwh]
puts "ASTER_LINUX_BUILD complete: [file join $output_dir aster_linux.bit]"
