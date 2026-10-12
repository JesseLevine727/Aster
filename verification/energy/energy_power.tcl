# Phase 21 (docs/phase21.md §3.4): each window's merged SAIF read into the routed final image, and its power
# reported. The checkpoint is opened once; for each SAIF:
#   - read_saif -strip_path tb (the testbench's top; the netlist sits at the design's own path), its unmatched
#     nets listed;
#   - report_power: the summary (with its confidence levels), by hierarchy, and by clock;
#   - the activity reset before the next.
# The conditions (§3.4.6): typical process, Vivado's defaults but the junction temperature, fixed by D6 when
# ENERGY_TJ is set.
#
#   vivado -mode batch -source energy_power.tcl -tclargs CHECKPOINT LIST OUT_DIR
# LIST: one line a SAIF, "<name> <path>"; OUT_DIR/<name>.{power,hier,clocks}.rpt and <name>.unmatched.txt.
lassign $argv checkpoint list out
open_checkpoint $checkpoint
if {[info exists ::env(ENERGY_TJ)]} {
    set_operating_conditions -junction_temp $::env(ENERGY_TJ)
    puts "ENERGY_TJ $::env(ENERGY_TJ)"
}
set f [open $list]
set rows [split [string trim [read $f]] "\n"]
close $f
foreach row $rows {
    lassign $row name saif
    set t [clock milliseconds]
    reset_switching_activity -all
    read_saif -strip_path tb -out_file [file join $out $name.unmatched.txt] $saif
    report_power -file [file join $out $name.power.rpt]
    report_power -hier all -hierarchical_depth 12 -file [file join $out $name.hier.rpt]
    puts "ENERGY_POWER $name [expr {[clock milliseconds] - $t}] ms"
}
