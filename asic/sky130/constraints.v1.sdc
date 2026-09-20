# Phase 16 constraints: the full v1.3 all-engine coherent system at 20 ns.
# Relative to CLOCK_PERIOD so the same file works at any target frequency.

create_clock -name clk -period $::env(CLOCK_PERIOD) [get_ports clk]
set clocks [get_clocks clk]

set io_delay [expr $::env(CLOCK_PERIOD) * $::env(IO_DELAY_CONSTRAINT) / 100]

set clk_input [get_port clk]
set clk_index [lsearch [all_inputs] $clk_input]
set data_inputs [lreplace [all_inputs] $clk_index $clk_index ""]

set_input_delay  $io_delay -clock $clocks $data_inputs
set_output_delay $io_delay -clock $clocks [all_outputs]

# Reset is asynchronous; it is not a timed data input.
set_false_path -from [get_ports rst_n]

set_max_fanout $::env(MAX_FANOUT_CONSTRAINT) [current_design]
set_load [expr $::env(OUTPUT_CAP_LOAD) / 1000.0] [all_outputs]
