# open_bd_gui.tcl — open the project and block design for a screenshot.
# Launch:
#   vivado -mode gui -source tools/open_bd_gui.tcl
# Then use the "Fit Selection" button and Win+Shift+S to capture.

set root [file normalize [file join [file dirname [info script]] ..]]
set proj [file join $root vivado ov5640_pynq proj ov5640_m1a.xpr]

if {![file exists $proj]} {
    puts "ERROR: project not found: $proj"
    return
}
open_project $proj
puts "INFO: project opened: $proj"

set bd [get_files -quiet ov5640_m1a.bd]
if {[llength $bd] == 0} {
    puts "ERROR: ov5640_m1a.bd not found in the project"
} else {
    open_bd_design $bd
    puts "INFO: block design opened. Use Fit Selection, then Win+Shift+S to capture."
}
