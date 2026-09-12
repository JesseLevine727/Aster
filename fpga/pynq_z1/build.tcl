# Reproducible non-project Vivado build for the PL-only PYNQ-Z1 target.
# Usage:
#   vivado -mode batch -source fpga/pynq_z1/build.tcl \
#     -tclargs <repo-root> <output-dir> <firmware-hex>

if {$argc < 3} {
    error "usage: build.tcl <repo-root> <output-dir> <firmware-hex>"
}

set repo_root [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
set firmware_hex [file normalize [lindex $argv 2]]
set part xc7z020clg400-1

if {![file exists $firmware_hex]} {
    error "firmware image does not exist: $firmware_hex (run 'make firmware' first)"
}

file mkdir $output_dir
cd $repo_root
create_project aster_pynq_z1 $output_dir -part $part -force

set rtl_files [list \
    rtl/core/aster_picorv32.sv \
    vendor/picorv32/picorv32.v \
    rtl/memory/aster_rom.sv \
    rtl/memory/aster_ram.sv \
    rtl/peripherals/aster_uart.sv \
    rtl/peripherals/aster_uart_tx.sv \
    rtl/soc/aster_minimal.sv \
    rtl/soc/aster_pynq_z1.sv \
]

foreach relative_file $rtl_files {
    set source_file [file normalize [file join $repo_root $relative_file]]
    read_verilog -sv $source_file
}
read_xdc [file normalize [file join $repo_root fpga/pynq_z1/aster_pynq_z1.xdc]]

set_property top aster_pynq_z1 [current_fileset]
update_compile_order -fileset sources_1

puts "Building Aster PYNQ-Z1 with firmware: $firmware_hex"
synth_design -top aster_pynq_z1 -part $part \
    -generic [format {MEM_INIT_FILE="%s"} $firmware_hex]
write_checkpoint -force [file join $output_dir aster_pynq_z1_synth.dcp]
report_utilization -file [file join $output_dir utilization.rpt]

opt_design
place_design
route_design
report_timing_summary -file [file join $output_dir timing_summary.rpt] -warn_on_violation
report_utilization -file [file join $output_dir utilization_routed.rpt]
write_checkpoint -force [file join $output_dir aster_pynq_z1_routed.dcp]
write_bitstream -force [file join $output_dir aster_pynq_z1.bit]

puts "Aster PYNQ-Z1 bitstream generated: [file join $output_dir aster_pynq_z1.bit]"
