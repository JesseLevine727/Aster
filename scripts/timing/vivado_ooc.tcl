# Out-of-context timing and area for one core block on the PYNQ-Z1 part (Phase 18).
#
#   vivado -mode batch -source scripts/timing/vivado_ooc.tcl \
#       -tclargs <out-dir> <top> <period-ns> <source>...
#
# Synthesizes <top> out of context against a <period-ns> clock on port clk,
# places and routes it, and writes timing_summary.rpt, utilization.rpt and a
# one-line summary.txt. Only register-to-register paths are timed: an
# out-of-context block has no board I/O, so its ports are left unconstrained.
#
# Named paths (docs/cpu.md §4 requires some in every milestone's report): the
# environment variable OOC_NAMED_PATHS, "label=from-pattern>to-pattern;...",
# reports for each the worst path from the registers matching the first pattern
# to those matching the second, as a "NAMED" line and in named_paths.rpt.
if {$argc < 4} { error "usage: vivado_ooc.tcl <out-dir> <top> <period-ns> <source>..." }
set out [file normalize [lindex $argv 0]]
set top [lindex $argv 1]
set period [lindex $argv 2]
set sources [lrange $argv 3 end]
set part xc7z020clg400-1
file mkdir $out

set xdc [file join $out clock.xdc]
set fp [open $xdc w]
puts $fp "create_clock -name clk -period $period \[get_ports clk\]"
close $fp

foreach source $sources {
    if {[string match *.sv $source]} { read_verilog -sv $source } else { read_verilog $source }
}
read_xdc -mode out_of_context $xdc
synth_design -top $top -part $part -mode out_of_context
opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 10 -file [file join $out timing_summary.rpt]
report_utilization -file [file join $out utilization.rpt]
set setup [get_timing_paths -setup -max_paths 1 -nworst 1]
set hold [get_timing_paths -hold -max_paths 1 -nworst 1]
set wns [get_property SLACK $setup]
set whs [get_property SLACK $hold]
# Site counts as in utilization.rpt (a LUT6_2 site holding two LUT cells counts once).
set util [report_utilization -return_string]
proc used {util site} {
    if {![regexp "\\|\\s*$site\\s*\\|\\s*(\\d+)\\s*\\|" $util -> count]} { error "no '$site' row in the utilization report" }
    return $count
}
set luts [used $util {Slice LUTs}]
set ffs [used $util {Slice Registers}]
set brams [used $util {Block RAM Tile}]
set dsps [used $util DSPs]
# The period this setup slack implies (a met 10 ns run is not pushed further, so
# this is an estimate, not a sweep).
set implied [format %.3f [expr {$period - $wns}]]
set fmax [format %.1f [expr {1000.0 / ($period - $wns)}]]
if {[info exists ::env(OOC_NAMED_PATHS)] && $::env(OOC_NAMED_PATHS) ne ""} {
    set named [open [file join $out named_paths.rpt] w]
    foreach item [split $::env(OOC_NAMED_PATHS) ";"] {
        lassign [split $item "="] label patterns
        lassign [split $patterns ">"] from to
        set sources [get_cells -hier -quiet -filter "NAME =~ $from && IS_SEQUENTIAL"]
        set sinks [get_cells -hier -quiet -filter "NAME =~ $to && IS_SEQUENTIAL"]
        if {![llength $sources] || ![llength $sinks]} { error "named path $label: no register matches $from or $to" }
        set path [get_timing_paths -setup -from $sources -to $sinks -max_paths 1 -nworst 1]
        set line "NAMED label=$label slack_ns=[get_property SLACK $path] from=[get_property STARTPOINT_PIN $path] to=[get_property ENDPOINT_PIN $path] levels=[get_property LOGIC_LEVELS $path]"
        puts $line
        puts $named $line
        puts $named [report_timing -of_objects $path -return_string]
    }
    close $named
}
set fp [open [file join $out summary.txt] w]
puts $fp "top=$top part=$part period_ns=$period wns_ns=$wns whs_ns=$whs implied_period_ns=$implied implied_fmax_mhz=$fmax luts=$luts ffs=$ffs brams=$brams dsps=$dsps"
close $fp
puts "SUMMARY top=$top period_ns=$period wns_ns=$wns whs_ns=$whs implied_period_ns=$implied implied_fmax_mhz=$fmax luts=$luts ffs=$ffs brams=$brams dsps=$dsps"
