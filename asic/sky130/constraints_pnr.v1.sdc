# Phase 16 PnR constraints: over-constrain to 20 ns so the optimizer works
# hard; the signoff SDC (constraints.v1.sdc) checks the achievable period.
create_clock -name clk -period 20.0 [get_ports clk]
set clocks [get_clocks clk]
set io_delay [expr 20.0 * $::env(IO_DELAY_CONSTRAINT) / 100]
set clk_input [get_port clk]
set clk_index [lsearch [all_inputs] $clk_input]
set data_inputs [lreplace [all_inputs] $clk_index $clk_index ""]
set_input_delay  $io_delay -clock $clocks $data_inputs
set_output_delay $io_delay -clock $clocks [all_outputs]
set_false_path -from [get_ports rst_n]
set_max_fanout $::env(MAX_FANOUT_CONSTRAINT) [current_design]
set_load [expr $::env(OUTPUT_CAP_LOAD) / 1000.0] [all_outputs]
