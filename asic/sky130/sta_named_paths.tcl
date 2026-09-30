# docs/cpu.md §4: every milestone's timing report names two paths of the Aster
# core — the memory's answer through the stall logic to the next data request
# (d_rsp_valid -> d_req_valid) and the d_rsp_error kill (d_rsp_error ->
# d_req_valid). In a core-alone block both run from an input port to an output
# port, within the SDC's input and output delays. LibreLane sources this file
# in each signoff STA corner (STA_EXTRA_CORNER_TCL_FILE); the reports go to the
# corner's sta.log between NAMED-PATH markers, which
# scripts/timing/sky130_summary.py reads.
foreach from {d_rsp_valid d_rsp_error} {
    if {[llength [get_ports -quiet $from]] == 0 || [llength [get_ports -quiet d_req_valid]] == 0} { continue }
    puts "NAMED-PATH-BEGIN ${from}_to_d_req_valid"
    report_checks -path_delay max -from [get_ports $from] -to [get_ports d_req_valid] -digits 3
    puts "NAMED-PATH-END"
}
