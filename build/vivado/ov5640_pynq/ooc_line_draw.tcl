# ooc_line_draw.tcl —— line_draw 的 OOC 综合（只出资源/时序，不碰工程）
set script_dir [file dirname [file normalize [info script]]]
cd $script_dir
set_part xc7z020clg400-1
read_verilog rtl/v3_bundle/line_draw.v
synth_design -top line_draw -part xc7z020clg400-1 -mode out_of_context
create_clock -period 10.000 -name clk [get_ports clk]
report_utilization -file line_draw_util.rpt
report_timing_summary -file line_draw_timing.rpt
puts "=== LINE_DRAW OOC DONE ==="
exit
