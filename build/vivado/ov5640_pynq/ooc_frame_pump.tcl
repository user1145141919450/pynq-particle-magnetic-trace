# ooc_frame_pump.tcl —— frame_pump 的 OOC 综合（看资源/时序）
set script_dir [file dirname [file normalize [info script]]]
cd $script_dir
set_part xc7z020clg400-1
read_verilog rtl/v3_bundle/frame_pump.v
synth_design -top frame_pump -part xc7z020clg400-1 -mode out_of_context
create_clock -period 10.000 -name clk [get_ports clk]
report_utilization -file frame_pump_util.rpt
report_timing_summary -file frame_pump_timing.rpt
puts "=== FRAME_PUMP OOC DONE ==="
exit
