# inspect_xclk.tcl — 查实现后设计里 ov5640_xclk 端口到底被谁驱动
set dcp [file join [file dirname [file normalize [info script]]] proj ov5640_m1a.runs impl_1 ov5640_m1a_wrapper_routed.dcp]
open_checkpoint $dcp

puts "======== 1) ov5640_xclk 端口的网络与驱动器 ========"
set p [get_ports ov5640_xclk]
set n [get_nets -of_objects $p]
puts "PORT = $p"
puts "NET  = $n"
foreach pin [get_pins -quiet -of_objects $n] {
    set c [get_cells -quiet -of_objects $pin]
    puts "   PIN $pin  DIR=[get_property DIRECTION $pin]  CELL=[get_property REF_NAME $c]  CELLNAME=[get_property NAME $c]"
}

puts ""
puts "======== 2) dvp_ctrl 相关单元 ========"
set dc [get_cells -quiet -hier -filter {NAME =~ *dvp_ctrl*}]
puts "dvp_ctrl 实例数 = [llength $dc]"
foreach c [lsearch -all -inline -glob [get_cells -quiet -hier] *dvp_ctrl*] { puts "   $c" }

puts ""
puts "======== 3) 全时钟频率 ========"
foreach c [get_clocks -quiet] {
    puts "   $c  period=[get_property PERIOD $c] ns  = [expr {1000.0/[get_property PERIOD $c]}] MHz"
}

puts ""
puts "======== 4) clk_wiz 输出接了什么 ========"
foreach pin [get_pins -quiet clk_wiz_0/inst/*] {
    set nm [get_property NAME $pin]
    if {[string match "*clk_out*" $nm]} {
        set nn [get_nets -quiet -of_objects $pin]
        puts "   $nm -> [get_property NAME $nn]"
    }
}
