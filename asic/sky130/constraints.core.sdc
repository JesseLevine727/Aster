# Phase 18 core-block constraints: one clock at CLOCK_PERIOD and I/O delays
# relative to it, so memory-port paths are timed against the block's clock.
create_clock -name clk -period $::env(CLOCK_PERIOD) [get_ports clk]
set clocks [get_clocks clk]

set io_delay [expr $::env(CLOCK_PERIOD) * $::env(IO_DELAY_CONSTRAINT) / 100]
set clk_input [get_port clk]
set clk_index [lsearch [all_inputs] $clk_input]
set data_inputs [lreplace [all_inputs] $clk_index $clk_index ""]

set_input_delay  $io_delay -clock $clocks $data_inputs
set_output_delay $io_delay -clock $clocks [all_outputs]

set_max_fanout $::env(MAX_FANOUT_CONSTRAINT) [current_design]
