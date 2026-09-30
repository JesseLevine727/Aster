# Phase 18 experiment: constraints.core.sdc with 3 ns of extra setup clock
# uncertainty, for place-and-route only; signoff STA keeps constraints.core.sdc
# at the real 10 ns. The resizer's pre-extraction wire estimates proved
# optimistic (slow-corner slack met before extraction, about -2 to -5 ns at
# signoff), so place-and-route is asked to aim tighter. Otherwise identical to
# constraints.core.sdc (self-contained: read_sdc does not follow `source`).
create_clock -name clk -period $::env(CLOCK_PERIOD) [get_ports clk]
set clocks [get_clocks clk]
set clk_input [get_port clk]
set clk_index [lsearch [all_inputs] $clk_input]
set data_inputs [lreplace [all_inputs] $clk_index $clk_index ""]

set io_delay [expr $::env(CLOCK_PERIOD) * $::env(IO_DELAY_CONSTRAINT) / 100]
set_input_delay  $io_delay -clock $clocks $data_inputs
set_output_delay $io_delay -clock $clocks [all_outputs]

set_max_fanout $::env(MAX_FANOUT_CONSTRAINT) [current_design]
set_max_transition $::env(MAX_TRANSITION_CONSTRAINT) [current_design]
if { [info exists ::env(MAX_CAPACITANCE_CONSTRAINT)] } {
    set_max_capacitance $::env(MAX_CAPACITANCE_CONSTRAINT) [current_design]
}

set driving_cell [split $::env(SYNTH_DRIVING_CELL) "/"]
set_driving_cell -lib_cell [lindex $driving_cell 0] -pin [lindex $driving_cell 1] $data_inputs
set_driving_cell -lib_cell [lindex $driving_cell 0] -pin [lindex $driving_cell 1] $clk_input
set_load [expr $::env(OUTPUT_CAP_LOAD) / 1000.0] [all_outputs]

# The margin applies to setup only; hold keeps the real uncertainty (without
# -setup, set_clock_uncertainty also tightens hold and the resizer inserts
# thousands of needless hold buffers).
set_clock_uncertainty -setup [expr $::env(CLOCK_UNCERTAINTY_CONSTRAINT) + 3.0] $clocks
set_clock_uncertainty -hold $::env(CLOCK_UNCERTAINTY_CONSTRAINT) $clocks
set_clock_transition $::env(CLOCK_TRANSITION_CONSTRAINT) $clocks
set_timing_derate -early [expr 1 - $::env(TIME_DERATING_CONSTRAINT) / 100.0]
set_timing_derate -late  [expr 1 + $::env(TIME_DERATING_CONSTRAINT) / 100.0]

if { [info exists ::env(OPENLANE_SDC_IDEAL_CLOCKS)] && $::env(OPENLANE_SDC_IDEAL_CLOCKS) } {
    unset_propagated_clock [all_clocks]
} else {
    set_propagated_clock [all_clocks]
}
