# Phase 21 (docs/phase21.md §3.3): one run of tb_energy in xsim, a SAIF file for each counting interval.
# `counting` (the devices' counting flip-flop) is watched: a file opens when it rises and closes when it falls. Each
# file's duration is its interval's counted cycles (step 1 found it so, against the records: energy_saif.py checks
# every window's sum against its record's cycles). energy_saif.py groups the intervals into the records' windows and
# merges them.
#
#   xsim <snapshot> -tclbatch energy_run.tcl -testplusarg ... ; ENERGY_SAIF_DIR names the directory (or none:
#   the run without activity, for its speed and its reproduction alone)
set dir [expr {[info exists ::env(ENERGY_SAIF_DIR)] ? $::env(ENERGY_SAIF_DIR) : ""}]
set n 0
set start [clock milliseconds]
# every object of the cell, listed once (the recursive listing is slow: minutes) and logged into each file
if {$dir ne ""} {
    set t [clock milliseconds]
    set objects [get_objects -r /tb/aster_soc_board_i/aster/inst/*]
    puts "ENERGY_OBJECTS [llength $objects] in [expr {[clock milliseconds] - $t}] ms"
}
while {1} {
    add_condition -name up -notrace {/tb/counting == 1 || /tb/done == 1} {stop}
    run all
    remove_conditions up
    if {[get_value /tb/done] == 1} break
    set from [current_time]
    if {$dir ne ""} {
        set t [clock milliseconds]
        open_saif [file join $dir [format "interval%04d.saif" $n]]
        log_saif $objects
        set logged [expr {[clock milliseconds] - $t}]
    }
    set t [clock milliseconds]
    add_condition -name down -notrace {/tb/counting == 0 || /tb/done == 1} {stop}
    run all
    remove_conditions down
    set ran [expr {[clock milliseconds] - $t}]
    set t [clock milliseconds]
    if {$dir ne ""} { close_saif }
    puts "ENERGY_INTERVAL $n from=$from to=[current_time] log_ms=[expr {$dir ne "" ? $logged : 0}] run_ms=$ran close_ms=[expr {[clock milliseconds] - $t}]"
    incr n
    if {[get_value /tb/done] == 1} break
}
run all
puts "ENERGY_DONE intervals=$n wall_ms=[expr {[clock milliseconds] - $start}] time=[current_time]"
quit
