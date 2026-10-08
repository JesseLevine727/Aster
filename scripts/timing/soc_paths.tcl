# The Phase 20 SoC's routed checkpoint, analysed (milestone 20.2's margin
# work): the worst setup paths, one line each (slack, logic levels, logic and
# route delay, start and end cells), the endpoints with slack below <below> ns
# (0: the failing ones) grouped by their blocks, and the hierarchical
# utilization. Written to <out-dir>.
#
#   vivado -mode batch -source soc_paths.tcl -tclargs <routed.dcp> <out-dir> ?paths? ?below?
set dcp [lindex $argv 0]
set out [file normalize [lindex $argv 1]]
set count 200
set below 0
if {$argc >= 3} { set count [lindex $argv 2] }
if {$argc >= 4} { set below [lindex $argv 3] }
file mkdir $out
open_checkpoint $dcp
set fp [open [file join $out paths.txt] w]
puts $fp "slack levels logic_ns route_ns from -> to"
set paths [get_timing_paths -setup -max_paths $count -nworst 1 -unique_pins]
foreach p $paths {
    set from [get_property STARTPOINT_PIN $p]
    set to [get_property ENDPOINT_PIN $p]
    puts $fp [format "%7.3f %2d %6.3f %6.3f %s -> %s" [get_property SLACK $p] [get_property LOGIC_LEVELS $p] \
        [get_property DATAPATH_LOGIC_DELAY $p] [get_property DATAPATH_NET_DELAY $p] $from $to]
}
close $fp
# The endpoints below the threshold, by block (the first level below the SoC, each end).
set fp [open [file join $out blocks.txt] w]
array set worst {}
array set n {}
foreach p [get_timing_paths -setup -max_paths 20000 -nworst 1 -slack_lesser_than $below -unique_pins] {
    set to [get_property ENDPOINT_PIN $p]
    set from [get_property STARTPOINT_PIN $p]
    regsub {.*implementation/} $to {} to_short
    regsub {.*implementation/} $from {} from_short
    set key "[lindex [split $from_short /] 0] -> [lindex [split $to_short /] 0]"
    if {![info exists n($key)]} { set n($key) 0; set worst($key) $below }
    incr n($key)
    set s [get_property SLACK $p]
    if {$s < $worst($key)} { set worst($key) $s }
}
foreach key [lsort [array names n]] { puts $fp [format "%5d %7.3f %s" $n($key) $worst($key) $key] }
close $fp
report_utilization -hierarchical -hierarchical_depth 4 -file [file join $out utilization_hier.rpt]
report_timing -max_paths 10 -nworst 1 -delay_type max -path_type full -input_pins -file [file join $out worst10.rpt]
