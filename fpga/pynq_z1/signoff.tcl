# Shared by standalone and PYNQ Linux builds after route_design.
proc aster_signoff {output_dir} {
    report_timing_summary -file [file join $output_dir timing_summary.rpt] -warn_on_violation
    report_route_status -file [file join $output_dir route_status.rpt]
    report_drc -file [file join $output_dir drc.rpt]
    report_methodology -file [file join $output_dir methodology.rpt]
    report_utilization -file [file join $output_dir utilization_routed.rpt]
    foreach delay_type {min max} {
        set worst [get_timing_paths -quiet -delay_type $delay_type -max_paths 1]
        if {[llength $worst] == 0} { error "No $delay_type timing paths found" }
        set slack [get_property SLACK $worst]
        if {$slack < 0} { error "Aster $delay_type timing failed: slack=$slack ns" }
        puts "ASTER_SIGNOFF $delay_type slack=$slack ns"
    }
    set drc_errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
    if {[llength $drc_errors]} { error "Aster DRC errors: $drc_errors" }
    # write_bitstream also runs mandatory routing/bitstream DRC and must
    # complete successfully. Reports above remain available on a failed run.
}
